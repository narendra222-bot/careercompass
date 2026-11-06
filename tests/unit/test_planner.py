"""Phase 4b: deterministic planner on the real DevOps blend and catalog."""

from __future__ import annotations

from typing import Any

import pytest

from careercompass.planner import StudentProfile, build_plan

pytestmark = pytest.mark.needs_data


def _student(**kw: Any) -> StudentProfile:
    base: dict[str, Any] = {
        "name": "Ravi",
        "branch": "CSE",
        "year": 3,
        "hours_per_week": 10,
        "target_role": "devops engineer",
        "tech_mastery": {"Linux": 0.7, "Bash": 0.6, "git": 0.7, "Python": 0.5, "Kafka": 0.4},
    }
    base.update(kw)
    return StudentProfile(**base)


@pytest.fixture(scope="module")
def con() -> Any:
    from careercompass import db

    return db.connect()


def _all_items(plan: dict[str, Any]) -> list[dict[str, Any]]:
    return [it for ph in plan["phases"] for it in ph["items"]]


def test_plan_is_deterministic(con: Any) -> None:
    assert build_plan(_student(), con) == build_plan(_student(), con)


def test_prereqs_scheduled_before_dependents(con: Any) -> None:
    items = _all_items(build_plan(_student(), con))
    pos = {it["id"]: n for n, it in enumerate(items)}
    for it in items:
        for p in it["prereqs"]:
            assert pos[p] < pos[it["id"]], f"{p} must precede {it['id']}"


def test_known_tools_are_skipped_and_unknown_reported(con: Any) -> None:
    plan = build_plan(_student(), con)
    ids = {it["id"] for it in _all_items(plan)}
    assert {"linux-fundamentals", "git-version-control", "bash-scripting"} <= set(
        plan["skipped_known"]
    )
    assert not ids & set(plan["skipped_known"])
    assert plan["unmatched_tech"] == ["Kafka"]

    fresh = build_plan(_student(tech_mastery={}), con)
    fresh_ids = {it["id"] for it in _all_items(fresh)}
    assert "linux-fundamentals" in fresh_ids
    assert fresh["total_hours"] > plan["total_hours"]


def test_every_project_is_a_phase_milestone(con: Any) -> None:
    plan = build_plan(_student(), con)
    projects = [it["id"] for it in _all_items(plan) if it["kind"] == "project"]
    assert len(projects) >= 3
    assert projects == [ph["milestone"] for ph in plan["phases"] if ph["milestone"]]


@pytest.mark.parametrize("view", ["core", "onet"])
def test_readiness_never_decreases(con: Any, view: str) -> None:
    plan = build_plan(_student(), con)
    seq = [plan["baseline_readiness"][view]["overall"]] + [
        ph["readiness_after"][view]["overall"] for ph in plan["phases"]
    ]
    assert all(b >= a for a, b in zip(seq, seq[1:], strict=False))
    assert seq[-1] > seq[0]


def test_core_readiness_tracks_what_the_plan_teaches(con: Any) -> None:
    plan = build_plan(_student(tech_mastery={}), con)
    base, final = plan["baseline_readiness"], plan["final_readiness"]
    print(
        f"\ncore {base['core']['overall']:.3f} -> {final['core']['overall']:.3f} | "
        f"onet {base['onet']['overall']:.3f} -> {final['onet']['overall']:.3f}"
    )
    assert base["core"]["tech"] == 0.0  # no declared tools
    core_gain = final["core"]["overall"] - base["core"]["overall"]
    onet_gain = final["onet"]["overall"] - base["onet"]["overall"]
    # the full O*NET view includes ~540 untaught tools and abilities, so it must move less
    assert core_gain > onet_gain > 0
    assert final["core"]["overall"] > final["onet"]["overall"]


def test_weeks_follow_hours(con: Any) -> None:
    plan = build_plan(_student(), con)
    assert plan["total_hours"] == sum(it["hours"] for it in _all_items(plan))
    assert plan["total_weeks"] == -(-plan["total_hours"] // 10)
    doubled = build_plan(_student(hours_per_week=20), con)
    assert doubled["total_weeks"] == -(-doubled["total_hours"] // 20)
    assert doubled["total_weeks"] < plan["total_weeks"]


def test_infeasible_deadline_reports_fix(con: Any) -> None:
    plan = build_plan(_student(max_weeks=20), con)
    assert plan["feasible"] is False
    fix = plan["feasibility_fix"]
    assert fix["needed_hours_per_week_for_max_weeks"] * 20 >= plan["total_hours"]
    ok = build_plan(_student(max_weeks=fix["needed_weeks_at_current_hours"]), con)
    assert ok["feasible"] is True


def test_zero_hours_rejected(con: Any) -> None:
    with pytest.raises(ValueError):
        build_plan(_student(hours_per_week=0), con)


SAMPLES = [
    ("devops engineer", "CSE", 3, {"Linux": 0.7, "Bash": 0.6, "git": 0.7, "Python": 0.5}),
    ("data analyst", "B.Com", 3, {"Excel": 0.5}),
    ("machine learning engineer", "CSE", 4, {"Python": 0.6, "git": 0.6, "SQL": 0.4, "Linux": 0.6}),
]


@pytest.mark.parametrize(("role", "branch", "year", "mastery"), SAMPLES)
def test_three_sample_roadmaps_are_valid(
    con: Any, role: str, branch: str, year: int, mastery: dict[str, float]
) -> None:
    from careercompass import catalog, schemas
    from careercompass.planner import generate_roadmap

    st = _student(target_role=role, branch=branch, year=year, tech_mastery=mastery)
    r = generate_roadmap(st, con)
    assert schemas.verify(r, catalog.load()) == []
    assert r.unmatched_tech == []
    assert r.final_readiness.core.overall > r.baseline_readiness.core.overall + 0.3
    # first milestone is the project reachable with the fewest total hours from scratch
    cat = catalog.load()
    planned = {n.id for n in r.nodes if n.kind in ("course", "project")}

    def from_scratch(pid: str) -> float:
        need = catalog.prerequisite_closure(cat, {pid}) & planned
        return sum(cat.by_id()[i].hours for i in need)

    milestones = [p.milestone for p in r.phases if p.milestone]
    assert from_scratch(milestones[0]) == min(from_scratch(m) for m in milestones)


def test_ml_skips_linux_when_known_even_without_bash(con: Any) -> None:
    st = _student(
        target_role="machine learning engineer", tech_mastery={"Linux": 0.6, "Python": 0.6}
    )
    plan = build_plan(st, con)
    assert "linux-fundamentals" in plan["skipped_known"]


def test_ml_ships_tabular_project_before_finetune(con: Any) -> None:
    plan = build_plan(_student(target_role="machine learning engineer", tech_mastery={}), con)
    ms = [ph["milestone"] for ph in plan["phases"] if ph["milestone"]]
    assert ms.index("proj-tabular-ml") < ms.index("proj-dl-finetune")


def test_bcom_starts_lower_than_cse_for_data_analyst(con: Any) -> None:
    bcom = build_plan(_student(target_role="data analyst", branch="B.Com", tech_mastery={}), con)
    cse = build_plan(_student(target_role="data analyst", branch="CSE", tech_mastery={}), con)
    b, c = bcom["baseline_readiness"]["core"], cse["baseline_readiness"]["core"]
    print(f"\nDA baseline core: B.Com {b['overall']:.3f} vs CSE {c['overall']:.3f}")
    assert b["skills"] < c["skills"]
