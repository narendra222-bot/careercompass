"""Chat agent: understanding the conversation, deciding, talking, and the SSE stream.

No network: FakeLLM stands in for Groq; API tests run with the LLM disabled.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from careercompass import agent, chat
from careercompass.llm import LLMError


def convo(*users: str) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for i, u in enumerate(users):
        if i:
            out.append({"role": "assistant", "content": "ok"})
        out.append({"role": "user", "content": u})
    return out


class FakeLLM:
    provider = "fake"

    def __init__(self, profile: Any = None, reply: str | Exception = "Hi there!") -> None:
        self.profile = profile
        self.reply = reply
        self.prompts: list[str] = []

    def chat(self, system: str, user: str, *, smart: bool = False, json_mode: bool = False) -> str:
        self.prompts.append(user)
        if isinstance(self.profile, Exception):
            raise self.profile
        return self.profile if isinstance(self.profile, str) else json.dumps(self.profile)

    def stream(self, system: str, user: str, *, smart: bool = False) -> Iterator[str]:
        self.prompts.append(user)
        if isinstance(self.reply, Exception):
            raise self.reply
        yield from self.reply.split(" ")


# ---------------- understanding without an LLM ----------------


def test_greeting_is_not_a_profile() -> None:
    u = agent.understand(convo("hy buddy"), None)
    assert u.intent == "greeting"
    assert u.missing == ["branch", "year", "target_role", "hours_per_week"]
    reply = agent.template_reply(u, has_roadmap=False)
    assert "Tell me what you study" in reply


def test_junk_reply_is_not_saved() -> None:
    # "hi" answering four questions once became branch="hi", role="Hi"
    u = agent.understand(convo("hy buddy", "hi"), None, pending=list(agent.REQUIRED))
    assert u.profile.branch is None and u.profile.target_role is None


def test_rules_read_a_messy_intro() -> None:
    u = agent.understand(
        convo("i am venkat i am studying btech 4 year ai&ml so you prepare me"), None
    )
    assert (u.profile.branch, u.profile.year) == ("AI&ML", 4)
    assert u.missing == ["target_role", "hours_per_week"]
    assert agent.options_for(u)[0] == "Machine Learning Engineer"


def test_bare_number_answers_hours() -> None:
    msgs = convo("3rd year CSE, want to be a devops engineer", "around 10")
    u = agent.understand(msgs, None, pending=["hours_per_week"])
    assert u.profile.hours_per_week == 10 and u.missing == []


def test_later_message_wins() -> None:
    u = agent.understand(
        convo("3rd year CSE, 10 hours per week, devops", "make it 15 hours a week"), None
    )
    assert u.profile.hours_per_week == 15


# ---------------- understanding with an LLM ----------------


def test_llm_profile_is_validated_field_by_field() -> None:
    llm = FakeLLM(
        {
            "intent": "profile",
            "name": "Venkat",
            "branch": "AI&ML",
            "year": 4,
            "hours_per_week": 500,  # out of range -> dropped
            "target_role": "x" * 200,  # a whole sentence -> dropped
            "tech_mastery": {"Python": 0.5, "bad": "lots"},
        }
    )
    u = agent.understand(convo("i am venkat, btech 4th year ai&ml, i know python"), llm)
    p = u.profile
    assert (p.name, p.branch, p.year, p.hours_per_week, p.target_role) == (
        "Venkat",
        "AI&ML",
        4,
        None,
        None,
    )
    assert p.tech_mastery == {"Python": 0.5}
    assert u.source == "fake"


def test_llm_any_role_is_plannable() -> None:
    llm = FakeLLM({"intent": "profile", "branch": "AI&ML", "year": 4, "target_role": "AI engineer"})
    u = agent.understand(convo("ai engineer"), llm)
    assert u.custom_role == "ai engineer" and u.unsupported_role is None
    assert u.missing == ["hours_per_week"]
    assert agent.reply_context(u, None)["student"]["wants_role"] == "AI Engineer"


def test_sentence_is_not_a_role() -> None:
    llm = FakeLLM({"intent": "profile", "target_role": "i want something good in computers please"})
    u = agent.understand(convo("i want something good in computers please"), llm)
    assert u.custom_role is None and u.unsupported_role is not None


def test_llm_down_falls_back_to_rules() -> None:
    u = agent.understand(convo("3rd year CSE, 10 hours a week, devops"), FakeLLM(LLMError("down")))
    assert u.source == "rules" and u.missing == []


def test_plan_changed() -> None:
    u = agent.understand(convo("3rd year CSE, 10 hours a week, devops, I know Linux"), None)
    last = agent.plan_input(u)
    assert not agent.plan_changed(u, last)
    assert agent.plan_changed(u, {**last, "hours_per_week": 8})
    assert agent.plan_changed(u, {**last, "tech_mastery": {}})
    assert agent.plan_changed(u, None)


def test_talk_streams_and_falls_back() -> None:
    u = agent.understand(convo("hello"), None)
    assert "".join(
        agent.talk(convo("hello"), u, FakeLLM(reply="Hi Venkat, what do you study?"), None)
    ) == (
        "HiVenkat,whatdoyoustudy?"  # FakeLLM splits on spaces
    )
    fallback = "".join(agent.talk(convo("hello"), u, FakeLLM(reply=LLMError("down")), None))
    assert "Tell me what you study" in fallback


# ---------------- event stream through the API (rules only) ----------------


def events(body: str) -> list[tuple[str, dict[str, Any]]]:
    out = []
    for block in body.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines())
        out.append((lines["event"], json.loads(lines["data"])))
    return out


@pytest.fixture(scope="module")
def client() -> Any:
    from careercompass import api, db
    from careercompass.graph import build_graph

    con = db.connect()
    api._state.update({"con": con, "llm": None, "graph": build_graph(None, con)})
    old = chat.STREAM_DELAY_S
    chat.STREAM_DELAY_S = 0
    yield TestClient(api.app)
    chat.STREAM_DELAY_S = old
    api._state.clear()


def post(client: TestClient, body: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    resp = client.post("/api/chat", json=body)
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    return events(resp.text)


def reply_text(ev: list[tuple[str, dict[str, Any]]]) -> str:
    return "".join(d["delta"] for k, d in ev if k == "text")


@pytest.mark.needs_data
def test_greeting_turn(client: TestClient) -> None:
    ev = post(client, {"messages": convo("hy buddy")})
    done = ev[-1][1]
    assert done["status"] == "needs_input" and "roadmap" not in [k for k, _ in ev]
    assert "Tell me what you study" in reply_text(ev)
    assert done["options"] == agent.OPTIONS["branch"]


@pytest.mark.needs_data
def test_plan_then_question_then_change(client: TestClient) -> None:
    first = "3rd year CSE, 10 hours per week, want to be a DevOps engineer, I know Linux and Git"
    ev = post(client, {"messages": convo(first)})
    kinds = [k for k, _ in ev]
    assert kinds.count("roadmap") == 1 and kinds.index("roadmap") > kinds.index("text")
    done = ev[-1][1]
    assert done["status"] == "ok" and done["title"] == "DevOps Engineer roadmap"
    assert done["profile"]["hours_per_week"] == 10
    rm = next(d["roadmap"] for k, d in ev if k == "roadmap")

    # a question about the plan: no re-plan
    ev2 = post(
        client,
        {
            "messages": convo(first, "what is phase 2 about?"),
            "last_plan": done["profile"],
            "roadmap": rm,
        },
    )
    assert "roadmap" not in [k for k, _ in ev2] and ev2[-1][1]["status"] == "chat"

    # a change: re-plan
    ev3 = post(
        client,
        {
            "messages": convo(first, "I can study 15 hours a week now"),
            "last_plan": done["profile"],
            "roadmap": rm,
        },
    )
    assert [k for k, _ in ev3].count("roadmap") == 1
    assert ev3[-1][1]["profile"]["hours_per_week"] == 15
    assert reply_text(ev3).startswith("Done, I've updated your roadmap.")


@pytest.mark.needs_data
def test_chat_validation(client: TestClient) -> None:
    assert client.post("/api/chat", json={"messages": []}).status_code == 422
    assert client.post("/api/chat", json={}).status_code == 422
    big = {"messages": [{"role": "user", "content": "x" * 2001}]}
    assert client.post("/api/chat", json=big).status_code == 422
    only_assistant = post(client, {"messages": [{"role": "assistant", "content": "hi"}]})
    assert only_assistant[-1][0] == "error"


def test_same_plan() -> None:
    r = {"nodes": [{"id": "a", "start_week": 1, "end_week": 2}], "feasibility": {"total_weeks": 2}}
    assert chat.same_plan(r, json.loads(json.dumps(r)))
    assert not chat.same_plan(None, r)
    assert not chat.same_plan(r, {**r, "feasibility": {"total_weeks": 3}})


def test_name_is_capitalised() -> None:
    u = agent.understand(convo("hi"), FakeLLM({"intent": "greeting", "name": "venkat"}))
    assert u.profile.name == "Venkat"


def test_skills_must_come_from_the_student() -> None:
    llm = FakeLLM(
        {"intent": "question", "tech_mastery": {"Python": 0.5, "SQL": 0.5, "Node.js": 0.5}}
    )
    msgs = convo("i know nodejs", "what is in phase 2?")
    msgs.insert(1, {"role": "assistant", "content": "Your gaps are Python and SQL."})
    assert agent.understand(msgs, llm).profile.tech_mastery == {"Node.js": 0.5}


def test_user_report_ai_engineer_without_llm() -> None:
    """hi -> B.Tech CSE -> 4th year -> Ai engineer, with the LLM unavailable."""
    msgs = convo("hi", "B.Tech CSE", "4th year", "Ai engineer")
    u = agent.understand(msgs, None, pending=["target_role", "hours_per_week"])
    assert (u.profile.branch, u.profile.year) == ("CSE", 4)
    assert u.unsupported_role == "Ai engineer"  # remembered, not asked again
    reply = agent.template_reply(u, False, "Ai engineer")
    assert "**AI Engineer** I need my AI planner" in reply
    assert "Which job role do you want" not in reply


def test_template_acknowledges_what_was_said() -> None:
    u = agent.understand(convo("B.Tech CSE", "4th year"), None, pending=["year", "target_role"])
    assert agent.template_reply(u, False, "4th year").startswith("Got it, 4th year.")


def test_greeting_is_not_taken_as_a_role() -> None:
    u = agent.understand(convo("B.Tech CSE", "hello"), None, pending=["target_role"])
    assert u.profile.target_role is None


@pytest.mark.parametrize(
    ("reply", "pending", "field", "value"),
    [
        ("4th year", ["year", "target_role", "hours_per_week"], "hours_per_week", None),
        ("4th year", ["year", "target_role", "hours_per_week"], "year", 4),
        ("15 hours", ["target_role", "hours_per_week"], "target_role", None),
        ("15 hours", ["target_role", "hours_per_week"], "hours_per_week", 15.0),
        ("ok", ["target_role"], "target_role", None),
        ("what is that?", ["target_role"], "target_role", None),
    ],
)
def test_rules_short_replies(reply: str, pending: list[str], field: str, value: Any) -> None:
    u = agent.understand(convo("B.Tech CSE", reply), None, pending=pending)
    assert getattr(u.profile, field) == value


def test_rules_remember_an_earlier_free_text_role() -> None:
    msgs = [
        {"role": "user", "content": "B.Tech CSE 4th year"},
        {"role": "assistant", "content": "Which job role do you want?"},
        {"role": "user", "content": "Ai engineer"},
        {"role": "assistant", "content": "How many hours a week can you spend learning?"},
        {"role": "user", "content": "15 hours"},
    ]
    u = agent.understand(msgs, None, pending=["hours_per_week"])
    assert u.profile.target_role == "Ai engineer" and u.profile.hours_per_week == 15


def test_role_ideas_follow_the_students_field() -> None:
    ideas = agent.suggest_roles(agent.Profile(branch="CSE", interest="cyber security"))
    assert ideas[0] == "Cybersecurity Analyst" and len(ideas) == 4
    assert "Software Developer" in agent.suggest_roles(agent.Profile(branch="CSE"))
    # without the LLM only the curated roles can be planned
    curated = agent.suggest_roles(agent.Profile(branch="CSE", interest="security"), any_role=False)
    assert set(curated) == {"DevOps Engineer", "Data Analyst", "Machine Learning Engineer"}


def test_llm_role_ideas_come_from_the_conversation() -> None:
    llm = FakeLLM(
        {
            "intent": "profile",
            "branch": "ECE",
            "year": 2,
            "interest": "drones",
            "role_ideas": [
                "drone systems engineer",
                "Embedded Engineer",
                "embedded engineer",
                "I think you should try robotics",
                7,
            ],
        }
    )
    u = agent.understand(convo("2nd year ECE, I love building drones"), llm)
    assert agent.options_for(u)[:2] == ["Drone Systems Engineer", "Embedded Engineer"]
    assert len(agent.options_for(u)) == 2  # duplicate, sentence and junk dropped
    # without ideas from the LLM the field-based list is used
    assert agent.options_for(agent.understand(convo("2nd year ECE"), None))
