"""Unit tests for deterministic scoring and gap analysis module."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import pytest

from careercompass import scoring
from careercompass.scoring import ScoringConfig


def test_weight_computation() -> None:
    """Test weight formula with reliable and unreliable elements."""
    cfg = ScoringConfig(gamma=1.5, unreliable_discount=0.5)

    # imp = 1.0 -> weight = 0
    assert scoring.weight(1.0, True, cfg) == 0.0
    assert scoring.weight(0.5, True, cfg) == 0.0

    # imp = 5.0, reliable: ((5-1)/4)^1.5 * 1.0 = 1.0^1.5 = 1.0
    assert math.isclose(scoring.weight(5.0, True, cfg), 1.0)
    # imp = 5.0, unreliable: 1.0 * 0.5 = 0.5
    assert math.isclose(scoring.weight(5.0, False, cfg), 0.5)

    # Exact halving
    imp_3 = 3.0
    w_rel = scoring.weight(imp_3, True, cfg)
    w_unrel = scoring.weight(imp_3, False, cfg)
    assert math.isclose(w_unrel, w_rel * 0.5)


def test_coverage_computation() -> None:
    """Test coverage for lvl <= 0.5 and p/lvl clamping."""
    # lvl <= 0.5 gives 1.0
    assert scoring.coverage(0.0, 0.4) == 1.0
    assert scoring.coverage(0.0, 0.5) == 1.0

    # lvl > 0.5
    assert scoring.coverage(0.0, 4.0) == 0.0
    assert math.isclose(scoring.coverage(2.0, 4.0), 0.5)
    assert math.isclose(scoring.coverage(4.0, 4.0), 1.0)
    assert math.isclose(scoring.coverage(6.0, 4.0), 1.0)  # clamped at 1.0


def test_blend_alphas_validation() -> None:
    """blend raises ValueError unless alphas sum to 1 and are >= 0."""
    req_set = [[{"domain": "skill", "element_name": "Coding", "imp": 4.0, "lvl": 4.0}]]

    with pytest.raises(ValueError, match="must sum to 1"):
        scoring.blend(req_set, [0.8])

    with pytest.raises(ValueError, match="must all be >= 0"):
        scoring.blend([req_set[0], req_set[0]], [-0.2, 1.2])

    with pytest.raises(ValueError, match="Length mismatch"):
        scoring.blend(req_set, [0.5, 0.5])


def test_blend_missing_and_reliability_rules() -> None:
    """Test missing element handling and alpha >= 0.10 reliability logic."""
    # Soc 1 (alpha 0.85): Python reliable=True
    # Soc 2 (alpha 0.08): Python reliable=False (alpha < 0.10, so should NOT spoil reliability)
    # Soc 3 (alpha 0.07): Python missing (counts as imp=1, lvl=0, excluded from reliability)
    soc1 = [{"domain": "skill", "element_name": "Python", "imp": 4.0, "lvl": 5.0, "reliable": True}]
    soc2 = [{"domain": "skill", "element_name": "Python", "imp": 3.0, "lvl": 3.0, "reliable": False}]
    soc3: list[dict[str, Any]] = []

    res = scoring.blend([soc1, soc2, soc3], [0.85, 0.08, 0.07])
    assert len(res) == 1
    item = res[0]
    assert item["element_name"] == "Python"
    # imp = 0.85*4.0 + 0.08*3.0 + 0.07*1.0 = 3.4 + 0.24 + 0.07 = 3.71
    assert math.isclose(item["imp"], 3.71)
    # lvl = 0.85*5.0 + 0.08*3.0 + 0.07*0.0 = 4.25 + 0.24 = 4.49
    assert math.isclose(item["lvl"], 4.49)
    # Reliable should be True because soc2 had alpha < 0.10 and soc3 had no row
    assert item["reliable"] is True

    # Now test when alpha >= 0.10 has reliable=False -> spoils reliability
    res2 = scoring.blend([soc1, soc2], [0.80, 0.20])
    assert res2[0]["reliable"] is False


def test_domain_readiness_edge_cases() -> None:
    """No rows -> 0.0; rows but sum(w)==0 -> 1.0."""
    # 1. No rows for domain
    empty_reqs: list[dict[str, Any]] = []
    assert scoring.domain_readiness(empty_reqs, {}, "skill") == 0.0

    # 2. Rows exist for domain, but all imp <= 1.0 (so w = 0)
    low_imp_reqs = [
        {"domain": "skill", "element_name": "Listening", "imp": 1.0, "lvl": 3.0, "reliable": True},
        {"domain": "skill", "element_name": "Reading", "imp": 0.8, "lvl": 4.0, "reliable": True},
    ]
    # sum(w) == 0 -> should return 1.0
    assert scoring.domain_readiness(low_imp_reqs, {}, "skill") == 1.0


def test_tech_readiness() -> None:
    """Test tech readiness weighting and grouping."""
    techs = [
        {"example": "Python", "commodity_title": "Programming", "hot": 1.0, "in_demand": 1.0},
        {"example": "Java", "commodity_title": "Programming", "hot": 0.0, "in_demand": 0.0},
    ]
    # Python: u = 1 + 0.5*1 + 0.5*1 = 2.0
    # Java: u = 1 + 0.5*0 + 0.5*0 = 1.0
    # Mastery: Python 1.0, Java 0.0 -> group mastery = (2.0*1.0 + 1.0*0.0) / 3.0 = 2/3
    # W = min(3, 3.0) = 3.0 -> readiness = 2/3
    mastery = {"Python": 1.0, "Java": 0.0}
    r = scoring.tech_readiness(techs, mastery)
    assert math.isclose(r, 2.0 / 3.0)


def test_estimate_proficiency() -> None:
    """Test estimate_proficiency with and without evidence, clamping."""
    elements = ["Python", "SQL", "Docker"]
    self_report = {"Python": 5.0, "SQL": 3.0}  # Docker defaults to 0
    evidence = {"Python": 0.8}                  # SQL has no evidence
    prior_cap = {"Python": 1.0, "SQL": 0.4}     # Docker cap defaults to 0.5

    # Python: self=5/5=1.0, cap=1.0 -> prior=1.0. evidence=0.8.
    # p = 7 * (0.65 * 0.8 + 0.35 * 1.0) = 7 * (0.52 + 0.35) = 7 * 0.87 = 6.09
    # SQL: self=3/5=0.6, cap=0.4 -> prior=0.4. No evidence.
    # p = 7 * 0.4 = 2.8
    # Docker: self=0, cap=0.5 -> prior=0. No evidence. p = 0.
    est = scoring.estimate_proficiency(self_report, evidence, prior_cap, elements, beta=0.65)
    assert math.isclose(est["Python"], 6.09)
    assert math.isclose(est["SQL"], 2.8)
    assert math.isclose(est["Docker"], 0.0)


def test_gaps_trainable_only() -> None:
    """gaps excludes ability rows and is sorted desc by gap, then element_name asc."""
    reqs = [
        {"domain": "ability", "element_name": "Vision", "imp": 4.5, "lvl": 5.0, "reliable": True},
        {"domain": "skill", "element_name": "Programming", "imp": 4.5, "lvl": 5.0, "reliable": True},
        {"domain": "knowledge", "element_name": "Computers", "imp": 4.5, "lvl": 5.0, "reliable": True},
    ]
    # Proficiency zero -> coverage zero -> gap = weight
    g = scoring.gaps(reqs, prof={})
    domains = [item["domain"] for item in g]
    assert "ability" not in domains
    assert len(g) == 2
    # Check sorting: identical gap, so tie-break by element_name asc ('Computers' before 'Programming')
    assert g[0]["element_name"] == "Computers"
    assert g[1]["element_name"] == "Programming"


@pytest.mark.needs_data
def test_baseline_readiness_and_sensitivity_on_onet() -> None:
    """needs_data: Blend of 15-1252.00 / 15-1299.08 / 15-1244.00 at p=0 and m=0."""
    from careercompass import db
    con = db.connect()

    socs = ["15-1252.00", "15-1299.08", "15-1244.00"]
    alphas = {"15-1252.00": 0.25, "15-1299.08": 0.45, "15-1244.00": 0.30}

    reqs_by_soc = {s: db.get_requirements(con, s, include_unreliable=True) for s in socs}
    tech_by_soc = {s: db.get_tech(con, s) for s in socs}

    blended_reqs = scoring.blend([reqs_by_soc[s] for s in socs], [alphas[s] for s in socs])
    blended_tech = scoring.blend_tech([tech_by_soc[s] for s in socs], [alphas[s] for s in socs])

    # Zero proficiency and zero mastery
    prof: dict[str, float] = {}
    mastery: dict[str, float] = {}

    baseline_r = scoring.readiness(blended_reqs, blended_tech, prof, mastery)

    # Assertions
    for dom, val in baseline_r.items():
        assert 0.0 <= val <= 1.0, f"Domain {dom} value {val} out of bounds"

    top_gaps = scoring.gaps(blended_reqs, prof)[:10]
    assert len(top_gaps) == 10
    assert all(g["domain"] != "ability" for g in top_gaps)

    # Print baseline readiness
    print("\n" + "=" * 80)
    print("BASELINE READINESS (Zero Proficiency & Zero Mastery)")
    print("=" * 80)
    for dom, score in baseline_r.items():
        print(f"  {dom:<15}: {score:.4f}")

    # Print top 10 gaps
    print("\n" + "=" * 80)
    print("TOP 10 GAPS (Blended 15-1252.00 / 15-1299.08 / 15-1244.00)")
    print("=" * 80)
    print(f"| {'Rank':<4} | {'Domain':<10} | {'Element Name':<32} | {'Imp':<5} | {'Lvl':<5} | {'Reliable':<8} | {'Gap':<7} |")
    print(f"|:-----|:-----------|:---------------------------------|:------|:------|:---------|:--------|")
    for idx, g in enumerate(top_gaps, 1):
        print(
            f"| {idx:<4} | {g['domain']:<10} | {g['element_name']:<32} | {g['imp']:<5.2f} | {g['lvl']:<5.2f} | {str(g['reliable']):<8} | {g['gap']:<7.4f} |"
        )

    # Sensitivity analysis
    sens = scoring.sensitivity(reqs_by_soc, alphas, prof, delta=0.10)
    assert 0.0 <= sens["min_jaccard"] <= 1.0
    if not math.isnan(sens["min_spearman"]):
        assert -1.0 <= sens["min_spearman"] <= 1.0

    print("\n" + "=" * 80)
    print("SENSITIVITY ANALYSIS SUMMARY (+/- 0.10 perturbation per occupation)")
    print("=" * 80)
    for p in sens["perturbations"]:
        sp_str = f"{p['spearman']:.4f}" if not math.isnan(p['spearman']) else "NaN"
        print(f"  Perturbation: {p['perturbation']:<24} | Jaccard: {p['jaccard']:.4f} | Spearman: {sp_str:<7} | Shared: {p['shared_count']}")
    print("-" * 80)
    min_sp_str = f"{sens['min_spearman']:.4f}" if not math.isnan(sens['min_spearman']) else "NaN"
    print(f"  Min Jaccard:  {sens['min_jaccard']:.4f}")
    print(f"  Min Spearman: {min_sp_str}")
    print("=" * 80)


def test_tech_gaps_functionality() -> None:
    """Test tech_gaps calculation, group_weight, raw_sum_u, and unmatched keys."""
    techs = [
        {"example": "Python", "commodity_title": "Programming Software", "hot": 1.0, "in_demand": 1.0},
        {"example": "Java", "commodity_title": "Programming Software", "hot": 0.0, "in_demand": 0.0},
        {"example": "Docker", "commodity_title": "Platform Software", "hot": 1.0, "in_demand": 0.0},
    ]
    # Mastery with varying case and whitespace + one unmatched key
    mastery = {
        "  python  ": 0.8,
        "UnknownTool": 0.5,
    }

    gaps_list, unmatched = scoring.tech_gaps(techs, mastery)
    assert len(unmatched) == 1
    assert unmatched[0] == "UnknownTool"

    # Programming Software:
    # Python: u = 2.0, m = 0.8. Java: u = 1.0, m = 0.0.
    # raw_sum_u = 3.0, W = 3.0. group_mastery = (2.0*0.8 + 1.0*0.0)/3.0 = 1.6/3.0 = 0.5333...
    # gap = 3.0 * (1 - 0.5333...) = 1.4
    # Platform Software:
    # Docker: u = 1.5, m = 0.0 -> raw_sum_u = 1.5, W = 1.5. group_mastery = 0.0 -> gap = 1.5
    assert len(gaps_list) == 2
    # Platform Software has gap 1.5, Programming Software has gap 1.4
    assert gaps_list[0]["commodity_title"] == "Platform Software"
    assert math.isclose(gaps_list[0]["gap"], 1.5)
    assert gaps_list[0]["raw_sum_u"] == 1.5
    assert gaps_list[0]["top_examples"] == ["Docker"]

    assert gaps_list[1]["commodity_title"] == "Programming Software"
    assert math.isclose(gaps_list[1]["gap"], 1.4)
    assert gaps_list[1]["raw_sum_u"] == 3.0
    # Python is hot, so appears before Java in top_examples
    assert gaps_list[1]["top_examples"] == ["Python", "Java"]


@pytest.mark.needs_data
def test_free_coverage_count() -> None:
    """Count and print how many blended elements have lvl <= 0.5 (free coverage)."""
    from careercompass import db
    con = db.connect()

    socs = ["15-1252.00", "15-1299.08", "15-1244.00"]
    alphas = {"15-1252.00": 0.25, "15-1299.08": 0.45, "15-1244.00": 0.30}
    reqs_by_soc = {s: db.get_requirements(con, s, include_unreliable=True) for s in socs}
    blended_reqs = scoring.blend([reqs_by_soc[s] for s in socs], [alphas[s] for s in socs])

    free_cov_elements = [r for r in blended_reqs if r["lvl"] <= 0.5]
    total_count = len(free_cov_elements)

    domain_counts: dict[str, int] = {}
    for r in free_cov_elements:
        domain_counts[r["domain"]] = domain_counts.get(r["domain"], 0) + 1

    print("\n" + "=" * 80)
    print("FREE COVERAGE ELEMENTS (lvl <= 0.5)")
    print("=" * 80)
    print(f"Total Blended Elements with lvl <= 0.5: {total_count}")
    print("Per-domain breakdown:")
    for dom, cnt in sorted(domain_counts.items()):
        print(f"  {dom:<15}: {cnt}")
    print("=" * 80)

    assert total_count >= 0


@pytest.mark.needs_data
def test_cse_student_readiness_and_gaps() -> None:
    """Test readiness and gaps for a CSE Year-3 student with zero tech mastery."""
    from careercompass import db, priors
    con = db.connect()

    socs = ["15-1252.00", "15-1299.08", "15-1244.00"]
    alphas = {"15-1252.00": 0.25, "15-1299.08": 0.45, "15-1244.00": 0.30}
    reqs_by_soc = {s: db.get_requirements(con, s, include_unreliable=True) for s in socs}
    tech_by_soc = {s: db.get_tech(con, s) for s in socs}

    blended_reqs = scoring.blend([reqs_by_soc[s] for s in socs], [alphas[s] for s in socs])
    blended_tech = scoring.blend_tech([tech_by_soc[s] for s in socs], [alphas[s] for s in socs])

    # Zero prior gaps for comparison
    zero_gaps = scoring.gaps(blended_reqs, prof={})[:10]
    zero_gap_sum = sum(g["gap"] for g in zero_gaps)
    zero_top10_names = [g["element_name"] for g in zero_gaps]

    # CSE Year 3 Student Seed Proficiency
    cse_branch = ("Engineering", "Computer Science and Engineering")
    prof = priors.seed_proficiency(cse_branch, 3, blended_reqs)
    mastery: dict[str, float] = {}

    cse_readiness = scoring.readiness(blended_reqs, blended_tech, prof, mastery)
    cse_trainable_gaps = scoring.gaps(blended_reqs, prof)[:10]
    cse_tech_gaps, unmatched = scoring.tech_gaps(blended_tech, mastery)

    cse_gap_sum = sum(g["gap"] for g in cse_trainable_gaps)
    cse_top10_names = [g["element_name"] for g in cse_trainable_gaps]

    # Assertions
    # 1. Soft-skill/trainable gaps shrink
    assert cse_gap_sum < zero_gap_sum, f"Expected {cse_gap_sum} < {zero_gap_sum}"
    # 2. At least 3 of the top-10 trainable gaps differ from zero-prior top-10
    differing_gaps = set(cse_top10_names) ^ set(zero_top10_names)
    assert len(differing_gaps) >= 3, f"Expected >= 3 differing elements, got {len(differing_gaps)}"
    # 3. Programming is NOT in the CSE top-10
    assert "Programming" not in cse_top10_names, f"Programming found in CSE top-10: {cse_top10_names}"

    print("\n" + "=" * 80)
    print("CSE YEAR-3 STUDENT: BASELINE READINESS (Zero Tech Mastery)")
    print("=" * 80)
    for dom, score in cse_readiness.items():
        print(f"  {dom:<15}: {score:.4f}")

    print("\n" + "=" * 80)
    print("CSE YEAR-3 STUDENT: TOP 10 TRAINABLE GAPS")
    print("=" * 80)
    print(f"| {'Rank':<4} | {'Domain':<10} | {'Element Name':<32} | {'Imp':<5} | {'Lvl':<5} | {'Reliable':<8} | {'Gap':<7} |")
    print(f"|:-----|:-----------|:---------------------------------|:------|:------|:---------|:--------|")
    for idx, g in enumerate(cse_trainable_gaps, 1):
        print(
            f"| {idx:<4} | {g['domain']:<10} | {g['element_name']:<32} | {g['imp']:<5.2f} | {g['lvl']:<5.2f} | {str(g['reliable']):<8} | {g['gap']:<7.4f} |"
        )

    print("\n" + "=" * 80)
    print(f"CSE YEAR-3 STUDENT: TOP 10 TECH GAPS (Total Commodity Groups: {len(cse_tech_gaps)})")
    print("=" * 80)
    print(f"| {'Rank':<4} | {'Commodity Title':<36} | {'Weight W':<9} | {'Raw Sum U':<10} | {'Mastery':<8} | {'Gap':<7} | {'Top Examples'}")
    print(f"|:-----|:-------------------------------------|:----------|:-----------|:---------|:--------|:-------------")
    for idx, tg in enumerate(cse_tech_gaps[:10], 1):
        ex_str = ", ".join(tg["top_examples"][:3])
        print(
            f"| {idx:<4} | {tg['commodity_title']:<36} | {tg['group_weight']:<9.2f} | {tg['raw_sum_u']:<10.2f} | {tg['mastery']:<8.2f} | {tg['gap']:<7.4f} | {ex_str}"
        )
    print(f"\nUnmatched mastery keys ({len(unmatched)}): {unmatched}")


@pytest.mark.needs_data
def test_bcom_student_readiness_and_gaps() -> None:
    """Test readiness and gaps for a B.Com Year-3 student with zero tech mastery."""
    from careercompass import db, priors
    con = db.connect()

    socs = ["15-1252.00", "15-1299.08", "15-1244.00"]
    alphas = {"15-1252.00": 0.25, "15-1299.08": 0.45, "15-1244.00": 0.30}
    reqs_by_soc = {s: db.get_requirements(con, s, include_unreliable=True) for s in socs}
    tech_by_soc = {s: db.get_tech(con, s) for s in socs}

    blended_reqs = scoring.blend([reqs_by_soc[s] for s in socs], [alphas[s] for s in socs])
    blended_tech = scoring.blend_tech([tech_by_soc[s] for s in socs], [alphas[s] for s in socs])

    # CSE gaps sum for comparison
    cse_branch = ("Engineering", "Computer Science and Engineering")
    cse_prof = priors.seed_proficiency(cse_branch, 3, blended_reqs)
    cse_top10_gaps = scoring.gaps(blended_reqs, cse_prof)[:10]
    cse_gap_sum = sum(g["gap"] for g in cse_top10_gaps)

    # B.Com Year 3 Student Seed Proficiency
    bcom_branch = ("Commerce", "General Commerce")
    bcom_prof = priors.seed_proficiency(bcom_branch, 3, blended_reqs)
    mastery: dict[str, float] = {}

    bcom_readiness = scoring.readiness(blended_reqs, blended_tech, bcom_prof, mastery)
    bcom_trainable_gaps = scoring.gaps(blended_reqs, bcom_prof)[:10]
    bcom_tech_gaps, unmatched = scoring.tech_gaps(blended_tech, mastery)

    bcom_gap_sum = sum(g["gap"] for g in bcom_trainable_gaps)
    bcom_top10_names = [g["element_name"] for g in bcom_trainable_gaps]

    # Assertions
    # 1. Programming IS in B.Com top-10
    assert "Programming" in bcom_top10_names, f"Programming missing from B.Com top-10: {bcom_top10_names}"
    # 2. B.Com top-10 gap sum is greater than CSE student's
    assert bcom_gap_sum > cse_gap_sum, f"Expected {bcom_gap_sum} > {cse_gap_sum}"

    print("\n" + "=" * 80)
    print("B.COM YEAR-3 STUDENT: BASELINE READINESS (Zero Tech Mastery)")
    print("=" * 80)
    for dom, score in bcom_readiness.items():
        print(f"  {dom:<15}: {score:.4f}")

    print("\n" + "=" * 80)
    print("B.COM YEAR-3 STUDENT: TOP 10 TRAINABLE GAPS")
    print("=" * 80)
    print(f"| {'Rank':<4} | {'Domain':<10} | {'Element Name':<32} | {'Imp':<5} | {'Lvl':<5} | {'Reliable':<8} | {'Gap':<7} |")
    print(f"|:-----|:-----------|:---------------------------------|:------|:------|:---------|:--------|")
    for idx, g in enumerate(bcom_trainable_gaps, 1):
        print(
            f"| {idx:<4} | {g['domain']:<10} | {g['element_name']:<32} | {g['imp']:<5.2f} | {g['lvl']:<5.2f} | {str(g['reliable']):<8} | {g['gap']:<7.4f} |"
        )

    print("\n" + "=" * 80)
    print(f"B.COM YEAR-3 STUDENT: TOP 10 TECH GAPS (Total Commodity Groups: {len(bcom_tech_gaps)})")
    print("=" * 80)
    print(f"| {'Rank':<4} | {'Commodity Title':<36} | {'Weight W':<9} | {'Raw Sum U':<10} | {'Mastery':<8} | {'Gap':<7} | {'Top Examples'}")
    print(f"|:-----|:-------------------------------------|:----------|:-----------|:---------|:--------|:-------------")
    for idx, tg in enumerate(bcom_tech_gaps[:10], 1):
        ex_str = ", ".join(tg["top_examples"][:3])
        print(
            f"| {idx:<4} | {tg['commodity_title']:<36} | {tg['group_weight']:<9.2f} | {tg['raw_sum_u']:<10.2f} | {tg['mastery']:<8.2f} | {tg['gap']:<7.4f} | {ex_str}"
        )
    print(f"\nUnmatched mastery keys ({len(unmatched)}): {unmatched}")

