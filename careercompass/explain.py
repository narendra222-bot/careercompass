"""Plain-language explanation of a roadmap: LLM when available, template otherwise.

The LLM sees only derived plan facts (role, phases, gaps, readiness, hours), never the
student's name. Its answer is rejected, and the template used instead, if it contains
a URL (links must come from the catalog), is too short or too long, or the call fails.
"""

from __future__ import annotations

import json
import re
from typing import Any

from careercompass.llm import LLM, LLMError
from careercompass.schemas import Roadmap

MIN_CHARS = 200
MAX_CHARS = 2500


def facts(r: Roadmap) -> dict[str, Any]:
    """The only data an LLM is allowed to see."""
    return {
        "target_role": r.target_role,
        "background": {"branch": r.student.branch, "year": r.student.year},
        "hours_per_week": r.student.hours_per_week,
        "total_weeks": r.feasibility.total_weeks,
        "feasible": r.feasibility.feasible,
        "feasibility_fix": r.feasibility.fix,
        "core_readiness": {
            "start": r.baseline_readiness.core.overall,
            "end": r.final_readiness.core.overall,
        },
        "already_known_skipped": r.skipped_known,
        "top_tech_gaps": [g.tech for g in r.top_tech_gaps[:6]],
        "phases": [
            {
                "phase": p.phase,
                "weeks": f"{p.start_week}-{p.end_week}",
                "milestone": p.title,
                "items": [n.label for n in r.nodes if n.id in p.node_ids],
                "core_readiness_after": p.readiness_after.core.overall,
            }
            for p in r.phases
        ],
    }


def template(r: Roadmap) -> str:
    f = facts(r)
    start, end = f["core_readiness"]["start"], f["core_readiness"]["end"]
    lines = [
        f"This roadmap takes a {f['background']['branch']} year-{f['background']['year']} "
        f"student towards {r.target_role.title()} in about {f['total_weeks']} weeks at "
        f"{f['hours_per_week']:g} hours per week.",
        f"Core readiness for the role grows from {start:.0%} to {end:.0%}.",
    ]
    if f["already_known_skipped"]:
        lines.append(
            "Courses you already know were skipped: " + ", ".join(f["already_known_skipped"]) + "."
        )
    if f["top_tech_gaps"]:
        lines.append("Biggest tool gaps today: " + ", ".join(f["top_tech_gaps"]) + ".")
    for p in f["phases"]:
        lines.append(
            f"Phase {p['phase']} (weeks {p['weeks']}): {p['milestone']}. "
            f"Core readiness afterwards: {p['core_readiness_after']:.0%}."
        )
    if not f["feasible"] and f["feasibility_fix"]:
        fix = f["feasibility_fix"]
        lines.append(
            f"Your deadline is too tight for this plan: it needs about "
            f"{fix['needed_hours_per_week_for_max_weeks']} hours per week, or "
            f"{fix['needed_weeks_at_current_hours']} weeks at your current pace."
        )
    return "\n".join(lines)


SYSTEM = """You are a friendly career mentor for Indian college students.
Explain the roadmap described by the JSON facts in 120-250 words of plain English:
why the phases come in this order, what the student will be able to show after each
project, and one practical tip for staying consistent. Use only the facts given.
Do not add courses, links, URLs, salaries or numbers that are not in the facts."""

URL_RE = re.compile(r"https?://|www\.", re.IGNORECASE)


def explain(r: Roadmap, llm: LLM | None) -> tuple[str, str]:
    """Return (text, source) where source is the provider name or "template"."""
    if llm is not None:
        try:
            text = llm.chat(SYSTEM, json.dumps(facts(r)), smart=True).strip()
            if not URL_RE.search(text) and MIN_CHARS <= len(text) <= MAX_CHARS:
                return text, llm.provider
        except LLMError:
            pass
    return template(r), "template"
