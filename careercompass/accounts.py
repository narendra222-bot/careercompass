"""Accounts and saved chats.

Storage is one SQLite file (data/app.db, not in git): users and their conversations.
A conversation is stored as the JSON the web app already uses, so the server does not
need to understand its shape; it only checks ownership and size.

Sign-in
  * Google: the browser gets an ID token from Google Identity Services; we verify it with
    google-auth (signature, audience = our client id, expiry, verified email).
  * Email: name + email without a password. Convenient for demos, but anyone who types an
    address gets that account, so it can be switched off with ALLOW_EMAIL_LOGIN=false.
Sessions are stateless tokens: base64(payload).HMAC-SHA256(APP_SECRET), valid 30 days.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "app.db"
TOKEN_TTL_S = 30 * 24 * 3600
MAX_CONVERSATION_BYTES = 2_000_000
MAX_CONVERSATIONS = 200
EMAIL = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")

_lock = threading.Lock()
_secret: bytes | None = None


class AuthError(Exception):
    pass


# ---------------- storage ----------------


def connect(path: Path | str | None = None) -> sqlite3.Connection:
    p = Path(path or DB_PATH)
    p.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(p, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY,
            email TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            picture TEXT,
            provider TEXT NOT NULL,
            created_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS conversations (
            id TEXT NOT NULL,
            user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            title TEXT NOT NULL,
            data TEXT NOT NULL,
            updated_at REAL NOT NULL,
            PRIMARY KEY (user_id, id)
        );
        CREATE INDEX IF NOT EXISTS conv_user ON conversations(user_id, updated_at);
        """
    )
    return con


def _user_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "email": row["email"],
        "name": row["name"],
        "picture": row["picture"],
        "provider": row["provider"],
    }


def upsert_user(
    con: sqlite3.Connection, email: str, name: str, provider: str, picture: str | None = None
) -> dict[str, Any]:
    email = email.strip().lower()
    name = name.strip()[:60] or email.split("@")[0]
    with _lock:
        row = con.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        if row is None:
            uid = secrets.token_hex(12)
            con.execute(
                "INSERT INTO users (id, email, name, picture, provider, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (uid, email, name, picture, provider, time.time()),
            )
        else:
            uid = row["id"]
            # a verified Google sign-in upgrades the account; email login never downgrades it
            if provider == "google":
                con.execute(
                    "UPDATE users SET name = ?, picture = ?, provider = 'google' WHERE id = ?",
                    (name, picture, uid),
                )
        con.commit()
        return _user_dict(con.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone())


def get_user(con: sqlite3.Connection, uid: str) -> dict[str, Any] | None:
    row = con.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
    return _user_dict(row) if row else None


def list_conversations(con: sqlite3.Connection, uid: str) -> list[dict[str, Any]]:
    rows = con.execute(
        "SELECT data FROM conversations WHERE user_id = ? ORDER BY updated_at DESC", (uid,)
    ).fetchall()
    return [json.loads(r["data"]) for r in rows]


def save_conversation(con: sqlite3.Connection, uid: str, conv: dict[str, Any]) -> None:
    cid = str(conv.get("id") or "")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", cid):
        raise ValueError("bad conversation id")
    data = json.dumps(conv, ensure_ascii=False)
    if len(data.encode()) > MAX_CONVERSATION_BYTES:
        raise ValueError("conversation too large")
    title = str(conv.get("title") or "New chat")[:120]
    with _lock:
        n = con.execute(
            "SELECT COUNT(*) FROM conversations WHERE user_id = ? AND id != ?", (uid, cid)
        ).fetchone()[0]
        if n >= MAX_CONVERSATIONS:
            raise ValueError("too many conversations; delete some first")
        con.execute(
            "INSERT INTO conversations (id, user_id, title, data, updated_at) "
            "VALUES (?, ?, ?, ?, ?) ON CONFLICT(user_id, id) DO UPDATE SET "
            "title = excluded.title, data = excluded.data, updated_at = excluded.updated_at",
            (cid, uid, title, data, time.time()),
        )
        con.commit()


def delete_conversation(con: sqlite3.Connection, uid: str, cid: str) -> bool:
    with _lock:
        cur = con.execute("DELETE FROM conversations WHERE user_id = ? AND id = ?", (uid, cid))
        con.commit()
        return cur.rowcount > 0


# ---------------- session tokens ----------------


def _key() -> bytes:
    global _secret
    if _secret is None:
        env = os.getenv("APP_SECRET")
        if env and len(env) >= 32:
            _secret = env.encode()
        else:
            # no configured secret: sessions last until the server restarts
            _secret = secrets.token_bytes(32)
    return _secret


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def issue_token(uid: str, now: float | None = None) -> str:
    payload = _b64(
        json.dumps({"uid": uid, "exp": int((now or time.time()) + TOKEN_TTL_S)}).encode()
    )
    sig = _b64(hmac.new(_key(), payload.encode(), hashlib.sha256).digest())
    return f"{payload}.{sig}"


def read_token(token: str, now: float | None = None) -> str:
    """Return the user id, or raise AuthError."""
    try:
        payload, sig = token.split(".", 1)
        good = _b64(hmac.new(_key(), payload.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(sig, good):
            raise AuthError("bad signature")
        data = json.loads(_unb64(payload))
    except (ValueError, json.JSONDecodeError) as exc:
        raise AuthError("malformed token") from exc
    if data.get("exp", 0) < (now or time.time()):
        raise AuthError("session expired")
    return str(data["uid"])


# ---------------- sign-in ----------------


def google_client_id() -> str | None:
    return os.getenv("GOOGLE_CLIENT_ID") or None


def email_login_allowed() -> bool:
    return os.getenv("ALLOW_EMAIL_LOGIN", "true").lower() not in ("0", "false", "no")


def verify_google(credential: str) -> dict[str, Any]:
    """Check a Google ID token and return its claims (email, name, picture)."""
    cid = google_client_id()
    if not cid:
        raise AuthError("Google sign-in is not configured on the server")
    from google.auth.transport import requests as g_requests
    from google.oauth2 import id_token

    try:
        claims = id_token.verify_oauth2_token(credential, g_requests.Request(), cid)
    except ValueError as exc:
        raise AuthError("Google sign-in could not be verified") from exc
    if claims.get("iss") not in ("accounts.google.com", "https://accounts.google.com"):
        raise AuthError("unexpected token issuer")
    if not claims.get("email") or not claims.get("email_verified"):
        raise AuthError("Google account email is not verified")
    return dict(claims)


def check_email(email: str) -> str:
    email = email.strip()
    if not EMAIL.match(email) or len(email) > 120:
        raise AuthError("Enter a valid email address")
    return email
