"""Hypothesis property-based tests for deterministic scoring and gap analysis."""

from __future__ import annotations

import math
from typing import Any

from hypothesis import given, settings
from hypothesis import strategies as st

from careercompass import scoring
from careercompass.scoring import ScoringConfig

ELEMENT_NAMES = [
    "Programming",
    "Critical Thinking",
    "Systems Analysis",
    "Computers and Electronics",
    "Mathematics",
    "Complex Problem Solving",
    "Active Listening",
    "Writing",
    "Deductive Reasoning",
    "Inductive Reasoning",
]

DOMAINS = ["skill", "knowledge", "ability"]


@st.composite
def req_strategy(draw: st.DrawFn) -> dict[str, Any]:
    """Generate a single requirement row with bounded imp and lvl."""
    return {
        "domain": draw(st.sampled_from(DOMAINS)),
        "element_name": draw(st.sampled_from(ELEMENT_NAMES)),
        "imp": draw(st.floats(min_value=1.0, max_value=5.0, allow_nan=False)),
        "lvl": draw(st.floats(min_value=0.0, max_value=7.0, allow_nan=False)),
        "reliable": draw(st.booleans()),
    }


@st.composite
def tech_strategy(draw: st.DrawFn) -> dict[str, Any]:
    """Generate a single technology row."""
    return {
        "example": draw(st.sampled_from(["Python", "Docker", "SQL", "Linux", "Git", "K8s"])),
        "commodity_title": draw(st.sampled_from(["Development Software", "Operating System", "Database"])),
        "hot": draw(st.floats(min_value=0.0, max_value=1.0, allow_nan=False)),
        "in_demand": draw(st.floats(min_value=0.0, max_value=1.0, allow_nan=False)),
    }


# 1. Readiness values always in [0, 1]
@given(
    reqs=st.lists(req_strategy(), max_size=30),
    techs=st.lists(tech_strategy(), max_size=20),
    prof=st.dictionaries(st.sampled_from(ELEMENT_NAMES), st.floats(min_value=0.0, max_value=10.0), max_size=10),
    mastery=st.dictionaries(st.sampled_from(["Python", "Docker", "SQL", "Linux", "Git", "K8s"]), st.floats(min_value=0.0, max_value=1.0), max_size=6),
)
@settings(max_examples=100)
def test_readiness_bounds(
    reqs: list[dict[str, Any]],
    techs: list[dict[str, Any]],
    prof: dict[str, float],
    mastery: dict[str, float],
) -> None:
    res = scoring.readiness(reqs, techs, prof, mastery)
    for domain, score in res.items():
        assert 0.0 <= score <= 1.0, f"Score for {domain} was {score}, outside [0, 1]"


# 2. Raising any proficiency never lowers readiness (Monotonicity)
@given(
    reqs=st.lists(req_strategy(), max_size=30),
    techs=st.lists(tech_strategy(), max_size=20),
    prof=st.dictionaries(st.sampled_from(ELEMENT_NAMES), st.floats(min_value=0.0, max_value=7.0), max_size=10),
    mastery=st.dictionaries(st.sampled_from(["Python", "Docker", "SQL", "Linux", "Git", "K8s"]), st.floats(min_value=0.0, max_value=1.0), max_size=6),
    elem=st.sampled_from(ELEMENT_NAMES),
    delta_p=st.floats(min_value=0.01, max_value=3.0),
)
@settings(max_examples=100)
def test_monotonicity(
    reqs: list[dict[str, Any]],
    techs: list[dict[str, Any]],
    prof: dict[str, float],
    mastery: dict[str, float],
    elem: str,
    delta_p: float,
) -> None:
    base_res = scoring.readiness(reqs, techs, prof, mastery)

    prof_higher = dict(prof)
    prof_higher[elem] = prof_higher.get(elem, 0.0) + delta_p

    higher_res = scoring.readiness(reqs, techs, prof_higher, mastery)

    for domain in ["skill", "knowledge", "ability", "overall"]:
        assert higher_res[domain] >= base_res[domain] - 1e-9, (
            f"Raising proficiency lowered {domain} readiness: {base_res[domain]} -> {higher_res[domain]}"
        )


