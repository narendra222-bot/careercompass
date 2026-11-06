"""Turn a student's free-text description into a StudentProfile draft.

Two extractors produce the same IntakeDraft:
  * rules  - regexes and the curated alias/tool lists (always available, deterministic)
  * llm    - an LLM asked for strict JSON (optional, handles messier phrasing)
The LLM draft is validated with pydantic and merged over the rule draft field by field;
anything it gets wrong in type or range is discarded, not trusted. Required fields that
are still missing become questions for the human (human-in-the-loop).
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, ValidationError, field_validator

from careercompass import tech_catalog
from careercompass.llm import LLM, LLMError, parse_json

ROOT = Path(__file__).resolve().parent.parent
REQUIRED = ("branch", "year", "hours_per_week", "target_role")
DEFAULT_MASTERY = 0.5
STRONG_MASTERY = 0.8

QUESTIONS = {
    "branch": "What is your degree and branch (for example B.Tech CSE, B.Com, B.Sc)?",
    "year": "Which year of study are you in (1-4), or have you graduated?",
    "hours_per_week": "How many hours per week can you spend on learning?",
    "target_role": "Which role are you aiming for?",
}

BRANCHES: list[tuple[str, str]] = [
    (r"\bb\.?\s?com\b|\bcommerce\b", "B.Com"),
    (r"\bcse\b|\bcomputer science\b|\bcomputer engineering\b", "CSE"),
    (r"\bece\b|\belectronics\b", "ECE"),
    (r"\beee\b|\belectrical\b", "EEE"),
    (r"\bmech(anical)?\b", "Mechanical"),
    (r"\bcivil\b", "Civil"),
    (r"\bb\.?\s?sc\b|\bbachelor of science\b", "B.Sc"),
    (r"\bbba\b|\bmanagement\b", "BBA"),
    (r"\bb\.?\s?a\b|\barts\b", "B.A"),
    (r"\binformation technology\b|\bb\.?tech it\b", "IT"),
]


class IntakeDraft(BaseModel):
    name: str | None = None
    branch: str | None = None
    year: int | None = Field(default=None, ge=1, le=6)
    hours_per_week: float | None = Field(default=None, gt=0, le=80)
    target_role: str | None = None
    tech_mastery: dict[str, float] = Field(default_factory=dict)
    max_weeks: int | None = Field(default=None, gt=0, le=260)

    @field_validator("tech_mastery")
    @classmethod
    def _clamp(cls, v: dict[str, float]) -> dict[str, float]:
        return {k.strip(): min(1.0, max(0.0, float(m))) for k, m in v.items() if k.strip()}


@lru_cache(maxsize=1)
def supported_roles() -> dict[str, list[str]]:
    """Roles with a curated tool list and catalog -> lowercase aliases."""
    with (ROOT / "data" / "tech_curated.yaml").open(encoding="utf-8") as fh:
        curated = set((yaml.safe_load(fh) or {}).keys())
    with (ROOT / "data" / "role_profiles.yaml").open(encoding="utf-8") as fh:
        profiles: dict[str, Any] = yaml.safe_load(fh) or {}
    out: dict[str, list[str]] = {}
    for role in curated:
        aliases = [a.lower() for a in profiles.get(role, {}).get("aliases", [])]
        out[role] = sorted({role.lower(), *aliases}, key=len, reverse=True)
    return out


def normalize_role(text: str | None) -> str | None:
    if not text:
        return None
    low = text.lower()
    best: tuple[int, str] | None = None
    for role, aliases in supported_roles().items():
        for a in aliases:
            if re.search(rf"\b{re.escape(a)}\b", low) and (best is None or len(a) > best[0]):
                best = (len(a), role)
    return best[1] if best else None


@lru_cache(maxsize=1)
def _tool_names() -> list[str]:
    names: dict[str, str] = {}
    for role in sorted(supported_roles()):
        for t in tech_catalog.load_curated(role):
            names.setdefault(t.lower(), t)
    for alias in tech_catalog.TECH_ALIASES:
        names.setdefault(alias.lower(), alias)
    return sorted(names.values(), key=len, reverse=True)


def extract_rules(text: str) -> IntakeDraft:
    low = text.lower()
    d: dict[str, Any] = {}

    if m := re.search(r"(?i:my name is|i am|i'm)\s+([A-Z][a-z]+)", text):
        d["name"] = m.group(1)
    for pattern, branch in BRANCHES:
        if re.search(pattern, low):
            d["branch"] = branch
            break
    if re.search(r"\bfinal year\b", low):
        d["year"] = 4
    elif m := re.search(r"\b([1-6])(?:st|nd|rd|th)?\s*(?:year|yr)\b", low):
        d["year"] = int(m.group(1))
    if m := re.search(
        r"(\d+(?:\.\d+)?)\s*(?:hours|hrs|hr|h)\s*(?:a|per|/|every|each)?\s*(week|wk|day)", low
    ):
        hours = float(m.group(1))
        d["hours_per_week"] = hours * 7 if m.group(2) == "day" else hours
    if m := re.search(r"(?:in|within|by)\s+(\d+)\s*(months?|weeks?)", low):
        n = int(m.group(1))
        d["max_weeks"] = n * 4 if m.group(2).startswith("month") else n
    d["target_role"] = normalize_role(text)

    mastery: dict[str, float] = {}
    for tool in _tool_names():
        pat = rf"(?<![\w+#.]){re.escape(tool.lower())}(?![\w+#])"
        for m in re.finditer(pat, low):
            # strength words must come just before the tool ("strong in python")
            before = low[max(0, m.start() - 20) : m.start()]
            strong = re.search(r"\b(strong|expert|advanced|good at|proficient)\b[^,.;]*$", before)
            mastery.setdefault(tool, STRONG_MASTERY if strong else DEFAULT_MASTERY)
    d["tech_mastery"] = mastery
    try:
        return IntakeDraft.model_validate(d)
    except ValidationError:
        return IntakeDraft()


SYSTEM = """You extract a student's profile from text for a career-planning tool.
Return ONLY a JSON object with these keys (null when not stated, never guess):
  branch: degree/branch as written, e.g. "CSE", "B.Com", "ECE"
  year: integer year of study 1-6 (final year = 4)
  hours_per_week: number of study hours per week (convert per-day x7)
  target_role: the job role they want, as written
  tech_mastery: object mapping each tool/technology they say they know to a level
                0.3 (beginner), 0.5 (comfortable) or 0.8 (strong)
  max_weeks: deadline in weeks if they give one (months x4), else null
