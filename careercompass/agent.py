"""Conversational brain: understand the whole chat, decide what to do, talk naturally.

Each turn:
  1. understand()  - read the conversation into a Profile + intent. The LLM reads every
                     message (any phrasing, typos, Tenglish); its JSON is validated and
                     anything malformed or implausible is dropped. Without an LLM, the
                     regex rules read each student message, later ones winning.
  2. decide()      - plan when everything required is known and either there is no
                     roadmap yet or the student changed something; otherwise talk.
  3. talk          - the LLM writes a short, natural reply (streamed) from a CONTEXT it
                     is given: what is known, what to ask next, roles to suggest, and
                     the current roadmap's facts. Templates are the fallback.

Only facts we computed are given to the LLM as facts; it is told not to invent
courses, links or numbers.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError, field_validator

from careercompass import intake
from careercompass.llm import LLM, LLMError, parse_json

REQUIRED = ("branch", "year", "target_role", "hours_per_week")
Intent = Literal["greeting", "profile", "change", "question", "other"]

ROLE_NAMES = {
    "devops engineer": "DevOps Engineer",
    "data analyst": "Data Analyst",
    "machine learning engineer": "Machine Learning Engineer",
}


def display_role(role: str) -> str:
    from careercompass.custom_planner import display_role as show

    return show(role)


def role_name(role: Any) -> str:
    return ROLE_NAMES.get(str(role).lower(), display_role(str(role)))


class Profile(BaseModel):
    """What the student has told us. Every field is optional until they say it."""

    name: str | None = Field(default=None, max_length=40)
    branch: str | None = Field(default=None, max_length=40)
    year: int | None = Field(default=None, ge=1, le=6)
    hours_per_week: float | None = Field(default=None, gt=0, le=80)
    target_role: str | None = Field(default=None, max_length=60)
    interest: str | None = Field(default=None, max_length=60)
    tech_mastery: dict[str, float] = Field(default_factory=dict)
    max_weeks: int | None = Field(default=None, gt=0, le=260)

    @field_validator("tech_mastery")
    @classmethod
    def _clamp(cls, v: dict[str, float]) -> dict[str, float]:
        return {
            k.strip()[:40]: min(1.0, max(0.0, float(m)))
            for k, m in list(v.items())[:30]
            if k.strip()
        }

    @field_validator("name", "branch", "target_role", "interest", mode="before")
    @classmethod
    def _blank(cls, v: Any) -> Any:
        if isinstance(v, str):
            v = v.strip()
            return v or None
        return v


@dataclass
class Understanding:
    profile: Profile
    intent: Intent
    source: str  # "rules" or the LLM provider
    role_key: str | None = None  # curated role the target maps to
    custom_role: str | None = None  # any other role, planned from O*NET by the LLM
    missing: list[str] = field(default_factory=list)
    unsupported_role: str | None = None


# ---------------- understanding ----------------

EXTRACT_SYSTEM = """You read a chat between a college student and CareerCompass, a career-roadmap \
assistant, and extract what the STUDENT has said about themselves. Later messages override \
earlier ones. The student may write in English, or Telugu/Hindi in English letters, with typos.
Return ONLY a JSON object with these keys:
  intent: the student's LAST message is one of
    "greeting" (hello or small talk only), "profile" (sharing facts about themselves or their goal),
    "change" (asking to change an existing plan: time, deadline, skills, role),
    "question" (asking something, e.g. what is DevOps, explain phase 2), "other"
  name: first name if they said it, else null
  branch: degree and branch as a short label, e.g. "CSE", "AI&ML", "B.Com", "ECE", else null
  year: year of study 1-6 (final year = 4, graduated = 5), else null
  hours_per_week: weekly study hours (per day x7, weekdays only x5, weekends only x2), else null
  target_role: the job role they want as a short role name, e.g. "machine learning engineer",
    or null if they did not name one. Never put their branch, a skill or a sentence here.
  interest: an area they like when no clear role is given, e.g. "AI", "cloud", else null
  tech_mastery: object mapping tools/technologies they say they know to 0.3 (beginner),
    0.5 (comfortable) or 0.8 (strong)
  max_weeks: deadline in weeks if they gave one (months x4), else null
