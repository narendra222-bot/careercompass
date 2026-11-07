"""Accounts, session tokens and saved chats (SQLite in a temp dir, no network)."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from careercompass import accounts, api


@pytest.fixture()
def client(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setenv("ALLOW_EMAIL_LOGIN", "true")
    monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)
    api._state["accounts"] = accounts.connect(tmp_path / "app.db")
    yield TestClient(api.app)
    api._state.pop("accounts").close()


def login(
    client: TestClient, email: str = "ravi@college.edu", name: str = "Ravi"
) -> dict[str, str]:
    r = client.post("/api/auth/email", json={"name": name, "email": email})
    assert r.status_code == 200
    return {"Authorization": f"Bearer {r.json()['token']}"}


CONV = {"id": "c1", "title": "DevOps roadmap", "messages": [{"role": "user", "content": "hi"}]}


def test_tokens_round_trip_and_reject_tampering() -> None:
    t = accounts.issue_token("u1", now=1000)
    assert accounts.read_token(t, now=1001) == "u1"
    payload, sig = t.split(".")
    other = accounts.issue_token("u2", now=1000).split(".")[0]
    with pytest.raises(accounts.AuthError):
        accounts.read_token(f"{other}.{sig}", now=1001)  # swapped payload
    with pytest.raises(accounts.AuthError):
        accounts.read_token(t, now=1000 + accounts.TOKEN_TTL_S + 1)  # expired
    with pytest.raises(accounts.AuthError):
        accounts.read_token("garbage", now=1001)


def test_config(client: TestClient) -> None:
    assert client.get("/api/auth/config").json() == {"google_client_id": None, "email_login": True}


def test_email_login_and_me(client: TestClient) -> None:
    h = login(client, "Ravi@College.edu")
    me = client.get("/api/me", headers=h).json()
    assert me["email"] == "ravi@college.edu" and me["provider"] == "email"
    assert client.post("/api/auth/email", json={"name": "x", "email": "nope"}).status_code == 422
    assert client.get("/api/me").status_code == 401
    assert client.get("/api/me", headers={"Authorization": "Bearer x.y"}).status_code == 401


def test_email_login_can_be_disabled(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALLOW_EMAIL_LOGIN", "false")
    r = client.post("/api/auth/email", json={"name": "a", "email": "a@b.co"})
    assert r.status_code == 403


def test_google_needs_configuration(client: TestClient) -> None:
    r = client.post("/api/auth/google", json={"credential": "x" * 40})
    assert r.status_code == 401 and "not configured" in r.json()["detail"]


def test_google_login_with_verified_claims(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    claims = {"email": "venkat@gmail.com", "name": "Venkat", "picture": "https://p/x.png"}
    monkeypatch.setattr(accounts, "verify_google", lambda cred: claims)
    email_first = login(client, "venkat@gmail.com", "v")
    r = client.post("/api/auth/google", json={"credential": "x" * 40})
    assert r.status_code == 200
    user = r.json()["user"]
    assert (user["name"], user["provider"]) == ("Venkat", "google")
    # same account as the earlier email login, now upgraded
    assert client.get("/api/me", headers=email_first).json()["id"] == user["id"]


def test_conversations_are_private(client: TestClient) -> None:
    ravi, priya = login(client), login(client, "priya@college.edu", "Priya")
    assert client.put("/api/conversations/c1", json=CONV, headers=ravi).status_code == 200
    assert client.get("/api/conversations", headers=ravi).json() == [CONV]
    assert client.get("/api/conversations", headers=priya).json() == []
    # Priya can't delete or overwrite Ravi's chat
    assert client.delete("/api/conversations/c1", headers=priya).status_code == 404
    client.put("/api/conversations/c1", json={**CONV, "title": "mine"}, headers=priya)
    assert client.get("/api/conversations", headers=ravi).json()[0]["title"] == "DevOps roadmap"
    # update, then delete
    client.put("/api/conversations/c1", json={**CONV, "title": "Updated"}, headers=ravi)
    assert client.get("/api/conversations", headers=ravi).json()[0]["title"] == "Updated"
    assert client.delete("/api/conversations/c1", headers=ravi).status_code == 200
    assert client.get("/api/conversations", headers=ravi).json() == []


def test_conversation_validation(client: TestClient) -> None:
    h = login(client)
    assert (
        client.put("/api/conversations/c2", json=CONV, headers=h).status_code == 422
    )  # id differs
    bad = {**CONV, "id": "../etc"}
    assert client.put("/api/conversations/..%2Fetc", json=bad, headers=h).status_code in (404, 422)
    huge = {**CONV, "blob": "x" * (accounts.MAX_CONVERSATION_BYTES + 1)}
    assert client.put("/api/conversations/c1", json=huge, headers=h).status_code == 422
    assert client.put("/api/conversations/c1", json=CONV).status_code == 401
