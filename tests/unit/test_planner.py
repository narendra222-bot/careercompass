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


def test_readiness_never_decreases(con: Any) -> None:
    plan = build_plan(_student(), con)
    seq = [plan["baseline_readiness"]["overall"]] + [
        ph["readiness_after"]["overall"] for ph in plan["phases"]
    ]
    assert all(b >= a for a, b in zip(seq, seq[1:], strict=False))
    assert seq[-1] > seq[0]


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
