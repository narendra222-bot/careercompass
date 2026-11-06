"""Phase 6: intake parsing, explanation guardrails and the LangGraph flow.

No network: a FakeLLM stands in for Groq so tests spend no quota.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from careercompass import explain, intake
from careercompass.llm import LLMError, parse_json

RAVI = (
    "Hi I am Ravi, 3rd year CSE student. I can spend 10 hours per week. "
    "I want to become a DevOps engineer. I know Linux and Git, strong in Python."
)


class FakeLLM:
    provider = "fake"

    def __init__(self, intake_json: Any = None, explanation: str | None = None) -> None:
        self.intake_json = intake_json
        self.explanation = explanation
        self.prompts: list[str] = []

    def chat(self, system: str, user: str, *, smart: bool = False, json_mode: bool = False) -> str:
        self.prompts.append(user)
        if json_mode:
            if isinstance(self.intake_json, Exception):
                raise self.intake_json
            return (
                self.intake_json
                if isinstance(self.intake_json, str)
                else json.dumps(self.intake_json)
            )
        if self.explanation is None:
            raise LLMError("down")
        return self.explanation


# ---------------- intake (pure) ----------------


def test_rules_extract_ravi() -> None:
    d = intake.extract_rules(RAVI)
    assert (d.branch, d.year, d.hours_per_week, d.target_role) == ("CSE", 3, 10, "devops engineer")
    assert d.name == "Ravi"
    assert d.tech_mastery == {"Python": 0.8, "Linux": 0.5, "Git": 0.5}


@pytest.mark.parametrize(
    ("text", "field", "value"),
    [
        ("B.Com final year", "year", 4),
        ("bcom 2nd yr", "branch", "B.Com"),
        ("2 hours a day", "hours_per_week", 14.0),
        ("need it within 6 months", "max_weeks", 24),
        ("aiming for ML engineer", "target_role", "machine learning engineer"),
        ("I want to be a BI analyst", "target_role", "data analyst"),
    ],
)
def test_rules_fields(text: str, field: str, value: Any) -> None:
    assert getattr(intake.extract_rules(text), field) == value


def test_tools_are_case_insensitive_and_unique() -> None:
    d = intake.extract_rules("I know excel, EXCEL and sql")
    assert {k.lower() for k in d.tech_mastery} == {"excel", "sql"}
    assert len(d.tech_mastery) == 2


def test_missing_fields_become_questions() -> None:
    out = intake.parse_intake("B.Com student who likes data analyst roles")
    assert set(out["missing"]) == {"year", "hours_per_week"}
    assert len(out["questions"]) == 2


def test_llm_draft_merges_and_bad_values_are_discarded() -> None:
    llm = FakeLLM(
        {
            "branch": "CSE",
            "year": 3,
            "hours_per_week": 999,
            "target_role": "devops",
            "tech_mastery": {"Docker": 0.3, "Kafka": "lots"},
            "name": "Ravi",
        }
    )
    out = intake.parse_intake("3rd year cse, devops, 10 hours per week", llm)
    # hours 999 is out of range -> whole LLM draft rejected, rules win
    assert out["draft"].hours_per_week == 10
    assert out["source"] == "rules"

    llm = FakeLLM(
        {
            "branch": "CSE",
            "year": 3,
            "hours_per_week": 12,
            "target_role": "devops",
            "tech_mastery": {"Docker": 0.3, "Kafka": "lots"},
        }
    )
    out = intake.parse_intake("cse student, wants devops", llm)
    assert out["source"] == "rules+fake"
    assert out["draft"].hours_per_week == 12
    assert out["draft"].target_role == "devops engineer"
    assert out["draft"].tech_mastery == {"Docker": 0.3}


def test_llm_never_sees_student_name() -> None:
    llm = FakeLLM({"name": "Ravi"})
    intake.parse_intake(RAVI, llm)
    out = intake.parse_intake(RAVI, llm)
    assert out["draft"].name == "Ravi"  # name comes from rules only


def test_llm_garbage_falls_back_to_rules() -> None:
    for bad in ["not json at all", LLMError("429")]:
        out = intake.parse_intake(RAVI, FakeLLM(bad))
        assert out["source"] == "rules"
        assert out["draft"].target_role == "devops engineer"


def test_parse_json_handles_code_fence() -> None:
    assert parse_json('```json\n{"a": 1}\n```') == {"a": 1}
    with pytest.raises(ValueError):
        parse_json("[1, 2]")


# ---------------- graph (needs O*NET data) ----------------


@pytest.fixture(scope="module")
def con() -> Any:
    from careercompass import db

    return db.connect()


@pytest.mark.needs_data
def test_happy_path_template(con: Any) -> None:
    from careercompass.graph import run

    s = run(RAVI, llm=None, con=con)
    assert s["status"] == "ok"
    assert s["trace"] == ["intake", "planner", "validate", "explain"]
    assert s["explanation_source"] == "template"
    assert "Devops Engineer" in s["roadmap"]["explanation"]


@pytest.mark.needs_data
def test_human_in_the_loop_resume(con: Any) -> None:
    from careercompass.graph import run

    text = "I'm doing B.Com final year, want to be a data analyst. I know Excel."
    first = run(text, llm=None, con=con)
    assert first["status"] == "needs_input"
    assert first["missing"] == ["hours_per_week"]
    second = run(text, answers={"hours_per_week": 8}, llm=None, con=con)
    assert second["status"] == "ok"
    assert second["roadmap"]["student"]["branch"] == "B.Com"


@pytest.mark.needs_data
def test_unsupported_role_lists_supported_ones(con: Any) -> None:
    from careercompass.graph import run

    s = run("2nd year ECE, 6 hours a week, want to be a game developer", llm=None, con=con)
    assert s["status"] == "needs_input"
    assert any("data analyst" in q for q in s["questions"])


@pytest.mark.needs_data
def test_infeasible_deadline_self_corrects(con: Any) -> None:
    from careercompass.graph import run

    text = "4th year CSE, 12 hrs a week, ML engineer, know python, sql, git. Need it in 4 months."
    s = run(text, llm=None, con=con)
    assert s["status"] == "ok"
    assert s["trace"].count("planner") == 2 and "adjust" in s["trace"]
    r = s["roadmap"]
    assert r["feasibility"]["feasible"] is True
    assert r["student"]["hours_per_week"] == 12  # never silently raised
    assert str(r["feasibility"]["total_weeks"]) in s["adjustments"][0]
    assert any("hours per week" in q for q in s["questions"])


@pytest.mark.needs_data
def test_llm_explanation_used_and_guarded(con: Any) -> None:
    from careercompass.graph import run

    good = "Start with containers because every later tool builds on them. " * 6
    s = run(RAVI, llm=FakeLLM({}, good), con=con)
    assert s["explanation_source"] == "fake"
    assert s["roadmap"]["explanation"] == good.strip()

    sneaky = good + " Also take https://scam-course.example/ for 50% off."
    s = run(RAVI, llm=FakeLLM({}, sneaky), con=con)
    assert s["explanation_source"] == "template"
    assert "scam" not in s["roadmap"]["explanation"]

    s = run(RAVI, llm=FakeLLM({}, None), con=con)  # provider down
    assert s["status"] == "ok" and s["explanation_source"] == "template"


@pytest.mark.needs_data
def test_explanation_facts_exclude_personal_data(con: Any) -> None:
    from careercompass.graph import run
    from careercompass.schemas import Roadmap

    s = run(RAVI, llm=None, con=con)
    facts = json.dumps(explain.facts(Roadmap.model_validate(s["roadmap"])))
    assert "Ravi" not in facts
