"""HTTP API for the CareerCompass agent.

Run:  .venv/bin/uvicorn careercompass.api:app --reload --port 8000
Docs: http://localhost:8000/docs

Endpoints
  GET  /api/health    database, catalog and LLM status
  GET  /api/roles     roles the planner supports (for a dropdown)
  POST /api/roadmap   free text (+ answers to earlier questions) -> roadmap or questions
  POST /api/chat      a conversation -> streamed thinking steps, reply text and roadmap (SSE)
  GET  /api/auth/config, POST /api/auth/google | /api/auth/email, GET /api/me
  GET  /api/conversations, PUT|DELETE /api/conversations/{id}   (signed-in user's chats)
"""

from __future__ import annotations

import os
import threading
from collections.abc import Iterator
from typing import Annotated, Any, Literal

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from careercompass import accounts, catalog, chat, intake
from careercompass.graph import build_graph
from careercompass.llm import get_llm
from careercompass.planner import load_role_profile

app = FastAPI(
    title="CareerCompass API",
    version="1.0",
    description="Personalized education-to-career roadmaps grounded in O*NET.",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("CORS_ORIGINS", "http://localhost:3000").split(","),
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["*"],
)

_lock = threading.Lock()  # DuckDB connection + compiled graph are shared
_state: dict[str, Any] = {}


def _runtime() -> dict[str, Any]:
    if not _state:
        from careercompass import db

        _state["con"] = db.connect()
        _state["llm"] = get_llm()
        _state["graph"] = build_graph(_state["llm"], _state["con"])
        threading.Thread(target=_warm_search, daemon=True).start()
    return _state


def _warm_search() -> None:
    """Load the O*NET search index in the background (first load takes a few seconds)."""
    try:
        from careercompass import retrieval

        retrieval.search("software developer", k=1)
    except Exception:  # search is only needed for non-curated roles
        pass


class RoadmapRequest(BaseModel):
    text: str = Field(min_length=10, max_length=2000, description="Student's own description")
    answers: dict[str, Any] = Field(
        default_factory=dict,
        description="Answers to earlier questions, e.g. {'hours_per_week': 8}",
    )


class RoadmapResponse(BaseModel):
    status: Literal["ok", "needs_input", "error"]
    roadmap: dict[str, Any] | None = None
    questions: list[str] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list)
    adjustments: list[str] = Field(default_factory=list)
    problems: list[str] = Field(default_factory=list)
    draft: dict[str, Any] = Field(default_factory=dict)
    trace: list[str] = Field(default_factory=list)
    intake_source: str | None = None
    explanation_source: str | None = None


@app.get("/api/health")
def health() -> dict[str, Any]:
    rt = _runtime()
    with _lock:
        n_occ = rt["con"].execute("SELECT COUNT(*) FROM occupation").fetchone()[0]
    llm = rt["llm"]
    return {
        "status": "ok",
        "occupations": n_occ,
        "catalog_items": len(catalog.load().items),
        "llm": llm.provider if llm is not None else "template-only",
    }


@app.get("/api/roles")
def roles() -> list[dict[str, Any]]:
    cat = catalog.load()
    out = []
    for role in sorted(intake.supported_roles()):
        prof = load_role_profile(role)
        items = cat.for_role(role)
        out.append(
            {
                "role": role,
                "socs": [
                    {"soc": s["soc"], "title": s["title"], "alpha": s["alpha"]}
                    for s in prof["socs"]
                ],
                "courses": sum(1 for i in items if i.kind == "course"),
                "projects": sum(1 for i in items if i.kind == "project"),
            }
        )
    return out


@app.post("/api/chat")
def chat_stream(req: chat.ChatRequest) -> StreamingResponse:
    """One chat turn as Server-Sent Events: step*, text*, roadmap?, text*, done | error."""
    rt = _runtime()

    def gen() -> Iterator[str]:
        with _lock:  # graph + DuckDB connection are shared
            yield from chat.run_events(rt["graph"], req, rt["llm"], con=rt["con"])

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/roadmap", response_model=RoadmapResponse)
def roadmap(req: RoadmapRequest) -> RoadmapResponse:
    rt = _runtime()
    try:
        with _lock:
            s = rt["graph"].invoke({"text": req.text, "answers": req.answers})
    except Exception as exc:  # unexpected failure: never leak a stack trace
        raise HTTPException(
            status_code=500, detail=f"planning failed: {type(exc).__name__}"
        ) from exc
    return RoadmapResponse(
        status=s.get("status", "error"),
        roadmap=s.get("roadmap") if s.get("status") == "ok" else None,
        questions=s.get("questions", []),
        missing=s.get("missing", []),
        adjustments=s.get("adjustments", []),
        problems=s.get("problems", []),
        draft=s.get("draft", {}),
        trace=s.get("trace", []),
        intake_source=s.get("intake_source"),
        explanation_source=s.get("explanation_source"),
    )


# ---------------- accounts and saved chats ----------------


def _accounts() -> Any:
    if "accounts" not in _state:
        _state["accounts"] = accounts.connect()
    return _state["accounts"]


def current_user(authorization: str | None = Header(default=None)) -> dict[str, Any]:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Sign in first")
    try:
        uid = accounts.read_token(authorization[7:])
    except accounts.AuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    user = accounts.get_user(_accounts(), uid)
    if user is None:
        raise HTTPException(status_code=401, detail="Account not found")
    return user


CurrentUser = Annotated[dict[str, Any], Depends(current_user)]


class GoogleLogin(BaseModel):
    credential: str = Field(min_length=20, max_length=5000)


class EmailLogin(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    email: str = Field(min_length=3, max_length=120)


def _session(user: dict[str, Any]) -> dict[str, Any]:
    return {"token": accounts.issue_token(user["id"]), "user": user}


@app.get("/api/auth/config")
def auth_config() -> dict[str, Any]:
    return {
        "google_client_id": accounts.google_client_id(),
        "email_login": accounts.email_login_allowed(),
    }


@app.post("/api/auth/google")
def login_google(body: GoogleLogin) -> dict[str, Any]:
    try:
        claims = accounts.verify_google(body.credential)
    except accounts.AuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    user = accounts.upsert_user(
        _accounts(),
        claims["email"],
        claims.get("name") or claims["email"].split("@")[0],
        "google",
        claims.get("picture"),
    )
    return _session(user)


@app.post("/api/auth/email")
def login_email(body: EmailLogin) -> dict[str, Any]:
    if not accounts.email_login_allowed():
        raise HTTPException(status_code=403, detail="Email sign-in is turned off; use Google")
    try:
        email = accounts.check_email(body.email)
    except accounts.AuthError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _session(accounts.upsert_user(_accounts(), email, body.name, "email"))


@app.get("/api/me")
def me(user: CurrentUser) -> dict[str, Any]:
    return user


@app.get("/api/conversations")
def conversations(user: CurrentUser) -> list[dict[str, Any]]:
    return accounts.list_conversations(_accounts(), user["id"])


@app.put("/api/conversations/{cid}")
def put_conversation(cid: str, conv: dict[str, Any], user: CurrentUser) -> dict[str, str]:
    if str(conv.get("id")) != cid:
        raise HTTPException(status_code=422, detail="id in the URL and body differ")
    try:
        accounts.save_conversation(_accounts(), user["id"], conv)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"status": "saved"}


@app.delete("/api/conversations/{cid}")
def delete_conversation(cid: str, user: CurrentUser) -> dict[str, str]:
    if not accounts.delete_conversation(_accounts(), user["id"], cid):
        raise HTTPException(status_code=404, detail="No such conversation")
    return {"status": "deleted"}