Only the Student's own words count; ignore tools the Assistant mentioned. Never guess values \
that were not said. Use null for unknown."""


def transcript(messages: list[dict[str, str]], limit: int = 14) -> str:
    lines = []
    for m in messages[-limit:]:
        who = "Student" if m["role"] == "user" else "Assistant"
        text = m["content"] if m["role"] == "user" else m["content"][:400]
        lines.append(f"{who}: {text}")
    return "\n".join(lines)


def _llm_profile(messages: list[dict[str, str]], llm: LLM) -> tuple[Profile, Intent] | None:
    try:
        raw = parse_json(llm.chat(EXTRACT_SYSTEM, transcript(messages), json_mode=True))
    except (LLMError, ValueError):
        return None
    intent = raw.pop("intent", "other")
    if intent not in ("greeting", "profile", "change", "question", "other"):
        intent = "other"
    tm = raw.get("tech_mastery")
    raw["tech_mastery"] = (
        {str(k): float(v) for k, v in tm.items() if isinstance(v, int | float)}
        if isinstance(tm, dict)
        else {}
    )
    data: dict[str, Any] = {}
    for k in Profile.model_fields:
        if k in raw and raw[k] not in (None, ""):
            # validate field by field so one bad value doesn't discard the rest
            try:
                Profile.model_validate({k: raw[k]})
                data[k] = raw[k]
            except ValidationError:
                pass
    # a skill counts only if the student actually wrote it (the assistant's own replies
    # mention tools too, and a model sometimes copies those)
    said = " ".join(m["content"] for m in messages if m["role"] == "user").lower()
    data["tech_mastery"] = {
        k: v for k, v in data.get("tech_mastery", {}).items() if _mentioned(k, said)
    }
    return Profile.model_validate(data), intent  # type: ignore[return-value]


def _mentioned(tool: str, text: str) -> bool:
    t = tool.lower().strip()
    variants = {t, t.replace(" ", ""), t.replace(".", ""), t.split(" ")[0]}
    return any(
        v and len(v) >= 2 and re.search(rf"(?<![a-z]){re.escape(v)}(?![a-z])", text)
        for v in variants
    )


GREETING = re.compile(
    r"^\W*(hi+|hey+|hy+|hello+|helo|namaste|namaskaram|hai|yo|good (morning|evening|afternoon))\b",
    re.I,
)
QUESTION = re.compile(r"\?\s*$|^\s*(what|why|how|which|when|explain|tell me|can you|is it)\b", re.I)
NUM = re.compile(r"(\d+(?:\.\d+)?)")


FILLER = re.compile(
    r"^(ok(ay)?|k|yes|yeah|yep|no|nope|sure|fine|cool|thanks|thank you|hmm+|haa|avunu|sare|"
    r"ledu|cheppu|tell me|help|start|next|continue|go ahead)\W*$",
    re.I,
)
HOURS_WORD = re.compile(r"\b(h|hr|hrs|hours?|gantalu|gantala)\b", re.I)


def _role_reply(msg: str) -> bool:
    """A short free-text answer that can only be a job role (rules mode only)."""
    low = msg.lower().strip()
    d = intake.extract_rules(msg)
    return (
        1 <= len(low.split()) <= 5
        and not re.search(r"\d", low)
        and re.search(r"[a-z]", low) is not None
        and not any(getattr(d, k) for k in ("branch", "year", "hours_per_week", "target_role"))
        and not d.tech_mastery
        and not GREETING.match(msg)
        and not FILLER.match(low)
        and not QUESTION.search(msg)
    )


