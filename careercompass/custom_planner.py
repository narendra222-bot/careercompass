"""Roadmaps for roles outside the curated catalog.

    role text ──► O*NET search (hybrid retrieval) ──► LLM picks the best occupation(s)
              ──► requirements from O*NET: tools (in-demand/hot first), skills, knowledge
              ──► LLM drafts phases of courses + a project, using only those names
              ──► we validate, schedule by the student's hours and score readiness
              ──► live web research finds a resource per course; every link is verified
              ──► explanation ──► schemas.Roadmap (strict)

The LLM never decides numbers we show as facts: weeks, hours totals, readiness and
feasibility are computed here. Readiness is an estimate from O*NET coverage: each tool
and skill in the target set is "covered" by the courses and projects that list it.
"""

from __future__ import annotations

import json
import math
import re
import time
from collections.abc import Iterator
from typing import Any

from careercompass import explain, research, schemas
from careercompass.llm import LLM, LLMError, parse_json

TARGET_READINESS = 0.8
COURSE_LEVEL, PROJECT_LEVEL = 0.7, 0.9  # mastery after finishing a course / project
MAX_TOOLS, MAX_SKILLS, MAX_KNOWLEDGE = 12, 10, 6

Event = tuple[str, Any]  # ("step", {...}) | ("roadmap", dict) | ("error", str)


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:48] or "item"


def display_role(role: str) -> str:
    """'ui ux designer' -> 'UI UX Designer'; keeps the student's own capitals."""
    if any(any(c.isupper() for c in w[1:]) for w in role.split()):
        return role.strip()  # has an acronym the student typed, e.g. "SOC Analyst"
    role = role.lower()
    small = {"ai", "ml", "ui", "ux", "qa", "it", "seo", "hr", "sre", "iot", "ar", "vr", "bi"}
    return " ".join(w.upper() if w in small else w.capitalize() for w in role.split())


# ---------------- 1. which O*NET occupation? ----------------

PICK_SYSTEM = """You map a job role a student wants to the closest O*NET occupations.
Given the role and candidate occupations, return ONLY JSON:
{"picks": [{"soc": "15-1212.00", "weight": 0.7}]}
Pick 1 or 2 candidates that best describe the actual day-to-day work of the role (weights sum
to 1). Use only SOC codes from the candidates."""


def resolve_role(role: str, llm: LLM, search: Any = None) -> list[dict[str, Any]]:
    """[{soc, title, alpha}] for the role, alphas summing to 1."""
    if search is None:
        from careercompass import retrieval

        search = retrieval.search
    cands = search(role, k=8)
    if not cands:
        return []
    if cands[0].get("source") == "curated_profile":
        picks = [(c["soc"], c["title"], float(c.get("score") or 0)) for c in cands[:3]]
    else:
        listing = [{"soc": c["soc"], "title": c["title"]} for c in cands]
        picks = []
        try:
            raw = parse_json(
                llm.chat(
                    PICK_SYSTEM, json.dumps({"role": role, "candidates": listing}), json_mode=True
                )
            )
            titles = {c["soc"]: c["title"] for c in cands}
            for p in raw.get("picks") or []:
                soc, w = str(p.get("soc")), float(p.get("weight") or 0)
                if soc in titles and w > 0 and soc not in [x[0] for x in picks]:
                    picks.append((soc, titles[soc], w))
        except (LLMError, ValueError, TypeError):
            picks = []
        picks = picks[:2] or [(cands[0]["soc"], cands[0]["title"], 1.0)]
    total = sum(w for _, _, w in picks) or 1.0
    out = [{"soc": s, "title": t, "alpha": round(w / total, 4)} for s, t, w in picks]
    out[0]["alpha"] = round(1 - sum(x["alpha"] for x in out[1:]), 4)  # exact sum of 1
    return out


# ---------------- 2. what does the job need? ----------------


