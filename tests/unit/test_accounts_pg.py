"""Accounts on Postgres (used on hosted deploys), against a throwaway local server."""

from __future__ import annotations

from typing import Any

import pytest

pgserver = pytest.importorskip("pgserver")

from careercompass import accounts  # noqa: E402


@pytest.fixture(scope="module")
def pg_url(tmp_path_factory: pytest.TempPathFactory) -> Any:
    srv = pgserver.get_server(tmp_path_factory.mktemp("pg"), cleanup_mode="stop")
    yield srv.get_uri()


def test_postgres_store(pg_url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", pg_url)
    con = accounts.connect()
    assert isinstance(con, accounts.PgConnection)
    u = accounts.upsert_user(con, "Ravi@College.edu", "Ravi", "email")
    same = accounts.upsert_user(con, "ravi@college.edu", "Ravi K", "google", "https://p/x.png")
    assert same["id"] == u["id"] and same["provider"] == "google"

    conv = {"id": "c1", "title": "DevOps", "messages": [{"role": "user", "content": "hi"}]}
    accounts.save_conversation(con, u["id"], conv)
    accounts.save_conversation(con, u["id"], {**conv, "title": "Updated"})
    assert [c["title"] for c in accounts.list_conversations(con, u["id"])] == ["Updated"]
    assert accounts.delete_conversation(con, u["id"], "c1")
    assert not accounts.delete_conversation(con, u["id"], "c1")

    con._con.close()  # hosted Postgres drops idle connections; the next query reconnects
    assert accounts.get_user(con, u["id"]) == same
    con.close()


def test_sqlite_is_used_for_an_explicit_path(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    con = accounts.connect(tmp_path / "app.db")
    assert not isinstance(con, accounts.PgConnection)
    con.close()
