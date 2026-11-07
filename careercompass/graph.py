"""LangGraph orchestration: free text in, validated roadmap (or questions) out.

    intake ──► gate ──(missing / unsupported)──► ask_human ──► END (needs_input)
                 │
                 ▼
            planner ──► validate ──(catalog violation)──► fail ──► END (error)
                 ▲          │
                 │   (deadline infeasible, replans < MAX_REPLANS)
                 └── adjust ◄┘
                            │ (ok)
                            ▼
                         explain ──► END (ok)

Self-correction: an infeasible deadline is never "fixed" by silently raising the
student's hours. `adjust` keeps their hours, plans at that pace, records the change,
and attaches a question so a human can accept it or choose more hours instead.
Human-in-the-loop: missing required fields or an unsupported role stop the graph with
questions; the caller re-runs with `answers` merged over the parsed text.
"""

from __future__ import annotations

from typing import Any, Literal, TypedDict

from langgraph.graph import END, StateGraph

from careercompass import catalog, explain, intake, schemas
from careercompass.llm import LLM, get_llm
from careercompass.planner import StudentProfile, build_plan, load_role_profile

MAX_REPLANS = 2


class State(TypedDict, total=False):
    text: str
    profile: dict[str, Any]
    answers: dict[str, Any]
    draft: dict[str, Any]
    intake_source: str
    missing: list[str]
    questions: list[str]
    student: dict[str, Any]
    plan: dict[str, Any]
    roadmap: dict[str, Any]
    problems: list[str]
    adjustments: list[str]
    replans: int
    explanation_source: str
    status: Literal["ok", "needs_input", "error"]
    trace: list[str]


def _t(state: State, node: str) -> list[str]:
    return [*state.get("trace", []), node]


