"""Chat layer: a conversation in, a stream of events out.

The frontend sends the whole conversation each turn (the server keeps no session), plus
the profile the current roadmap was built from and the roadmap itself. Each turn:
  1. agent.understand() reads the conversation (LLM, or rules as a fallback),
  2. if every required field is known and something changed since the last plan, the
     LangGraph planner runs and each finished node becomes a visible thinking step,
  3. otherwise the agent talks: a natural reply that answers, asks for what is missing
     or suggests roles, streamed token by token from the LLM.

Event stream (Server-Sent Events, one JSON object per event):
    step     {"label", "detail"}             a finished agent step
    text     {"delta"}                       a piece of the reply (markdown)
    roadmap  {"roadmap"}                     the validated roadmap (status ok only)
    done     {"status", "missing", "options", "title", "profile", ...}
    error    {"message"}
status is "ok" (new roadmap), "needs_input" (asked for something), "chat" (answered or
talked, nothing missing) or "error".
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Iterator
from typing import Any, Literal

from pydantic import BaseModel, Field

from careercompass import agent, custom_planner, explain, schemas
from careercompass.agent import role_name
from careercompass.llm import LLM
from careercompass.planner import load_role_profile

STREAM_DELAY_S = 0.018  # pause between template reply chunks; 0 in tests


class ChatTurn(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=2000)


class ChatRequest(BaseModel):
    messages: list[ChatTurn] = Field(min_length=1, max_length=40)
    pending: list[str] = Field(
        default_factory=list,
        description="Fields the assistant asked for last turn (from the previous 'done' event)",
    )
    last_plan: dict[str, Any] | None = Field(
        default=None, description="'profile' from the done event of the turn that built the roadmap"
    )
    roadmap: dict[str, Any] | None = Field(
        default=None, description="The roadmap currently shown, so questions can be answered"
    )


# ---------------- thinking steps for planner nodes ----------------


def understood_detail(u: agent.Understanding) -> str:
    p = u.profile
    bits = []
    if p.name:
        bits.append(p.name)
    if p.branch or p.year:
        bits.append(" ".join(x for x in [p.branch or "", f"year {p.year}" if p.year else ""] if x))
    if u.role_key or p.target_role:
        bits.append(role_name(u.role_key) if u.role_key else str(p.target_role))
    elif p.interest:
        bits.append(f"interested in {p.interest}")
    if p.hours_per_week:
        bits.append(f"{p.hours_per_week:g} h/week")
    if p.max_weeks:
        bits.append(f"within {p.max_weeks} weeks")
    if p.tech_mastery:
        bits.append("knows " + ", ".join(list(p.tech_mastery)[:5]))
    return " · ".join(bits) or "no details about you yet"


def steps_for(node: str, update: dict[str, Any], state: dict[str, Any]) -> list[dict[str, str]]:
    if node == "planner":
        plan = update.get("plan", {})
        n_items = sum(len(p.get("items", [])) for p in plan.get("phases", []))
        if state.get("replans"):
            return [{"label": "Re-planned at your pace", "detail": f"{n_items} items"}]
        socs = plan.get("socs") or {}
        n_skip = len(plan.get("skipped_known", []))
        try:
            titles = {
                s["soc"]: s.get("title", s["soc"]) for s in load_role_profile(plan["role"])["socs"]
            }
        except (KeyError, TypeError):
            titles = {}
        return [
            {
                "label": "Matched O*NET occupations",
                "detail": ", ".join(
                    f"{titles.get(s, s)} {a:.0%}" for s, a in list(socs.items())[:3]
                ),
            },
            {
                "label": "Picked courses and projects",
                "detail": f"{n_items} items"
                + (f", {n_skip} skipped because you know them" if n_skip else ""),
            },
        ]
    if node == "validate":
        probs = update.get("problems") or []
        return [
            {
                "label": "Checked prerequisites and order",
                "detail": "all good" if not probs else f"{len(probs)} problem(s) found",
            }
        ]
    if node == "adjust":
        return [{"label": "Adjusted for your deadline", "detail": update["adjustments"][-1]}]
    if node == "explain":
        f = update["roadmap"]["feasibility"]
        return [
            {
                "label": "Checked it fits your time",
                "detail": f"{f['total_hours']:g} h at {f['hours_per_week']:g} h/week = "
                f"{f['total_weeks']} weeks",
            }
        ]
    return []


# ---------------- replies after planning ----------------


def _join(xs: list[str]) -> str:
    return xs[0] if len(xs) == 1 else ", ".join(xs[:-1]) + " and " + xs[-1]


def reply_ok_intro(r: dict[str, Any], updated: bool = False) -> str:
    f = r["feasibility"]
    start = r["baseline_readiness"]["core"]["overall"]
    end = r["final_readiness"]["core"]["overall"]
    lead = "Done, I've updated your roadmap." if updated else "Your roadmap is ready."
    return (
        f"{lead} At **{f['hours_per_week']:g} hours a week** it takes "
        f"**{f['total_weeks']} weeks** and moves your job readiness from "
        f"**{start:.0%} to {end:.0%}**."
    )


def reply_ok_notes(r: dict[str, Any], state: dict[str, Any]) -> str:
    n_proj = sum(1 for n in r["nodes"] if n["kind"] == "project")
    lines = ["A few things to know:", ""]
    lines.append(
        f"- Each phase ends with a **project you can show in interviews**. "
        f"There {'is' if n_proj == 1 else 'are'} {n_proj} in total."
    )
    known = [n["label"] for n in r.get("nodes", []) if n["kind"] == "known"]
    if known:
        them = "it" if len(known) == 1 else "them"
        lines.append(f"- I skipped {_join(known)} since you already know {them}.")
    gaps = [g["tech"] for g in r.get("top_tech_gaps", [])[:3]]
    if gaps:
        lines.append(
            f"- Your biggest tool gaps right now are **{_join(gaps)}**. The plan covers them."
        )
    if r.get("dropped_for_time"):
        n = len(r["dropped_for_time"])
        lines.append(f"- To fit your time I left out {n} optional item{'s' if n > 1 else ''}.")
    for note in state.get("adjustments", []):
        lines.append(f"- {note}")
    lines.append("")
    if state.get("questions"):
        lines.append(state["questions"][-1])
    else:
        lines.append(
            "Tick off items in the roadmap as you finish them and I'll track your progress."
        )
    return "\n".join(lines)


def chunks(text: str) -> Iterator[str]:
    """Split text into small pieces for a typing effect, keeping whitespace."""
    yield from re.findall(r"\S+\s*|\s+", text)


def roadmap_facts(raw: dict[str, Any] | None) -> dict[str, Any] | None:
    if not raw:
        return None
    try:
        return explain.facts(schemas.Roadmap.model_validate(raw))
    except ValueError:
        return None


def same_plan(old: dict[str, Any] | None, new: dict[str, Any]) -> bool:
    """True when a re-plan produced the same items on the same schedule."""
    if not old:
        return False

    def sig(r: dict[str, Any]) -> list[Any]:
        return [(n["id"], n.get("start_week"), n.get("end_week")) for n in r.get("nodes", [])]

    return sig(old) == sig(new) and old.get("feasibility") == new.get("feasibility")


# ---------------- the event stream ----------------


def sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def run_events(
    graph: Any,
    req: ChatRequest,
    llm: LLM | None = None,
    delay: float | None = None,
    con: Any = None,
) -> Iterator[str]:
    """Yield SSE strings for one chat turn. Never raises; errors become an 'error' event."""
    pause = STREAM_DELAY_S if delay is None else delay

    def text(t: str) -> Iterator[str]:
        for piece in chunks(t):
            yield sse("text", {"delta": piece})
            if pause:
                time.sleep(pause)

    messages = [m.model_dump() for m in req.messages]
    if not any(m["role"] == "user" for m in messages):
        yield sse("error", {"message": "conversation has no user message"})
        return

    u = agent.understand(messages, llm, req.pending)
    yield sse("step", {"label": "Read your message", "detail": understood_detail(u)})
    role = u.role_key or u.custom_role
    title = f"{role_name(role)} roadmap" if role else "New chat"
    profile = agent.plan_input(u)

    if not agent.should_plan(u, req.last_plan):
        if u.missing:
            yield sse(
                "step",
                {
                    "label": "Checked what's missing",
                    "detail": ", ".join(f.replace("_", " ") for f in u.missing),
                },
            )
        facts = roadmap_facts(req.roadmap)
        try:
            for piece in agent.talk(messages, u, llm, facts):
                for small in chunks(piece) if llm is None else [piece]:
                    yield sse("text", {"delta": small})
                    if llm is None and pause:
                        time.sleep(pause)
        except Exception as exc:  # never leak a stack trace
            yield sse("error", {"message": f"reply failed: {type(exc).__name__}"})
            return
        status = "needs_input" if u.missing else "chat"
        yield sse(
            "done",
            {
                "status": status,
                "missing": u.missing,
                "options": agent.options_for(u),
                "title": title,
                "profile": None,
                "intake_source": u.source,
                "degraded": llm is not None and u.source == "rules",
            },
        )
        return

    if u.custom_role and llm is not None:
        yield from custom_turn(profile, req, llm, con, title, u.source, text)
        return

    state: dict[str, Any] = {"profile": profile, "intake_source": u.source}
    try:
        for upd in graph.stream(
            {"text": "", "profile": profile, "intake_source": u.source}, stream_mode="updates"
        ):
            for node, delta in upd.items():
                delta = delta or {}
                state.update(delta)
                for st in steps_for(node, delta, state):
                    yield sse("step", st)
    except Exception as exc:  # unexpected failure: never leak a stack trace
        yield sse("error", {"message": f"planning failed: {type(exc).__name__}"})
        return

    status = state.get("status", "error")
    if status == "ok" and same_plan(req.roadmap, state["roadmap"]):
        f = state["roadmap"]["feasibility"]
        deadline = profile.get("max_weeks")
        lead = (
            f"Good news: your current plan already fits. It takes **{f['total_weeks']} weeks**"
            + (f", within your {deadline}-week deadline." if deadline else ".")
        )
        yield from text(lead + " Nothing in your roadmap needed to change.")
    elif status == "ok":
        r = state["roadmap"]
        yield from text(reply_ok_intro(r, updated=req.roadmap is not None))
        yield sse("roadmap", {"roadmap": r})
        yield from text("\n\n" + reply_ok_notes(r, state))
    else:
        yield from text(
            "Sorry, I couldn't build a valid plan from that. Could you tell me again what you "
            "study, which year you're in, your weekly hours and the role you want?"
        )
    yield sse(
        "done",
        {
            "status": status,
            "missing": [],
            "options": [],
            "title": title,
            "profile": profile if status == "ok" else None,
            "intake_source": u.source,
            "explanation_source": state.get("explanation_source"),
            "trace": state.get("trace", []),
        },
    )


def custom_turn(
    profile: dict[str, Any],
    req: ChatRequest,
    llm: LLM,
    con: Any,
    title: str,
    source: str,
    text: Any,
) -> Iterator[str]:
    """Plan a role outside the curated catalog (O*NET + LLM draft + verified links)."""
    roadmap: dict[str, Any] | None = None
    error = None
    try:
        for kind, data in custom_planner.plan_events(profile, llm, con):
            if kind == "step":
                yield sse("step", data)
            elif kind == "roadmap":
                roadmap = data
            else:
                error = data
    except Exception as exc:  # never leak a stack trace
        error = f"planning failed: {type(exc).__name__}"
    if roadmap is None:
        yield from text(
            "Sorry, I couldn't build a plan for that role right now"
            + (f" ({error})" if error else "")
            + ". Try again in a minute, or pick a role like DevOps Engineer or Data Analyst."
        )
        done = {"status": "error", "missing": [], "options": [], "title": title, "profile": None}
        yield sse("done", done)
        return
    if same_plan(req.roadmap, roadmap):
        yield from text(
            "Good news: your current plan already fits. Nothing in your roadmap needed to change."
        )
    else:
        yield from text(reply_ok_intro(roadmap, updated=req.roadmap is not None))
        yield sse("roadmap", {"roadmap": roadmap})
        occ = ", ".join(s["title"] for s in roadmap["soc_alignment"])
        note = (
            f"\n\nThis plan is built from the O*NET requirements for **{occ}**, and every "
            "course link was opened and checked before showing it to you.\n\n"
        )
        yield from text(note + reply_ok_notes(roadmap, {"questions": []}))
    yield sse(
        "done",
        {
            "status": "ok",
            "missing": [],
            "options": [],
            "title": title,
            "profile": profile,
            "intake_source": source,
        },
    )