Do not include the student's name or any other field."""


def extract_llm(text: str, llm: LLM) -> IntakeDraft | None:
    try:
        raw = parse_json(llm.chat(SYSTEM, text, smart=False, json_mode=True))
    except (LLMError, ValueError):
        return None
    raw.pop("name", None)
    tm = raw.get("tech_mastery")
    if not isinstance(tm, dict):
        raw["tech_mastery"] = {}
    else:
        raw["tech_mastery"] = {
            str(k): float(v) for k, v in tm.items() if isinstance(v, int | float)
        }
    try:
        return IntakeDraft.model_validate(raw)
    except ValidationError:
        return None


def parse_intake(text: str, llm: LLM | None = None) -> dict[str, Any]:
    """Return {"draft": IntakeDraft, "missing": [...], "questions": [...], "source": str}."""
    rules = extract_rules(text)
    merged = rules
    source = "rules"
    if llm is not None and (got := extract_llm(text, llm)) is not None:
        source = f"rules+{llm.provider}"
        data = rules.model_dump()
        for k, v in got.model_dump().items():
            if k == "tech_mastery":
                data[k] = {**rules.tech_mastery, **v}
            elif v not in (None, ""):
                data[k] = v
        merged = IntakeDraft.model_validate(data)
    role = normalize_role(merged.target_role) or merged.target_role
    merged = merged.model_copy(update={"target_role": role})
    missing = [f for f in REQUIRED if getattr(merged, f) in (None, "")]
    return {
        "draft": merged,
        "missing": missing,
        "questions": [QUESTIONS[f] for f in missing],
        "source": source,
    }