def requirements(con: Any, socs: list[dict[str, Any]]) -> dict[str, Any]:
    from careercompass import db

    tools: dict[str, float] = {}
    skills: dict[str, float] = {}
    knowledge: dict[str, float] = {}
    descs = []
    for s in socs:
        a = s["alpha"]
        occ = db.get_occupation(con, s["soc"]) or {}
        if occ.get("description"):
            descs.append(f"{s['title']}: {occ['description']}")
        for t in db.get_tech(con, s["soc"]):
            score = a * (2.0 * bool(t["in_demand"]) + 1.0 * bool(t["hot"]) + 0.2)
            tools[t["example"]] = tools.get(t["example"], 0) + score
        for r in db.get_requirements(con, s["soc"]):
            if r.get("suppress"):
                continue
            score = a * float(r["imp"]) * float(r["lvl"])
            bucket = (
                skills
                if r["domain"] == "skill"
                else knowledge
                if r["domain"] == "knowledge"
                else None
            )
            if bucket is not None:
                bucket[r["element_name"]] = bucket.get(r["element_name"], 0) + score

    def top(d: dict[str, float], n: int) -> list[str]:
        return [k for k, _ in sorted(d.items(), key=lambda kv: (-kv[1], kv[0]))[:n]]

    return {
        "tools": top(tools, 40),
        "skills": top(skills, MAX_SKILLS),
        "knowledge": top(knowledge, MAX_KNOWLEDGE),
        "description": " ".join(descs)[:900],
    }


# ---------------- 3. the LLM draft ----------------

DRAFT_SYSTEM = """You design practical, job-focused learning roadmaps for Indian college \
students. Use the O*NET requirements you are given. Return ONLY a JSON object:
{
 "tools": [up to 12 names copied EXACTLY from candidate_tools: the ones an entry-level hire
           most needs],
 "phases": [
   {"title": "short name of what this phase builds",
    "items": [
      {"key": "short-id", "kind": "course", "title": "specific course topic",
       "hours": 6-40, "covers": [names from tools, key_skills or key_knowledge],
       "after": [keys of EARLIER items this needs]},
      ... 2 to 5 courses, then exactly ONE project last:
      {"key": "...", "kind": "project", "title": "portfolio project", "hours": 15-40,
       "covers": [...], "after": [...], "deliverables": ["2 to 4 concrete outputs"]}
    ]}
 ]
}
Rules: 3 to 5 phases, foundations first. Course titles are specific topics, e.g. "Network \
security fundamentals (TCP/IP, firewalls, VPNs)", never a brand-name course. Skip topics the \
student already knows well (level 0.8). Keep the total hours within budget_hours. No URLs."""


def draft(
    llm: LLM, role: str, req: dict[str, Any], profile: dict[str, Any], budget: tuple[int, int]
) -> dict[str, Any]:
    payload = {
        "role": role,
        "occupation": req["description"],
        "student": {
            "branch": profile.get("branch"),
            "year": profile.get("year"),
            "hours_per_week": profile.get("hours_per_week"),
            "deadline_weeks": profile.get("max_weeks"),
            "knows": profile.get("tech_mastery") or {},
        },
        "candidate_tools": req["tools"],
        "key_skills": req["skills"],
        "key_knowledge": req["knowledge"],
        "budget_hours": {"min": budget[0], "max": budget[1]},
    }
    last: Exception | None = None
    for _ in range(2):
        try:
            raw = parse_json(
                llm.chat(DRAFT_SYSTEM, json.dumps(payload), smart=True, json_mode=True)
            )
            return clean_draft(raw, req)
        except (LLMError, ValueError) as exc:
            last = exc
            payload["note"] = f"Your last answer was invalid ({exc}). Follow the format exactly."
    raise ValueError(f"could not draft a roadmap: {last}")