def _rules_profile(messages: list[dict[str, str]], pending: list[str]) -> tuple[Profile, Intent]:
    data: dict[str, Any] = {"tech_mastery": {}}
    users: list[str] = []
    asked_role = False  # did the assistant's previous message ask for a role?
    for m in messages:
        if m["role"] == "assistant":
            asked_role = bool(re.search(r"\b(job )?role\b", m["content"], re.I))
            continue
        msg = m["content"]
        users.append(msg)
        d = intake.extract_rules(msg)
        for k in ("name", "branch", "year", "hours_per_week", "max_weeks", "target_role"):
            if (v := getattr(d, k)) is not None:
                data[k] = v
        data["tech_mastery"].update(d.tech_mastery)
        # a role we don't know, given as the answer to "which role?", is still their answer
        if asked_role and _role_reply(msg):
            data["target_role"] = msg.strip()
    last = users[-1] if users else ""
    low = last.lower().strip()
    last_rules = intake.extract_rules(last)
    # a bare number answering "how many hours?" ("10", "around 10", "15 hours")
    if (
        "hours_per_week" in pending
        and last_rules.hours_per_week is None
        and last_rules.year is None
        and not re.search(r"\b(year|yr|sem|semester)\b|\d\s*(st|nd|rd|th)\b", low)
    ):
        if (m := NUM.search(low)) and len(low) <= 40:
            h = float(m.group(1))
            if re.search(r"\b(a|per|each|every)\s*day\b|\bdaily\b", low):
                h *= intake.days_per_week(low)
            if 0 < h <= 80:
                data["hours_per_week"] = h
    if "year" in pending and "year" not in data and re.fullmatch(r"\D*([1-6])\D*", low):
        if not HOURS_WORD.search(low):
            data["year"] = int(re.sub(r"\D", "", low))
    if "target_role" in pending and _role_reply(last):
        data["target_role"] = last.strip()
    found_any = any(k in data for k in ("branch", "year", "hours_per_week", "target_role"))
    intent: Intent
    if GREETING.match(last) and not intake.extract_rules(last).target_role and len(low) < 30:
        intent = "greeting"
    elif QUESTION.search(last):
        intent = "question"
    else:
        intent = "profile" if found_any else "other"
    return Profile.model_validate(data), intent


def understand(
    messages: list[dict[str, str]], llm: LLM | None, pending: list[str] | None = None
) -> Understanding:
    got = _llm_profile(messages, llm) if llm is not None else None
    if got is not None:
        profile, intent = got
        source = llm.provider if llm is not None else "rules"
        # rules still catch what a terse LLM answer may miss (e.g. a bare "10")
        rp, _ = _rules_profile(messages, pending or [])
        for k in ("hours_per_week", "year"):
            if getattr(profile, k) is None and getattr(rp, k) is not None:
                profile = profile.model_copy(update={k: getattr(rp, k)})
    else:
        profile, intent = _rules_profile(messages, pending or [])
        source = "rules"
    if profile.branch:
        profile = profile.model_copy(update={"branch": intake.normalize_branch(profile.branch)})
    if profile.name:
        profile = profile.model_copy(update={"name": profile.name.strip().title()})

    u = Understanding(profile=profile, intent=intent, source=source)
    if profile.target_role:
        u.role_key = intake.normalize_role(profile.target_role)
        if u.role_key is None:
            if source != "rules" and plausible_role(profile.target_role):
                u.custom_role = profile.target_role.strip().lower()
            else:  # without an LLM only curated roles can be planned
                u.unsupported_role = profile.target_role
    u.missing = [
        f
        for f in REQUIRED
        if (f == "target_role" and u.role_key is None and u.custom_role is None)
        or (f != "target_role" and getattr(profile, f) is None)
    ]
    return u


def plausible_role(text: str) -> bool:
    """A short job title, not a sentence or a greeting."""
    words = text.split()
    return 1 <= len(words) <= 6 and len(text) <= 60 and not GREETING.match(text)


def plan_input(u: Understanding) -> dict[str, Any]:
    """The profile the planner needs (all REQUIRED fields present)."""
    p = u.profile
    return {
        "name": p.name or "Student",
        "branch": p.branch,
        "year": p.year,
        "hours_per_week": p.hours_per_week,
        "target_role": u.role_key or u.custom_role,
        "tech_mastery": p.tech_mastery,
        "max_weeks": p.max_weeks,
    }


def plan_changed(u: Understanding, last: dict[str, Any] | None) -> bool:
    if not last:
        return True
    now = plan_input(u)
    keys = ("branch", "year", "hours_per_week", "target_role", "max_weeks")
    if any(now.get(k) != last.get(k) for k in keys):
        return True
    before = {k.lower(): v for k, v in (last.get("tech_mastery") or {}).items()}
    return any(before.get(k.lower()) != v for k, v in now["tech_mastery"].items())