# 3. Blend with alphas=[1.0] reproduces single-occupation requirements exactly
@given(
    reqs=st.lists(req_strategy(), min_size=1, max_size=30),
)
@settings(max_examples=50)
def test_blend_identity_single_occupation(reqs: list[dict[str, Any]]) -> None:
    unique_reqs: dict[tuple[str, str], dict[str, Any]] = {}
    for r in reqs:
        key = (r["domain"], r["element_name"])
        if key not in unique_reqs:
            unique_reqs[key] = r

    input_list = list(unique_reqs.values())
    blended = scoring.blend([input_list], [1.0])

    assert len(blended) == len(input_list)
    blended_map = {(r["domain"], r["element_name"]): r for r in blended}

    for orig in input_list:
        key = (orig["domain"], orig["element_name"])
        assert key in blended_map
        b = blended_map[key]
        assert math.isclose(b["imp"], orig["imp"], abs_tol=1e-9)
        assert math.isclose(b["lvl"], orig["lvl"], abs_tol=1e-9)
        assert b["reliable"] == orig["reliable"]


# 4. Blend with bad alphas raises ValueError
@given(
    alpha1=st.floats(min_value=-2.0, max_value=2.0),
    alpha2=st.floats(min_value=-2.0, max_value=2.0),
)
@settings(max_examples=50)
def test_blend_bad_alphas(alpha1: float, alpha2: float) -> None:
    reqs = [[{"domain": "skill", "element_name": "Programming", "imp": 4.0, "lvl": 4.0}]] * 2
    is_valid = (
        alpha1 >= 0.0
        and alpha2 >= 0.0
        and math.isclose(alpha1 + alpha2, 1.0, rel_tol=1e-9, abs_tol=1e-9)
    )

    if not is_valid:
        try:
            scoring.blend(reqs, [alpha1, alpha2])
            raise AssertionError(f"Expected ValueError for alphas {[alpha1, alpha2]}")
        except ValueError:
            pass


# 5. Gaps never include ability rows and are sorted descending
@given(
    reqs=st.lists(req_strategy(), max_size=30),
    prof=st.dictionaries(st.sampled_from(ELEMENT_NAMES), st.floats(min_value=0.0, max_value=7.0), max_size=10),
)
@settings(max_examples=100)
def test_gaps_properties(reqs: list[dict[str, Any]], prof: dict[str, float]) -> None:
    g_list = scoring.gaps(reqs, prof)
    for item in g_list:
        assert item["domain"] != "ability", f"Found ability in gaps: {item}"

    # Verify descending sort by gap, tie-break by element_name asc
    for i in range(len(g_list) - 1):
        g1 = g_list[i]
        g2 = g_list[i + 1]
        if math.isclose(g1["gap"], g2["gap"], abs_tol=1e-9):
            assert g1["element_name"] <= g2["element_name"]
        else:
            assert g1["gap"] >= g2["gap"]


# 6. p = lvl everywhere gives skill/knowledge readiness 1.0 and no gaps
@given(
    reqs=st.lists(req_strategy(), max_size=30),
)
@settings(max_examples=50)
def test_perfect_proficiency_gives_full_readiness_and_zero_gaps(
    reqs: list[dict[str, Any]],
) -> None:
    # Set proficiency for each element_name to the maximum lvl seen for that element across rows
    # so that p >= lvl for every requirement row containing that element_name
    prof: dict[str, float] = {}
    for r in reqs:
        elem = r["element_name"]
        prof[elem] = max(prof.get(elem, 0.0), float(r["lvl"]))

    skill_r = scoring.domain_readiness(reqs, prof, "skill")
    know_r = scoring.domain_readiness(reqs, prof, "knowledge")

    has_skills = any(r["domain"] == "skill" for r in reqs)
    has_know = any(r["domain"] == "knowledge" for r in reqs)

    if has_skills:
        assert math.isclose(skill_r, 1.0, abs_tol=1e-9)
    if has_know:
        assert math.isclose(know_r, 1.0, abs_tol=1e-9)

    # Gaps must be completely empty (since p >= lvl everywhere -> coverage == 1.0 -> 1-c == 0)
    g = scoring.gaps(reqs, prof)
    assert len(g) == 0


# 7. Unreliable rows get exactly half the weight of reliable rows with the same imp
@given(
    imp=st.floats(min_value=1.0, max_value=5.0),
)
@settings(max_examples=50)
def test_unreliable_half_weight(imp: float) -> None:
    cfg = ScoringConfig(gamma=1.5, unreliable_discount=0.5)
    w_rel = scoring.weight(imp, True, cfg)
    w_unrel = scoring.weight(imp, False, cfg)
    assert math.isclose(w_unrel, 0.5 * w_rel, abs_tol=1e-9)
