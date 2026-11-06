"""Unit tests for degree-based prior proficiency definitions."""

from __future__ import annotations

import math
from typing import Any

import pytest

from careercompass import priors
from careercompass.priors import ELEMENT_ALIASES, PRIORS, PriorConfig, seed_proficiency


def test_prior_year_scaling() -> None:
    """Validate year scaling factor: Year 1: 0.7, Year 2: 0.8, Year 3: 0.9, Year 4: 1.0."""
    reqs = [{"element_name": "Programming", "lvl": 5.0}]
    branch = ("Engineering", "Computer Science and Engineering")

    # Fraction is 0.70.
    # Year 1: 0.70 * 5.0 * 0.7 = 2.45
    prof_y1 = seed_proficiency(branch, 1, reqs)
    assert math.isclose(prof_y1["Programming"], 2.45)

    # Year 2: 0.70 * 5.0 * 0.8 = 2.80
    prof_y2 = seed_proficiency(branch, 2, reqs)
    assert math.isclose(prof_y2["Programming"], 2.80)

    # Year 3: 0.70 * 5.0 * 0.9 = 3.15
    prof_y3 = seed_proficiency(branch, 3, reqs)
    assert math.isclose(prof_y3["Programming"], 3.15)

    # Year 4: 0.70 * 5.0 * 1.0 = 3.50
    prof_y4 = seed_proficiency(branch, 4, reqs)
    assert math.isclose(prof_y4["Programming"], 3.50)

    # Year 5+ capped at 1.0
    prof_y5 = seed_proficiency(branch, 5, reqs)
    assert math.isclose(prof_y5["Programming"], 3.50)


def test_prior_unlisted_element_fallback() -> None:
    """Unlisted elements default to default_fraction (0.25)."""
    reqs = [{"element_name": "Unlisted Random Skill", "lvl": 4.0}]
    branch = ("Engineering", "Computer Science and Engineering")

    # Year 4 -> factor 1.0 -> 0.25 * 4.0 * 1.0 = 1.0
    prof = seed_proficiency(branch, 4, reqs)
    assert math.isclose(prof["Unlisted Random Skill"], 1.0)


def test_cse_vs_bcom_prior_differences() -> None:
    """Verify CSE has high technical fraction and B.Com has high accounting fraction."""
    reqs = [
        {"element_name": "Programming", "lvl": 6.0},
        {"element_name": "Economics and Accounting", "lvl": 6.0},
    ]

    cse_prof = seed_proficiency(("Engineering", "Computer Science and Engineering"), 4, reqs)
    bcom_prof = seed_proficiency(("Commerce", "General Commerce"), 4, reqs)

    # CSE programming: 0.70 * 6 = 4.2. B.Com programming: 0.05 * 6 = 0.3
    assert cse_prof["Programming"] > bcom_prof["Programming"]
    assert math.isclose(cse_prof["Programming"], 4.2)
    assert math.isclose(bcom_prof["Programming"], 0.3)

    # B.Com economics: 0.70 * 6 = 4.2. CSE economics (unlisted -> 0.25): 1.5
    assert bcom_prof["Economics and Accounting"] > cse_prof["Economics and Accounting"]
    assert math.isclose(bcom_prof["Economics and Accounting"], 4.2)
    assert math.isclose(cse_prof["Economics and Accounting"], 1.5)


@pytest.mark.needs_data
def test_all_priors_elements_exist_in_onet() -> None:
    """Every PRIORS element name must exist in the O*NET requirements for the blend or in ELEMENT_ALIASES."""
    from careercompass import db, scoring

    con = db.connect()
    socs = ["15-1252.00", "15-1299.08", "15-1244.00"]
    alphas = {"15-1252.00": 0.25, "15-1299.08": 0.45, "15-1244.00": 0.30}
    reqs_by_soc = {s: db.get_requirements(con, s, include_unreliable=True) for s in socs}
    blended_reqs = scoring.blend([reqs_by_soc[s] for s in socs], [alphas[s] for s in socs])

    blended_names = {r["element_name"] for r in blended_reqs}

    # Verify specific required elements exist exactly as spelled
    assert "Economics and Accounting" in blended_names
    assert "Mathematics" in blended_names
    assert "English Language" in blended_names

    unmatched_names: list[tuple[tuple[str, str], str]] = []

    for branch_key, elem_dict in PRIORS.items():
        for elem_name in elem_dict:
            resolved_name = ELEMENT_ALIASES.get(elem_name.lower(), elem_name)
            if resolved_name not in blended_names and elem_name not in blended_names:
                unmatched_names.append((branch_key, elem_name))

    print(f"\nUnmatched PRIORS element names count: {len(unmatched_names)}")
    if unmatched_names:
        print("Unmatched elements:", unmatched_names)

    assert not unmatched_names, f"Found unmatched element names in PRIORS: {unmatched_names}"

