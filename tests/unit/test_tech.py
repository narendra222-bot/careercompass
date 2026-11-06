"""Phase 3c: per-technology scoring, name normalization, curated lists, NON_PLAN_ELEMENTS."""

from __future__ import annotations

import math
from typing import Any

import pytest

from careercompass import scoring, tech_catalog
from careercompass.tech_catalog import canonical_tech, resolve

ALPHAS = {"15-1252.00": 0.25, "15-1299.08": 0.45, "15-1244.00": 0.30}
CAD_TERMS = ("cad", "revit", "autocad")


# ---------- pure unit tests ----------


def test_canonical_tech() -> None:
    assert canonical_tech("IBM Terraform") == "terraform"
    assert canonical_tech("Jenkins CI") == "jenkins"
    assert canonical_tech("Amazon Web Services AWS software") == "aws"
    assert canonical_tech("  docker  ") == "docker"
    # prefix strip must not collapse distinct products
    assert canonical_tech("Red Hat Enterprise Linux") == "enterprise linux"
    assert canonical_tech("GitHub") != canonical_tech("GitHub Actions")


def test_resolve_exact_alias_none() -> None:
    ex = [
        "Docker",
        "IBM Terraform",
        "Ansible software",
        "Red Hat Ansible Engine",
        "GitHub",
        "Linux",
        "Red Hat Enterprise Linux",
    ]
    assert resolve("docker", ex) == ("exact", ["Docker"])
    assert resolve("Terraform", ex) == ("alias", ["IBM Terraform"])
    assert resolve("Ansible", ex) == ("alias", ["Ansible software", "Red Hat Ansible Engine"])
    assert resolve("Linux", ex) == ("exact", ["Linux"])
    assert resolve("GitHub Actions", ex) == ("NONE", [])
    assert resolve("Helm", ex) == ("NONE", [])


def _toy() -> dict[str, list[dict[str, Any]]]:
    return {
        "A": [
            {"example": "Docker", "commodity_title": "App server", "hot": True, "in_demand": True},
            {
                "example": "Autodesk AutoCAD",
                "commodity_title": "CAD",
                "hot": False,
                "in_demand": False,
            },
            {
                "example": "IBM Terraform",
                "commodity_title": "Config",
                "hot": True,
                "in_demand": False,
            },
        ],
        "B": [
            {"example": "Docker", "commodity_title": "App server", "hot": True, "in_demand": False},
            {
                "example": "Microsoft Excel",
                "commodity_title": "Spreadsheet",
                "hot": True,
                "in_demand": False,
            },
        ],
    }


def test_weights_sum_to_one_and_curated_dominates() -> None:
    res = scoring.tech_gap_items(
        _toy(), {"A": 0.5, "B": 0.5}, {}, curated=["Docker", "Terraform", "Helm"]
    )
    items = res["items"]
    assert math.isclose(sum(it["weight"] for it in items), 1.0, abs_tol=1e-9)
    by = {it["example"]: it for it in items}
    assert by["Helm"]["source"] == "curated_only"
    assert by["Terraform"]["onet_examples"] == ["IBM Terraform"]
    assert "IBM Terraform" not in by  # claimed by curated item, not duplicated in tail
    assert [it["curated"] for it in items[:3]] == [True, True, True]


def test_no_curated_uses_onet_weights_only() -> None:
    res = scoring.tech_gap_items(_toy(), {"A": 0.5, "B": 0.5}, {})
    by = {it["example"]: it for it in res["items"]}
    assert math.isclose(sum(it["weight"] for it in res["items"]), 1.0)
    # Docker is in both occupations, AutoCAD in one -> Docker outranks AutoCAD
    assert by["Docker"]["weight"] > by["Autodesk AutoCAD"]["weight"]


def test_mastery_normalization_and_unmatched() -> None:
    res = scoring.tech_gap_items(
        _toy(),
        {"A": 0.5, "B": 0.5},
        {" docker ": 0.5, "terraform": 1.0, "Kubernetes": 0.3},
        curated=["Docker", "Terraform"],
    )
    by = {it["example"]: it for it in res["items"]}
    assert by["Docker"]["mastery"] == 0.5
    assert by["Terraform"]["gap"] == 0.0
    assert res["matched"] == {" docker ": "Docker", "terraform": "Terraform"}
    assert res["unmatched"] == ["Kubernetes"]


def test_alpha_validation() -> None:
    with pytest.raises(ValueError, match="must sum to 1"):
        scoring.tech_gap_items(_toy(), {"A": 0.5, "B": 0.4}, {})


