"""HTTP API for the CareerCompass agent.

Run:  .venv/bin/uvicorn careercompass.api:app --reload --port 8000
Docs: http://localhost:8000/docs

Endpoints
  GET  /api/health    database, catalog and LLM status
  GET  /api/roles     roles the planner supports (for a dropdown)
  POST /api/roadmap   free text (+ answers to earlier questions) -> roadmap or questions
"""

from __future__ import annotations

import os
import threading
from typing import Any, Literal

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from careercompass import catalog, intake
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
    allow_methods=["GET", "POST"],
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
    return _state


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