def should_plan(u: Understanding, last_plan: dict[str, Any] | None) -> bool:
    return not u.missing and plan_changed(u, last_plan)


# ---------------- what to ask and suggest ----------------

ASK_PHRASES = {
    "branch": "what they study (degree and branch)",
    "year": "which year they are in",
    "target_role": "which job role they want",
    "hours_per_week": "how many hours a week they can spend learning",
}

OPTIONS = {
    "hours_per_week": ["5 hours a week", "8 hours a week", "10 hours a week", "15 hours a week"],
    "year": ["1st year", "2nd year", "3rd year", "4th year"],
    "branch": ["B.Tech CSE", "B.Tech AI&ML", "B.Tech ECE", "B.Com"],
}


def suggest_roles(p: Profile) -> list[str]:
    hint = f"{p.branch or ''} {p.interest or ''} {p.target_role or ''}".lower()
    order = ["devops engineer", "data analyst", "machine learning engineer"]
    if re.search(r"\b(ai|ml|machine|data science|ds|intelligen)", hint):
        order = ["machine learning engineer", "data analyst", "devops engineer"]
    elif re.search(r"com|bba|business|finance|account|econ|stat|math", hint):
        order = ["data analyst", "machine learning engineer", "devops engineer"]
    elif re.search(r"cloud|devops|linux|network|infra|server", hint):
        order = ["devops engineer", "machine learning engineer", "data analyst"]
    return [role_name(r) for r in order]


def ask_for(u: Understanding) -> list[str]:
    """At most two things to ask next, in a natural order."""
    return u.missing[:2]


def options_for(u: Understanding) -> list[str]:
    nxt = ask_for(u)
    if not nxt:
        return []
    if nxt[0] == "target_role":
        return suggest_roles(u.profile)
    return OPTIONS.get(nxt[0], [])


# ---------------- talking ----------------

REPLY_SYSTEM = """You are CareerCompass, a warm, practical career mentor for Indian college \
students. You chat to understand the student; the app then builds a week-by-week roadmap \
grounded in O*NET job data.
Rules:
- Reply in 1 to 5 short sentences, like a friendly senior. Markdown **bold** is fine. Use a short \
list only when answering a question that needs one.
- Match the student's language. If they write Telugu or Hindi in English letters, reply the same \
way; otherwise reply in English.
- Greet only in your first reply of the conversation; after that, get straight to the point.
- If the student only said hello and nothing is known yet, introduce yourself in one sentence \
(you plan week-by-week career roadmaps) before asking.
- Use their first name now and then if CONTEXT has it, spelled exactly as given.
- Use only facts from CONTEXT about them and their plan. Never invent courses, links, salaries, \
numbers or timelines.
- If CONTEXT.ask_for is not empty, end with ONE natural question asking for exactly those things.
- If the role is still unknown and CONTEXT.suggest_roles is given, suggest those roles briefly, \
leading with the first one and saying why it fits them.
- If CONTEXT.unsupported_role is set, say kindly that a detailed roadmap for it is not available \
yet, and offer CONTEXT.suggest_roles instead.
- If the student asks a question, answer it briefly and correctly. General career knowledge is \
fine; for anything about their own plan use CONTEXT.roadmap only.
- Never mention CONTEXT, JSON, fields, or that you are a language model."""


def reply_context(
    u: Understanding, roadmap_facts: dict[str, Any] | None, first_reply: bool = False
) -> dict[str, Any]:
    p = u.profile
    known = {
        "first_name": p.name,
        "studies": " ".join(x for x in [p.branch or "", f"year {p.year}" if p.year else ""] if x)
        or None,
        "wants_role": role_name(u.role_key or u.custom_role or p.target_role)
        if (u.role_key or u.custom_role or p.target_role)
        else None,
        "interest": p.interest,
        "hours_per_week": p.hours_per_week,
        "knows": list(p.tech_mastery)[:8] or None,
        "deadline_weeks": p.max_weeks,
    }
    return {
        "intent_of_last_message": u.intent,
        "first_reply": first_reply,
        "student": {k: v for k, v in known.items() if v not in (None, "", [])},
        "ask_for": [ASK_PHRASES[f] for f in ask_for(u)],
        "suggest_roles": suggest_roles(p) if "target_role" in u.missing else [],
        "unsupported_role": u.unsupported_role,
        "roles_with_hand_checked_catalogs": [role_name(r) for r in ROLE_NAMES],
        "note": "Any other job role can also be planned from O*NET job data.",
        "roadmap": roadmap_facts,
    }


