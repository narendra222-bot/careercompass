"""Strict JSON contract for a CareerCompass roadmap.

The frontend renders `nodes` and `edges` directly as a React Flow DAG; `phases` drive
the timeline and `readiness` the progress chart. `verify()` checks a roadmap against the
catalog so that nothing outside data/catalog.yaml (for example a URL invented by an LLM)
can reach the user.
"""

from __future__ import annotations

from typing import Any, Literal

import networkx as nx
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator

from careercompass.catalog import Catalog

SCHEMA_VERSION = "1.0"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Resource(_Strict):
    title: str
    url: HttpUrl


class Readiness(_Strict):
    overall: float = Field(ge=0, le=1)
    tech: float = Field(ge=0, le=1)
    skills: float = Field(ge=0, le=1)


class OnetReadiness(_Strict):
    overall: float = Field(ge=0, le=1)
    skill: float = Field(ge=0, le=1)
    knowledge: float = Field(ge=0, le=1)
    ability: float = Field(ge=0, le=1)
    technology: float = Field(ge=0, le=1)


class ReadinessPair(_Strict):
    core: Readiness
    onet: OnetReadiness


class SocAlignment(_Strict):
    soc: str = Field(pattern=r"^\d{2}-\d{4}\.\d{2}$")
    title: str
    alpha: float = Field(ge=0, le=1)


class StudentInput(_Strict):
    name: str
    branch: str
    year: int = Field(ge=1, le=6)
    hours_per_week: float = Field(gt=0, le=80)
    target_role: str
    tech_mastery: dict[str, float] = Field(default_factory=dict)
    max_weeks: int | None = Field(default=None, gt=0)


class Node(_Strict):
    id: str
    label: str
    kind: Literal["course", "project", "known", "goal"]
    phase: int | None = None
    start_week: int | None = None
    end_week: int | None = None
    hours: float = 0.0
    resources: list[Resource] = Field(default_factory=list)
    deliverables: list[str] = Field(default_factory=list)
    skills_gained: list[str] = Field(default_factory=list)


class Edge(_Strict):
    id: str
    source: str
    target: str
    kind: Literal["prereq", "milestone"]


class Phase(_Strict):
    phase: int = Field(ge=1)
    title: str
    milestone: str | None
    start_week: int = Field(ge=1)
    end_week: int = Field(ge=1)
    hours: float = Field(gt=0)
    node_ids: list[str]
    readiness_after: ReadinessPair


class Feasibility(_Strict):
    feasible: bool
    total_hours: float
    total_weeks: int
    hours_per_week: float
    max_weeks: int | None
    fix: dict[str, int] | None


class TechGap(_Strict):
    tech: str
    gap: float


class Provenance(_Strict):
    # deterministic-planner: curated catalog; llm-onet-planner: LLM draft from O*NET requirements
    generator: Literal["deterministic-planner", "llm-onet-planner"] = "deterministic-planner"
    data: str = "O*NET 31.0 Database (onetcenter.org), CC BY 4.0"
    editorial_constants_human_reviewed: bool = False


class Roadmap(_Strict):
    schema_version: Literal["1.0"] = SCHEMA_VERSION
    student: StudentInput
    target_role: str
    soc_alignment: list[SocAlignment]
    baseline_readiness: ReadinessPair
    final_readiness: ReadinessPair
    target_readiness: float = Field(ge=0, le=1)
    phases: list[Phase]
    nodes: list[Node]
    edges: list[Edge]
    top_tech_gaps: list[TechGap]
    skipped_known: list[str]
    dropped_for_time: list[str]
    unmatched_tech: list[str]
    feasibility: Feasibility
    explanation: str | None = None
    provenance: Provenance = Field(default_factory=Provenance)

    @model_validator(mode="after")
    def _graph_is_consistent(self) -> Roadmap:
        ids = [n.id for n in self.nodes]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate node ids")
        known = set(ids)
        bad = [e.id for e in self.edges if e.source not in known or e.target not in known]
        if bad:
            raise ValueError(f"edges reference unknown nodes: {bad}")
        g: nx.DiGraph = nx.DiGraph((e.source, e.target) for e in self.edges)
        if not nx.is_directed_acyclic_graph(g):
            raise ValueError("roadmap graph has a cycle")
        for p in self.phases:
            missing = [i for i in p.node_ids if i not in known]
            if missing:
                raise ValueError(f"phase {p.phase} lists unknown nodes {missing}")
            if p.end_week < p.start_week:
                raise ValueError(f"phase {p.phase} ends before it starts")
        if abs(sum(s.alpha for s in self.soc_alignment) - 1) > 1e-6:
            raise ValueError("SOC alphas must sum to 1")
        return self


