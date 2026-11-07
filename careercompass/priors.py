"""Degree-based prior proficiency definitions for student profiles.

All PRIORS and year-factor numbers in this module are editorial, human_reviewed: false.
Expresses baseline proficiency as a fraction of an O*NET element's required level (lvl).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class PriorConfig:
    """Configuration constants for degree prior proficiency estimation.

    All numbers: editorial, human_reviewed: false.
    """

    default_fraction: float = 0.25
    year_base_factor: float = 0.70
    year_scale_step: float = 0.10
    year_cap: float = 1.00
    editorial_flag: str = "editorial, human_reviewed: false"


# Optional alias mapping from common naming variations to canonical O*NET element names
ELEMENT_ALIASES: dict[str, str] = {
    "computer science": "Computers and Electronics",
    "software engineering": "Programming",
    "math": "Mathematics",
    "stats": "Mathematics",
    "english": "English Language",
    "accounting": "Economics and Accounting",
}

# Priors keyed by (degree_family, branch)
# Values represent fraction (0.0 to 1.0) of element lvl.
# All numbers: editorial, human_reviewed: false.
PRIORS: dict[tuple[str, str], dict[str, float]] = {
    ("Engineering", "Computer Science and Engineering"): {
        "Programming": 0.70,
        "Computers and Electronics": 0.60,
        "Mathematics": 0.50,
        "Critical Thinking": 0.55,
        "Reading Comprehension": 0.60,
        "Systems Analysis": 0.45,
        "English Language": 0.60,
        "Complex Problem Solving": 0.50,
        "Active Listening": 0.55,
        "Engineering and Technology": 0.55,
        "Technology Design": 0.50,
        "Troubleshooting": 0.50,
        "Active Learning": 0.55,
        "Judgment and Decision Making": 0.45,
        "Systems Evaluation": 0.40,
        "Writing": 0.50,
        "Speaking": 0.50,
    },
    ("Commerce", "General Commerce"): {
        "Economics and Accounting": 0.70,
        "Administration and Management": 0.65,
        "English Language": 0.65,
        "Reading Comprehension": 0.65,
        "Writing": 0.60,
        "Speaking": 0.60,
        "Active Listening": 0.65,
        "Customer and Personal Service": 0.60,
        "Critical Thinking": 0.55,
        "Active Learning": 0.55,
        "Monitoring": 0.55,
        "Judgment and Decision Making": 0.60,
        "Complex Problem Solving": 0.50,
        "Coordination": 0.50,
        "Social Perceptiveness": 0.50,
        "Time Management": 0.50,
        "Personnel and Human Resources": 0.50,
        "Mathematics": 0.45,
        "Operations Monitoring": 0.45,
        "Management of Financial Resources": 0.60,
        "Management of Personnel Resources": 0.55,
        "Management of Material Resources": 0.50,
        "Instructing": 0.45,
        "Service Orientation": 0.50,
        "Persuasion": 0.50,
        "Negotiation": 0.50,
        "Systems Analysis": 0.20,
        "Systems Evaluation": 0.20,
        "Computers and Electronics": 0.15,
        "Telecommunications": 0.30,
        "Engineering and Technology": 0.35,
        "Technology Design": 0.20,
        "Troubleshooting": 0.30,
        "Learning Strategies": 0.45,
        "Programming": 0.05,
    },
}


def _normalize_branch_key(profile_branch: tuple[str, str] | str) -> tuple[str, str] | None:
    """Normalize input profile branch into registered PRIORS key."""
    if isinstance(profile_branch, tuple) and len(profile_branch) == 2:
        for k in PRIORS:
            if (
                k[0].strip().lower() == profile_branch[0].strip().lower()
                and k[1].strip().lower() == profile_branch[1].strip().lower()
            ):
                return k
        return profile_branch

    branch_str = str(profile_branch).strip().lower()
    if any(term in branch_str for term in ["cse", "computer science", "cs", "computer engineering"]):
        return ("Engineering", "Computer Science and Engineering")
    # CS-family branches share most of the CSE first-years curriculum
    cs_family = r"\bai\s*&?\s*(ml|ds)\b|\bdata science\b|\bit\b|information technology"
    if re.search(cs_family, branch_str):
        return ("Engineering", "Computer Science and Engineering")
    if any(term in branch_str for term in ["b.com", "bcom", "commerce", "business", "accounting"]):
        return ("Commerce", "General Commerce")

    return None


def seed_proficiency(
    profile_branch: tuple[str, str] | str,
    year: int,
    reqs: list[dict[str, Any]],
    cfg: PriorConfig = PriorConfig(),
) -> dict[str, float]:
    """Calculate prior seed proficiency for a student profile.

    Formula: fraction * element_lvl * year_scaling_factor.
    Year scaling factor (years 1..4): 0.7 + 0.1*(year-1), capped at 1.0.
    Unlisted elements default to cfg.default_fraction (0.25).
    All numbers: editorial, human_reviewed: false.
    """
    year_clamped = max(1, year)
    year_factor = min(cfg.year_cap, cfg.year_base_factor + cfg.year_scale_step * (year_clamped - 1))

    norm_key = _normalize_branch_key(profile_branch)
    branch_priors = PRIORS.get(norm_key, {}) if norm_key else {}

    seeded_prof: dict[str, float] = {}

    for r in reqs:
        elem_name = r["element_name"]
        lvl = float(r.get("lvl", 0.0))

        fraction = branch_priors.get(elem_name)
        if fraction is None:
            for p_name, p_frac in branch_priors.items():
                if p_name.lower() == elem_name.lower():
                    fraction = p_frac
                    break

        if fraction is None:
            fraction = cfg.default_fraction

        p = float(fraction * lvl * year_factor)
        seeded_prof[elem_name] = max(0.0, p)

    return seeded_prof