def clean_draft(raw: dict[str, Any], req: dict[str, Any]) -> dict[str, Any]:
    """Keep only well-formed items, names from the O*NET lists and backward dependencies."""
    allowed_tools = {t.lower(): t for t in req["tools"]}
    tools = []
    for t in raw.get("tools") or []:
        if (
            isinstance(t, str)
            and t.lower() in allowed_tools
            and allowed_tools[t.lower()] not in tools
        ):
            tools.append(allowed_tools[t.lower()])
    tools = tools[:MAX_TOOLS] or req["tools"][:8]
    names = {n.lower(): n for n in [*tools, *req["skills"], *req["knowledge"]]}

    phases = []
    seen_keys: dict[str, str] = {}
    used_ids: set[str] = set()
    for ph in (raw.get("phases") or [])[:6]:
        items = []
        for it in (ph.get("items") or [])[:7] if isinstance(ph, dict) else []:
            if not isinstance(it, dict) or not str(it.get("title") or "").strip():
                continue
            kind = "project" if it.get("kind") == "project" else "course"
            title = str(it["title"]).strip()[:110]
            try:
                hours = float(it.get("hours") or 0)
            except (TypeError, ValueError):
                hours = 0
            lo, hi = (15, 40) if kind == "project" else (6, 40)
            hours = float(min(hi, max(lo, round(hours or (25 if kind == "project" else 15)))))
            base = slug(title)
            nid = base
            n = 2
            while nid in used_ids:
                nid, n = f"{base}-{n}", n + 1
            used_ids.add(nid)
            covers = []
            for c in it.get("covers") or []:
                if isinstance(c, str) and c.lower() in names and names[c.lower()] not in covers:
                    covers.append(names[c.lower()])
            after = [
                seen_keys[a] for a in it.get("after") or [] if isinstance(a, str) and a in seen_keys
            ]
            deliverables = [
                str(d).strip()[:200] for d in it.get("deliverables") or [] if str(d).strip()
            ]
            items.append(
                {
                    "id": nid,
                    "kind": kind,
                    "title": title,
                    "hours": hours,
                    "covers": covers,
                    "after": sorted(set(after)),
                    "deliverables": deliverables[:4] if kind == "project" else [],
                }
            )
            if isinstance(it.get("key"), str):
                seen_keys[it["key"]] = nid
        if not items:
            continue
        # the project closes the phase
        items.sort(key=lambda x: x["kind"] == "project")
        projects = [x for x in items if x["kind"] == "project"]
        title = str(ph.get("title") or "").strip()[:100]
        phases.append(
            {"title": projects[-1]["title"] if projects else title or "Electives", "items": items}
        )
    if len(phases) < 2 or sum(len(p["items"]) for p in phases) < 4:
        raise ValueError("draft has too few phases or items")
    return {"tools": tools, "phases": phases}


# ---------------- 4. schedule, readiness, roadmap ----------------


def _known_level(tool: str, mastery: dict[str, float]) -> float:
    t = tool.lower()
    best = 0.0
    for name, m in mastery.items():
        n = name.lower()
        if n and (n in t or t in n or n.split()[0] == t.split()[0]):
            best = max(best, m)
    return best


class _Scorer:
    def __init__(self, tools: list[str], req: dict[str, Any], profile: dict[str, Any]) -> None:
        year = int(profile.get("year") or 1)
        self.base = 0.1 + 0.05 * min(year, 4)
        mastery = profile.get("tech_mastery") or {}
        self.tech = {t: _known_level(t, mastery) for t in tools}
        self.skill = {s: self.base for s in req["skills"]}
        self.know = {k: self.base for k in req["knowledge"]}

    def complete(self, item: dict[str, Any]) -> None:
        level = PROJECT_LEVEL if item["kind"] == "project" else COURSE_LEVEL
        for c in item["covers"]:
            for d in (self.tech, self.skill, self.know):
                if c in d:
                    d[c] = max(d[c], level)

    def pair(self) -> dict[str, Any]:
        def avg(d: dict[str, float]) -> float:
            return round(sum(d.values()) / len(d), 4) if d else 0.0

        tech, sk, kn = avg(self.tech), avg(self.skill), avg(self.know)
        skills = round(
            (sk * len(self.skill) + kn * len(self.know)) / max(1, len(self.skill) + len(self.know)),
            4,
        )
        return {
            "core": {
                "overall": round(0.55 * tech + 0.45 * skills, 4),
                "tech": tech,
                "skills": skills,
            },
            "onet": {
                "overall": round((sk + kn + self.base + tech) / 4, 4),
                "skill": sk,
                "knowledge": kn,
                "ability": round(self.base, 4),
                "technology": tech,
            },
        }