def reply_prompt(messages: list[dict[str, str]], ctx: dict[str, Any]) -> str:
    return (
        "CONTEXT:\n"
        + json.dumps(ctx, ensure_ascii=False)
        + "\n\nCONVERSATION:\n"
        + transcript(messages, limit=10)
        + "\n\nWrite CareerCompass's next reply."
    )


def _ack(last: str) -> str:
    """Echo what the student's last message told us, e.g. 'Got it, 4th year.'"""
    d = intake.extract_rules(last)
    bits = []
    if d.branch:
        bits.append(d.branch)
    if d.year:
        bits.append({1: "1st", 2: "2nd", 3: "3rd"}.get(d.year, f"{d.year}th") + " year")
    if d.target_role:
        bits.append(role_name(d.target_role))
    if d.hours_per_week:
        bits.append(f"{d.hours_per_week:g} hours a week")
    return f"Got it, {', '.join(bits)}. " if bits else "Got it. "


def template_reply(u: Understanding, has_roadmap: bool, last: str = "") -> str:
    """Reply used when no LLM is available."""
    p = u.profile
    hi = f"Hi {p.name}! " if p.name else "Hi! "
    nxt = ask_for(u)
    if u.unsupported_role:
        roles = suggest_roles(p)
        choices = f"{', '.join(roles[:-1])} or {roles[-1]}"
        if u.source == "rules":
            return (
                f"To plan **{display_role(u.unsupported_role)}** I need my AI planner, which is "
                f"busy right now. Try again in a minute, or pick a role I can plan right away: "
                f"{choices}."
            )
        return f"I didn't catch the job role. Which one do you want? For example {choices}."
    if u.intent == "question" and has_roadmap and not nxt:
        return (
            "I can't answer open questions right now, but your roadmap is on the right. "
            "You can also tell me to change your weekly hours, deadline or the skills you know."
        )
    if not any(getattr(p, f) for f in ("branch", "year", "target_role", "hours_per_week")):
        return (
            f"{hi}I'm CareerCompass. Tell me what you study, which year you're in and the job "
            "you'd like, and I'll plan a week-by-week roadmap for you."
        )
    asks = {
        "branch": "what are you studying (degree and branch)",
        "year": "which year are you in",
        "target_role": "which job role do you want",
        "hours_per_week": "how many hours a week can you spend learning",
    }
    q = " and ".join(asks[f] for f in nxt)
    lead = _ack(last) if p.name is None else f"Thanks, {p.name}. "
    extra = ""
    if "target_role" in nxt:
        roles = suggest_roles(p)
        extra = f" Based on what you study, **{roles[0]}** could be a good fit."
    return f"{lead}{q[0].upper()}{q[1:]}?{extra}"


def talk(
    messages: list[dict[str, str]],
    u: Understanding,
    llm: LLM | None,
    roadmap_facts: dict[str, Any] | None,
) -> Iterator[str]:
    """Stream the assistant's reply; falls back to a template if the LLM fails early."""
    first = not any(m["role"] == "assistant" for m in messages)
    if llm is not None and hasattr(llm, "stream"):
        sent = False
        try:
            for piece in llm.stream(
                REPLY_SYSTEM,
                reply_prompt(messages, reply_context(u, roadmap_facts, first)),
                smart=True,
            ):
                sent = True
                yield piece
            return
        except LLMError:
            if sent:
                return
    elif llm is not None:
        try:
            yield llm.chat(
                REPLY_SYSTEM,
                reply_prompt(messages, reply_context(u, roadmap_facts, first)),
                smart=True,
            )
            return
        except LLMError:
            pass
    users = [m["content"] for m in messages if m["role"] == "user"]
    yield template_reply(u, roadmap_facts is not None, users[-1] if users else "")
