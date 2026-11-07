"""Any-role planning (O*NET + LLM draft), link research/verification and model fallback.

No network: a FakeLLM answers, search and link checks are stubbed.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from careercompass import agent, custom_planner, llm, research, schemas

REQ = {
    "tools": ["Linux", "Python", "Splunk Enterprise", "Wireshark", "Tenable Nessus"],
    "skills": ["Critical Thinking", "Systems Analysis"],
    "knowledge": ["Computers and Electronics"],
    "description": "Plan and carry out security measures.",
}

DRAFT = {
    "tools": ["Python", "splunk enterprise", "Wireshark", "Made Up Tool"],
    "phases": [
        {
            "title": "Foundations",
            "items": [
                {"key": "p1", "kind": "project", "title": "Home lab", "hours": 99,
                 "covers": ["Wireshark"], "after": ["c1"], "deliverables": ["A lab"]},
                {"key": "c1", "kind": "course", "title": "Networking basics", "hours": 2,
                 "covers": ["Wireshark", "Critical Thinking", "nonsense"], "after": ["later"]},
                {"key": "c2", "kind": "course", "title": "Python for security", "hours": 20,
                 "covers": ["Python"], "after": ["c1"]},
            ],
        },
        {
            "title": "Detection",
            "items": [
                {"key": "c3", "kind": "course", "title": "SIEM with Splunk", "hours": 15,
                 "covers": ["Splunk Enterprise", "Systems Analysis"], "after": ["c2", "c4"]},
                {"key": "p2", "kind": "project", "title": "Detect an attack", "hours": 25,
                 "covers": ["Splunk Enterprise", "Computers and Electronics"], "after": ["c3"],
                 "deliverables": ["Dashboard", "Report"]},
            ],
        },
    ],
}  # fmt: skip

PROFILE = {
    "name": "Venkat",
    "branch": "AI&ML",
    "year": 4,
    "hours_per_week": 10.0,
    "target_role": "cyber security analyst",
    "tech_mastery": {"Linux": 0.5},
    "max_weeks": None,
}


class FakeLLM:
    provider = "fake"

    def __init__(self, answers: dict[str, Any]) -> None:
        self.answers = answers  # keyword in system prompt -> JSON answer

    def chat(self, system: str, user: str, *, smart: bool = False, json_mode: bool = False) -> str:
        for k, v in self.answers.items():
            if k in system:
                return v if isinstance(v, str) else json.dumps(v)
        raise llm.LLMError("no canned answer")


# ---------------- draft cleaning, schedule, readiness ----------------


def test_clean_draft_keeps_only_valid_parts() -> None:
    d = custom_planner.clean_draft(DRAFT, REQ)
    assert d["tools"] == ["Python", "Splunk Enterprise", "Wireshark"]  # case fixed, fake dropped
    first = d["phases"][0]["items"]
    assert [x["kind"] for x in first] == ["course", "course", "project"]  # project moved last
    by_title = {x["title"]: x for p in d["phases"] for x in p["items"]}
    c1 = by_title["Networking basics"]
    assert c1["hours"] == 6 and c1["covers"] == ["Wireshark", "Critical Thinking"]
    assert c1["after"] == []  # "later" is not an earlier item
    assert by_title["Home lab"]["hours"] == 40  # clamped
    # c4 never existed, c2 did
    assert by_title["SIEM with Splunk"]["after"] == [by_title["Python for security"]["id"]]


def test_clean_draft_rejects_tiny_drafts() -> None:
    with pytest.raises(ValueError):
        custom_planner.clean_draft({"phases": [DRAFT["phases"][0]]}, REQ)


def test_build_is_a_valid_roadmap() -> None:
    d = custom_planner.clean_draft(DRAFT, REQ)
    socs = [{"soc": "15-1212.00", "title": "Information Security Analysts", "alpha": 1.0}]
    rm = custom_planner.build("Cyber Security Analyst", socs, REQ, d, PROFILE, "fake")
    r = schemas.Roadmap.model_validate(rm)
    assert r.provenance.generator == "llm-onet-planner"
    hours = sum(n.hours for n in r.nodes)
    assert r.feasibility.total_hours == hours
    assert r.feasibility.total_weeks == -(-int(hours) // 10)
    # readiness only goes up, phase by phase
    seq = [r.baseline_readiness.core.overall, *[p.readiness_after.core.overall for p in r.phases]]
    assert seq == sorted(seq) and seq[-1] > seq[0]
    # weeks are contiguous and ordered
    items = [n for n in r.nodes if n.kind in ("course", "project")]
    assert items[0].start_week == 1 and all(n.end_week >= n.start_week for n in items)
    assert {e.kind for e in r.edges} == {"prereq", "milestone"}


def test_deadline_makes_plan_infeasible_with_a_fix() -> None:
    d = custom_planner.clean_draft(DRAFT, REQ)
    socs = [{"soc": "15-1212.00", "title": "x", "alpha": 1.0}]
    rm = custom_planner.build("X", socs, REQ, d, {**PROFILE, "max_weeks": 4}, "fake")
    f = rm["feasibility"]
    assert not f["feasible"] and f["fix"]["needed_weeks_at_current_hours"] == f["total_weeks"]


def test_resolve_role_uses_llm_pick_and_normalises() -> None:
    cands = [
        {"soc": "15-1299.06", "title": "Digital Forensics Analysts", "source": "retrieval"},
        {"soc": "15-1212.00", "title": "Information Security Analysts", "source": "retrieval"},
    ]
    fake = FakeLLM({"closest O*NET": {"picks": [{"soc": "15-1212.00", "weight": 3},
                                                {"soc": "99-9999.99", "weight": 1}]}})  # fmt: skip
    socs = custom_planner.resolve_role("cyber security analyst", fake, search=lambda q, k: cands)
    assert socs == [{"soc": "15-1212.00", "title": "Information Security Analysts", "alpha": 1.0}]
    # LLM failure falls back to the top search hit
    socs = custom_planner.resolve_role("x", FakeLLM({}), search=lambda q, k: cands)
    assert socs[0]["soc"] == "15-1299.06"


def test_display_role() -> None:
    assert custom_planner.display_role("ui ux designer") == "UI UX Designer"
    assert custom_planner.display_role("SOC Analyst") == "SOC Analyst"


@pytest.mark.needs_data
def test_plan_events_end_to_end(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    from careercompass import db

    monkeypatch.setattr(research, "CACHE_PATH", tmp_path / "cache.json")
    monkeypatch.setattr(research, "verify", lambda url, timeout=8.0: "good" in url)
    fake = FakeLLM(
        {
            "closest O*NET": {"picks": [{"soc": "15-1212.00", "weight": 1}]},
            "learning roadmaps": {**DRAFT, "tools": []},  # empty -> falls back to top O*NET tools
            "recommend free learning": {
                "resources": [
                    {"topic": 1, "title": "Good course", "url": "https://good.example/1"},
                    {"topic": 2, "title": "Bad course", "url": "https://bad.example/2"},
                ]
            },
        }
    )
    cands = [{"soc": "15-1212.00", "title": "Information Security Analysts", "source": "retrieval"}]
    events = list(
        custom_planner.plan_events(PROFILE, fake, db.connect(), search=lambda q, k: cands)
    )
    kinds = [k for k, _ in events]
    assert kinds[-1] == "roadmap" and "error" not in kinds
    labels = [d["label"] for k, d in events if k == "step"]
    assert labels[0] == "Matched O*NET occupations" and "Verified links" in labels
    rm = events[-1][1]
    linked = [n for n in rm["nodes"] if n["resources"]]
    assert [n["resources"][0]["url"] for n in linked] == ["https://good.example/1"]
    assert rm["explanation"]  # template explanation when the LLM can't explain


# ---------------- link research ----------------


def test_research_verifies_and_caches(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    monkeypatch.setattr(research, "CACHE_PATH", tmp_path / "cache.json")
    monkeypatch.setattr(research, "verify", lambda url, timeout=8.0: url.endswith("/ok"))
    fake = FakeLLM(
        {
            "recommend free learning": {
                "resources": [
                    {"topic": 1, "title": "A", "url": "https://a.example/ok"},
                    {"topic": 2, "title": "B", "url": "https://b.example/broken"},
                    {"topic": 9, "title": "C", "url": "https://c.example/ok"},  # no such topic
                ]
            }
        }
    )
    got = research.research(["Topic one", "Topic two"], "analyst", fake)
    assert got == {"Topic one": {"title": "A", "url": "https://a.example/ok"}}
    # second time it comes from the cache, no LLM needed
    assert research.research(["Topic one"], "analyst", None) == got


def test_is_public_https_blocks_internal_targets() -> None:
    assert not research.is_public_https("http://example.com")
    assert not research.is_public_https("https://localhost/")
    assert not research.is_public_https("https://127.0.0.1/x")
    assert not research.is_public_https("https://user:pw@example.com/")


# ---------------- custom roles in the agent ----------------


def test_agent_accepts_any_role_with_llm_only() -> None:
    msgs = [{"role": "user", "content": "3rd year CSE, 10 hours a week, cyber security analyst"}]
    fake = FakeLLM(
        {
            "extract what the STUDENT": {
                "intent": "profile",
                "branch": "CSE",
                "year": 3,
                "hours_per_week": 10,
                "target_role": "Cyber Security Analyst",
            }
        }
    )
    u = agent.understand(msgs, fake)
    assert u.custom_role == "cyber security analyst" and u.missing == []
    assert agent.plan_input(u)["target_role"] == "cyber security analyst"
    rules = agent.understand(msgs, None)
    assert rules.custom_role is None and "target_role" in rules.missing


# ---------------- model fallback on quota errors ----------------


def test_chat_falls_back_to_next_model(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    monkeypatch.setattr(llm, "_cooldown", {})
    calls: list[str] = []

    def fake_post(url: str, **kw: Any) -> httpx.Response:
        model = kw["json"]["model"]
        calls.append(model)
        req = httpx.Request("POST", url)
        if model == "a":
            return httpx.Response(429, headers={"retry-after": "900"}, request=req)
        body = {"choices": [{"message": {"content": f"hi from {model}"}}]}
        return httpx.Response(200, json=body, request=req)

    monkeypatch.setattr(httpx, "post", fake_post)
    client = llm.OpenAICompatLLM(
        api_key="k", model_fast="a", model_smart="a", fallback_models=("b",), use_cache=False
    )
    assert client.chat("s", "u") == "hi from b"
    assert calls == ["a", "b"]
    # "a" is cooling down now, so the next call goes straight to "b"
    calls.clear()
    assert client.chat("s", "u2") == "hi from b" and calls == ["b"]


def test_short_rate_limit_waits_and_retries_same_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(llm, "_cooldown", {})
    monkeypatch.setattr(llm.time, "sleep", lambda s: None)
    calls: list[str] = []

    def fake_post(url: str, **kw: Any) -> httpx.Response:
        calls.append(kw["json"]["model"])
        req = httpx.Request("POST", url)
        if len(calls) == 1:
            return httpx.Response(429, headers={"retry-after": "3"}, request=req)
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]}, request=req)

    monkeypatch.setattr(httpx, "post", fake_post)
    client = llm.OpenAICompatLLM(
        api_key="k", model_fast="a", model_smart="a", fallback_models=("b",), use_cache=False
    )
    assert client.chat("s", "u") == "ok" and calls == ["a", "a"]


def test_daily_quota_is_read_from_the_message(monkeypatch: pytest.MonkeyPatch) -> None:
    msg = (
        '{"error":{"message":"Rate limit reached for model `a` on tokens per day (TPD): '
        'Limit 200000, Used 199684. Please try again in 9m11.2s."}}'
    )
    assert llm.quota_reset_s(msg) == pytest.approx(551.2)
    assert llm.quota_reset_s('{"error":{"message":"tokens per minute (TPM)"}}') is None
    monkeypatch.setattr(llm, "_cooldown", {})
    monkeypatch.setattr(llm.time, "sleep", lambda s: None)
    calls: list[str] = []

    def fake_post(url: str, **kw: Any) -> httpx.Response:
        calls.append(kw["json"]["model"])
        req = httpx.Request("POST", url)
        if kw["json"]["model"] == "a":  # short retry-after header, but the day is used up
            return httpx.Response(429, headers={"retry-after": "4"}, text=msg, request=req)
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]}, request=req)

    monkeypatch.setattr(httpx, "post", fake_post)
    client = llm.OpenAICompatLLM(
        api_key="k", model_fast="a", model_smart="a", fallback_models=("b",), use_cache=False
    )
    assert client.chat("s", "u") == "ok" and calls == ["a", "b"]  # no pointless wait on "a"
    calls.clear()
    assert client.chat("s", "u2") == "ok" and calls == ["b"]  # "a" skipped until reset