def test_plan_gaps_excludes_non_plan() -> None:
    reqs = [
        {
            "domain": "knowledge",
            "element_name": "Customer and Personal Service",
            "imp": 4.0,
            "lvl": 4.0,
            "reliable": True,
        },
        {
            "domain": "skill",
            "element_name": "Programming",
            "imp": 4.0,
            "lvl": 4.0,
            "reliable": True,
        },
    ]
    assert {g["element_name"] for g in scoring.gaps(reqs, {})} == {
        "Customer and Personal Service",
        "Programming",
    }
    assert [g["element_name"] for g in scoring.plan_gaps(reqs, {})] == ["Programming"]


# ---------- O*NET data tests ----------


def _print_items(title: str, items: list[dict[str, Any]], n: int = 20) -> None:
    print(f"\n{'=' * 100}\n{title}\n{'=' * 100}")
    print(
        f"{'#':>3}  {'example':<34} {'src':<12} {'n':>2} "
        f"{'weight':>7} {'mast':>5} {'gap':>7}  commodity"
    )
    for i, it in enumerate(items[:n], 1):
        print(
            f"{i:>3}  {it['example']:<34} {it['source']:<12} {it['in_n_occupations']:>2} "
            f"{it['weight']:>7.4f} {it['mastery']:>5.2f} {it['gap']:>7.4f}  {it['commodity_title']}"
        )


@pytest.mark.needs_data
def test_devops_tech_gaps_on_onet() -> None:
    from careercompass import db

    con = db.connect()
    techs_by_soc = {s: db.get_tech(con, s) for s in ALPHAS}
    examples = sorted({t["example"] for ts in techs_by_soc.values() for t in ts})
    curated = tech_catalog.load_curated("devops engineer")

    # Curated match table
    table = tech_catalog.match_curated(curated, examples)
    print(f"\n{'=' * 100}\nCURATED MATCH TABLE (devops engineer)\n{'=' * 100}")
    for row in table:
        print(f"  {row['curated']:<16} {row['match']:<6} {', '.join(row['examples']) or '-'}")
    none = sorted(r["curated"] for r in table if r["match"] == "NONE")
    print(f"  NONE ({len(none)}): {none}")
    # These exist in O*NET 31.0 for the blend; a NONE here means matching broke.
    must_match = {
        "Linux",
        "Bash",
        "Git",
        "Docker",
        "Python",
        "Kubernetes",
        "Prometheus",
        "Terraform",
        "Jenkins",
        "Ansible",
        "Grafana",
        "AWS",
    }
    assert not (must_match & set(none)), f"Expected O*NET matches missing: {must_match & set(none)}"

    # Zero mastery
    base = scoring.tech_gap_items(techs_by_soc, ALPHAS, {}, curated=curated)
    items = base["items"]
    assert math.isclose(sum(it["weight"] for it in items), 1.0, abs_tol=1e-9)
    _print_items("TOP 20 TECH GAPS - CSE year 3, zero mastery", items)
    print(f"  total items: {len(items)}")

    top10_curated = sum(1 for it in items[:10] if it["curated"])
    assert top10_curated >= 6, f"only {top10_curated} curated items in top 10"
    cad = [
        it["example"]
        for it in items[:20]
        if any(term in it["example"].lower() for term in CAD_TERMS)
    ]
    assert not cad, f"CAD items in top 20: {cad}"

    # With mastery
    mastery = {"Linux": 0.6, "Git": 0.7, "Docker": 0.0}
    after = scoring.tech_gap_items(techs_by_soc, ALPHAS, mastery, curated=curated)
    _print_items("TOP 20 TECH GAPS - mastery Linux 0.6, Git 0.7, Docker 0", after["items"])
    print(f"  matched: {after['matched']}")
    print(f"  unmatched: {after['unmatched']}")
    assert after["unmatched"] == []

    def gap_of(res: dict[str, Any], name: str) -> float:
        return next(it["gap"] for it in res["items"] if it["example"] == name)

    assert gap_of(after, "Linux") < gap_of(base, "Linux")
    assert gap_of(after, "Git") < gap_of(base, "Git")

    docker_up = scoring.tech_gap_items(techs_by_soc, ALPHAS, {"docker": 0.8}, curated=curated)
    assert gap_of(docker_up, "Docker") < gap_of(base, "Docker")


@pytest.mark.needs_data
def test_non_plan_elements_exist_in_onet() -> None:
    from careercompass import db

    con = db.connect()
    names = {r["element_name"] for s in ALPHAS for r in db.get_requirements(con, s)}
    missing = sorted(scoring.NON_PLAN_ELEMENTS - names)
    print(f"\nNON_PLAN_ELEMENTS: {sorted(scoring.NON_PLAN_ELEMENTS)}")
    print(f"missing from O*NET blend: {missing}")
    assert not missing, f"NON_PLAN_ELEMENTS not in O*NET: {missing}"