def build_graph(llm: LLM | None, con: Any = None) -> Any:
    from careercompass import db

    con = con or db.connect()

    def node_intake(state: State) -> State:
        if (given := state.get("profile")) is not None:
            # the chat agent already read the conversation; trust its validated profile
            parsed = {
                "draft": intake.IntakeDraft.model_validate(given),
                "source": state.get("intake_source") or "agent",
            }
        else:
            parsed = intake.parse_intake(state.get("text", ""), llm)
        data = parsed["draft"].model_dump()
        answers = {k: v for k, v in (state.get("answers") or {}).items() if v not in (None, "")}
        if "tech_mastery" in answers:
            data["tech_mastery"] = {**data["tech_mastery"], **answers.pop("tech_mastery")}
        data.update(answers)
        draft = intake.IntakeDraft.model_validate(data)
        role = intake.normalize_role(draft.target_role) or draft.target_role
        draft = draft.model_copy(update={"target_role": role})
        missing = [f for f in intake.REQUIRED if getattr(draft, f) in (None, "")]
        roles = ", ".join(sorted(intake.supported_roles()))
        questions = [
            intake.QUESTIONS[f] + (f" Supported: {roles}." if f == "target_role" else "")
            for f in missing
        ]
        if draft.target_role and draft.target_role not in intake.supported_roles():
            missing.append("target_role")
            questions.append(
                f"'{draft.target_role}' is not supported yet. Choose one of: " + roles + "."
            )
        return {
            "draft": draft.model_dump(),
            "intake_source": parsed["source"],
            "missing": missing,
            "questions": questions,
            "trace": _t(state, "intake"),
        }

    def node_ask_human(state: State) -> State:
        return {"status": "needs_input", "trace": _t(state, "ask_human")}

    def node_plan(state: State) -> State:
        d = state["student"] if "student" in state else state["draft"]
        student = StudentProfile(
            name=d.get("name") or "Student",
            branch=d["branch"],
            year=int(d["year"]),
            hours_per_week=float(d["hours_per_week"]),
            target_role=d["target_role"],
            tech_mastery=dict(d.get("tech_mastery") or {}),
            max_weeks=d.get("max_weeks"),
        )
        return {
            "student": dict(student.__dict__),
            "plan": build_plan(student, con),
            "trace": _t(state, "planner"),
        }

    def node_validate(state: State) -> State:
        cat = catalog.load()
        profile = load_role_profile(state["plan"]["role"])
        titles = {s["soc"]: s.get("title", s["soc"]) for s in profile["socs"]}
        try:
            r = schemas.build_roadmap(state["plan"], cat, titles)
            problems = schemas.verify(r, cat)
        except ValueError as exc:  # pydantic ValidationError subclasses ValueError
            return {"problems": [str(exc)], "trace": _t(state, "validate")}
        return {
            "roadmap": r.model_dump(mode="json"),
            "problems": problems,
            "trace": _t(state, "validate"),
        }

    def node_adjust(state: State) -> State:
        st = dict(state["student"])
        fix = state["plan"]["feasibility_fix"] or {}
        note = (
            f"A {st['max_weeks']}-week deadline needs about "
            f"{fix.get('needed_hours_per_week_for_max_weeks')} h/week. Kept your "
            f"{st['hours_per_week']:g} h/week and extended the plan to "
            f"{fix.get('needed_weeks_at_current_hours')} weeks."
        )
        # Re-plan at the student's own pace; optional items stay dropped.
        st["max_weeks"] = fix.get("needed_weeks_at_current_hours")
        return {
            "student": st,
            "replans": state.get("replans", 0) + 1,
            "adjustments": [*state.get("adjustments", []), note],
            "questions": [
                *state.get("questions", []),
                "Your deadline was extended to fit your weekly hours. Reply with more "
                f"hours per week (about {fix.get('needed_hours_per_week_for_max_weeks')}) "
                "if you must keep the original deadline.",
            ],
            "trace": _t(state, "adjust"),
        }

    def node_explain(state: State) -> State:
        r = schemas.Roadmap.model_validate(state["roadmap"])
        text, source = explain.explain(r, llm)
        r = r.model_copy(update={"explanation": text})
        # explanation must not have broken the contract
        assert schemas.verify(r, catalog.load()) == []
        return {
            "roadmap": r.model_dump(mode="json"),
            "explanation_source": source,
            "status": "ok",
            "trace": _t(state, "explain"),
        }

    def node_fail(state: State) -> State:
        return {"status": "error", "trace": _t(state, "fail")}

    def after_intake(state: State) -> str:
        return "ask_human" if state["missing"] else "plan"

    def after_validate(state: State) -> str:
        if state.get("problems"):
            return "fail"
        plan = state["plan"]
        if not plan["feasible"] and state.get("replans", 0) < MAX_REPLANS:
            return "adjust"
        return "explain"

    g: StateGraph = StateGraph(State)
    g.add_node("intake", node_intake)
    g.add_node("ask_human", node_ask_human)
    g.add_node("planner", node_plan)
    g.add_node("validate", node_validate)
    g.add_node("adjust", node_adjust)
    g.add_node("explain", node_explain)
    g.add_node("fail", node_fail)
    g.set_entry_point("intake")
    g.add_conditional_edges("intake", after_intake, {"ask_human": "ask_human", "plan": "planner"})
    g.add_edge("ask_human", END)
    g.add_edge("planner", "validate")
    g.add_conditional_edges(
        "validate", after_validate, {"fail": "fail", "adjust": "adjust", "explain": "explain"}
    )
    g.add_edge("adjust", "planner")
    g.add_edge("explain", END)
    g.add_edge("fail", END)
    return g.compile()


def run(
    text: str,
    answers: dict[str, Any] | None = None,
    llm: LLM | None | Literal["auto"] = "auto",
    con: Any = None,
) -> State:
    """One-shot entry point. llm="auto" uses the configured provider if a key is set."""
    model = get_llm() if llm == "auto" else llm
    result: State = build_graph(model, con).invoke({"text": text, "answers": answers or {}})
    return result