def build(
    role: str,
    socs: list[dict[str, Any]],
    req: dict[str, Any],
    d: dict[str, Any],
    profile: dict[str, Any],
    provider: str,
) -> dict[str, Any]:
    hpw = float(profile["hours_per_week"])
    scorer = _Scorer(d["tools"], req, profile)
    baseline = scorer.pair()
    mastery0 = dict(scorer.tech)

    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    phases: list[dict[str, Any]] = []
    goal_id = f"goal:{slug(role)}"
    cum = 0.0
    for n, ph in enumerate(d["phases"], 1):
        start = math.floor(cum / hpw) + 1
        for it in ph["items"]:
            s_week = math.floor(cum / hpw) + 1
            cum += it["hours"]
            e_week = max(s_week, math.ceil(cum / hpw))
            nodes.append(
                {
                    "id": it["id"],
                    "label": it["title"],
                    "kind": it["kind"],
                    "phase": n,
                    "start_week": s_week,
                    "end_week": e_week,
                    "hours": it["hours"],
                    "resources": [],
                    "deliverables": it["deliverables"],
                    "skills_gained": it["covers"],
                }
            )
            for a in it["after"]:
                edges.append(
                    {"id": f"{a}->{it['id']}", "source": a, "target": it["id"], "kind": "prereq"}
                )
            if it["kind"] == "project":
                edges.append(
                    {
                        "id": f"{it['id']}->{goal_id}",
                        "source": it["id"],
                        "target": goal_id,
                        "kind": "milestone",
                    }
                )
            scorer.complete(it)
        proj = next((x for x in ph["items"] if x["kind"] == "project"), None)
        phases.append(
            {
                "phase": n,
                "title": ph["title"],
                "milestone": proj["id"] if proj else None,
                "start_week": start,
                "end_week": max(start, math.ceil(cum / hpw)),
                "hours": sum(x["hours"] for x in ph["items"]),
                "node_ids": [x["id"] for x in ph["items"]],
                "readiness_after": scorer.pair(),
            }
        )
    known = [t for t, m in mastery0.items() if m >= 0.5]
    for t in known:
        nodes.append(
            {
                "id": f"known-{slug(t)}",
                "label": t,
                "kind": "known",
                "phase": None,
                "start_week": None,
                "end_week": None,
                "hours": 0.0,
                "resources": [],
                "deliverables": [],
                "skills_gained": [],
            }
        )
    nodes.append(
        {
            "id": goal_id,
            "label": role,
            "kind": "goal",
            "phase": None,
            "start_week": None,
            "end_week": None,
            "hours": 0.0,
            "resources": [],
            "deliverables": [],
            "skills_gained": [],
        }
    )
    total_weeks = max(1, math.ceil(cum / hpw))
    max_weeks = profile.get("max_weeks")
    feasible = max_weeks is None or total_weeks <= max_weeks
    gaps = sorted(((t, (1 - m) / len(mastery0)) for t, m in mastery0.items()), key=lambda x: -x[1])
    return {
        "student": {
            "name": profile.get("name") or "Student",
            "branch": profile["branch"],
            "year": int(profile["year"]),
            "hours_per_week": hpw,
            "target_role": role,
            "tech_mastery": profile.get("tech_mastery") or {},
            "max_weeks": max_weeks,
        },
        "target_role": role,
        "soc_alignment": [
            {"soc": s["soc"], "title": s["title"], "alpha": s["alpha"]} for s in socs
        ],
        "baseline_readiness": baseline,
        "final_readiness": phases[-1]["readiness_after"],
        "target_readiness": TARGET_READINESS,
        "phases": phases,
        "nodes": nodes,
        "edges": edges,
        "top_tech_gaps": [{"tech": t, "gap": round(g, 4)} for t, g in gaps[:10] if g > 0],
        "skipped_known": [f"known-{slug(t)}" for t in known],
        "dropped_for_time": [],
        "unmatched_tech": [],
        "feasibility": {
            "feasible": feasible,
            "total_hours": cum,
            "total_weeks": total_weeks,
            "hours_per_week": hpw,
            "max_weeks": max_weeks,
            "fix": None
            if feasible
            else {
                "needed_hours_per_week_for_max_weeks": math.ceil(cum / max_weeks),
                "needed_weeks_at_current_hours": total_weeks,
            },
        },
        "explanation": None,
        "provenance": {
            "generator": "llm-onet-planner",
            "data": "O*NET 31.0 Database (onetcenter.org), CC BY 4.0; plan drafted by "
            f"{provider} from O*NET requirements; links checked {time.strftime('%Y-%m-%d')}",
            "editorial_constants_human_reviewed": False,
        },
    }