def build_roadmap(
    plan: dict[str, Any], cat: Catalog, soc_titles: dict[str, str] | None = None
) -> Roadmap:
    """Convert Planner.plan() output into a validated Roadmap."""
    soc_titles = soc_titles or {}
    by_id = cat.by_id()
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    phases: list[dict[str, Any]] = []
    in_plan: set[str] = set()

    for ph in plan["phases"]:
        for it in ph["items"]:
            in_plan.add(it["id"])
            nodes.append(
                {
                    "id": it["id"],
                    "label": it["title"],
                    "kind": it["kind"],
                    "phase": ph["phase"],
                    "start_week": it["start_week"],
                    "end_week": it["end_week"],
                    "hours": it["hours"],
                    "resources": it["resources"],
                    "deliverables": it["deliverables"],
                    "skills_gained": sorted(it["covers"]["tech"])
                    + sorted(it["covers"]["elements"]),
                }
            )
        phases.append(
            {
                "phase": ph["phase"],
                "title": ph["title"],
                "milestone": ph["milestone"],
                "start_week": ph["start_week"],
                "end_week": ph["end_week"],
                "hours": ph["hours"],
                "node_ids": [it["id"] for it in ph["items"]],
                "readiness_after": ph["readiness_after"],
            }
        )

    for sid in plan["skipped_known"]:
        it = by_id[sid]
        nodes.append({"id": sid, "label": it.title, "kind": "known", "hours": 0.0})

    present = in_plan | set(plan["skipped_known"])
    for nid in sorted(present):
        for p in by_id[nid].prereqs:
            if p in present:
                edges.append({"id": f"{p}->{nid}", "source": p, "target": nid, "kind": "prereq"})

    goal = "goal:" + plan["role"].replace(" ", "-")
    nodes.append({"id": goal, "label": plan["role"].title(), "kind": "goal"})
    for ph in plan["phases"]:
        if ph["milestone"]:
            m = ph["milestone"]
            edges.append({"id": f"{m}->{goal}", "source": m, "target": goal, "kind": "milestone"})

    st = plan["student"]
    return Roadmap.model_validate(
        {
            "student": st,
            "target_role": plan["role"],
            "soc_alignment": [
                {"soc": s, "title": soc_titles.get(s, s), "alpha": a}
                for s, a in plan["socs"].items()
            ],
            "baseline_readiness": plan["baseline_readiness"],
            "final_readiness": plan["final_readiness"],
            "target_readiness": plan["target_readiness"],
            "phases": phases,
            "nodes": nodes,
            "edges": edges,
            "top_tech_gaps": plan["top_tech_gaps"],
            "skipped_known": plan["skipped_known"],
            "dropped_for_time": plan["dropped_for_time"],
            "unmatched_tech": plan["unmatched_tech"],
            "feasibility": {
                "feasible": plan["feasible"],
                "total_hours": plan["total_hours"],
                "total_weeks": plan["total_weeks"],
                "hours_per_week": st["hours_per_week"],
                "max_weeks": st["max_weeks"],
                "fix": plan["feasibility_fix"],
            },
        }
    )


def verify(roadmap: Roadmap, cat: Catalog) -> list[str]:
    """Check every node and resource against the catalog. Empty list means clean.

    Catches anything an LLM step might add or alter: unknown items, changed hours or
    titles, and URLs that are not in data/catalog.yaml.
    """
    by_id = cat.by_id()
    problems: list[str] = []
    for n in roadmap.nodes:
        if n.kind == "goal":
            continue
        it = by_id.get(n.id)
        if it is None:
            problems.append(f"node {n.id!r} is not in the catalog")
            continue
        if n.label != it.title:
            problems.append(f"node {n.id!r} title changed")
        if n.kind != "known" and n.hours != it.hours:
            problems.append(f"node {n.id!r} hours changed")
        allowed = {str(r.url) for r in it.resources}
        for r in n.resources:
            if str(r.url) not in allowed:
                problems.append(f"node {n.id!r} has non-catalog URL {r.url}")
    return problems
