"""Phase 7: HTTP API (FastAPI TestClient, LLM disabled)."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.needs_data


@pytest.fixture(scope="module")
def client() -> Any:
    from careercompass import api, db
    from careercompass.graph import build_graph

    con = db.connect()
    api._state.update({"con": con, "llm": None, "graph": build_graph(None, con)})
    yield TestClient(api.app)
    api._state.clear()


def test_health(client: TestClient) -> None:
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["llm"] == "template-only"
    assert body["occupations"] > 900
    assert body["catalog_items"] >= 39


def test_roles(client: TestClient) -> None:
    roles = {x["role"]: x for x in client.get("/api/roles").json()}
    assert set(roles) == {"devops engineer", "data analyst", "machine learning engineer"}
    assert all(x["projects"] >= 3 for x in roles.values())


def test_roadmap_ok(client: TestClient) -> None:
    text = "3rd year CSE, 10 hours per week, want to be a DevOps engineer, know Linux and Git"
    r = client.post("/api/roadmap", json={"text": text})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    rm = body["roadmap"]
    assert rm["schema_version"] == "1.0"
    assert {n["kind"] for n in rm["nodes"]} >= {"course", "project", "goal"}
    assert rm["explanation"]


def test_roadmap_questions_then_answers(client: TestClient) -> None:
    text = "B.Com final year, want to be a data analyst, I know Excel"
    first = client.post("/api/roadmap", json={"text": text}).json()
    assert first["status"] == "needs_input" and first["roadmap"] is None
    assert first["missing"] == ["hours_per_week"]
    second = client.post("/api/roadmap", json={"text": text, "answers": {"hours_per_week": 8}})
    assert second.json()["status"] == "ok"


def test_request_validation(client: TestClient) -> None:
    assert client.post("/api/roadmap", json={"text": "hi"}).status_code == 422
    assert client.post("/api/roadmap", json={"text": "x" * 2001}).status_code == 422
    assert client.post("/api/roadmap", json={}).status_code == 422