# ---------------- the whole flow, as events ----------------


def plan_events(profile: dict[str, Any], llm: LLM, con: Any, search: Any = None) -> Iterator[Event]:
    role = display_role(str(profile["target_role"]))
    try:
        socs = resolve_role(role, llm, search)
    except Exception as exc:  # retrieval index missing or broken
        yield ("error", f"couldn't search O*NET: {type(exc).__name__}")
        return
    if not socs:
        yield ("error", f"no O*NET occupation matches {role}")
        return
    yield (
        "step",
        {
            "label": "Matched O*NET occupations",
            "detail": ", ".join(f"{s['title']} {s['alpha']:.0%}" for s in socs),
        },
    )
    req = requirements(con, socs)
    yield (
        "step",
        {
            "label": "Read the job's requirements",
            "detail": f"{len(req['tools'])} tools, {len(req['skills'])} skills, "
            f"{len(req['knowledge'])} knowledge areas",
        },
    )

    hpw = float(profile["hours_per_week"])
    if profile.get("max_weeks"):
        cap = int(hpw * profile["max_weeks"])
        budget = (min(120, cap), max(60, cap))
    else:
        budget = (180, 380)
    try:
        d = draft(llm, role, req, profile, budget)
    except ValueError as exc:
        yield ("error", str(exc))
        return
    n_items = sum(len(p["items"]) for p in d["phases"])
    yield (
        "step",
        {
            "label": "Drafted courses and projects",
            "detail": f"{len(d['phases'])} phases, {n_items} items, "
            f"aimed at {', '.join(d['tools'][:4])}",
        },
    )

    rm = build(role, socs, req, d, profile, getattr(llm, "provider", "llm"))
    courses = [n for n in rm["nodes"] if n["kind"] == "course"]
    yield (
        "step",
        {
            "label": "Researching resources",
            "detail": f"looking for one free resource for each of {len(courses)} courses",
        },
    )
    try:
        links = research.research([c["label"] for c in courses], role, llm)
    except Exception:  # links are a bonus; the plan stands without them
        links = {}
    for n in courses:
        if hit := links.get(n["label"]):
            n["resources"] = [hit]
    yield (
        "step",
        {
            "label": "Verified links",
            "detail": f"{len(links)} of {len(courses)} courses have a working link; "
            "the rest get a search link",
        },
    )

    try:
        roadmap = schemas.Roadmap.model_validate(rm)
    except ValueError as exc:
        yield ("error", f"generated roadmap failed validation: {str(exc)[:200]}")
        return
    text, _source = explain.explain(roadmap, llm)
    roadmap = roadmap.model_copy(update={"explanation": text})
    f = rm["feasibility"]
    yield (
        "step",
        {
            "label": "Checked it fits your time",
            "detail": f"{f['total_hours']:g} h at {f['hours_per_week']:g} h/week = "
            f"{f['total_weeks']} weeks",
        },
    )
    yield ("roadmap", roadmap.model_dump(mode="json"))
