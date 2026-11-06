"""Hand-curated learning catalog: courses, projects and their prerequisite DAG.

The planner selects only from this catalog, so every resource URL and hour estimate
in a roadmap is traceable to data/catalog.yaml. Nothing here calls an LLM.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import networkx as nx
import yaml
from pydantic import BaseModel, Field, HttpUrl, field_validator

CATALOG_PATH = Path(__file__).resolve().parent.parent / "data" / "catalog.yaml"


class Resource(BaseModel):
    title: str
    url: HttpUrl


class Covers(BaseModel):
    tech: dict[str, float] = Field(default_factory=dict)
    elements: dict[str, float] = Field(default_factory=dict)

    @field_validator("tech", "elements")
    @classmethod
    def _gain_in_unit_interval(cls, v: dict[str, float]) -> dict[str, float]:
        bad = {k: g for k, g in v.items() if not 0.0 < g <= 1.0}
        if bad:
            raise ValueError(f"coverage gains must be in (0, 1]: {bad}")
        return v


class CatalogItem(BaseModel):
    id: str
    title: str
    kind: Literal["course", "project"]
    roles: list[str]
    hours: float = Field(gt=0)
    prereqs: list[str] = Field(default_factory=list)
    resources: list[Resource] = Field(default_factory=list)
    covers: Covers = Field(default_factory=Covers)
    deliverables: list[str] = Field(default_factory=list)


class Catalog(BaseModel):
    items: list[CatalogItem]

    def by_id(self) -> dict[str, CatalogItem]:
        return {it.id: it for it in self.items}

    def for_role(self, role: str) -> list[CatalogItem]:
        r = role.strip().lower()
        return [it for it in self.items if r in (x.strip().lower() for x in it.roles)]

    def graph(self) -> nx.DiGraph:
        """Prerequisite graph: edge prereq -> item."""
        g: nx.DiGraph = nx.DiGraph()
        for it in self.items:
            g.add_node(it.id)
            for p in it.prereqs:
                g.add_edge(p, it.id)
        return g


def validate(cat: Catalog) -> list[str]:
    """Structural checks. Returns a list of problems (empty means valid)."""
    problems: list[str] = []
    ids = [it.id for it in cat.items]
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    if dupes:
        problems.append(f"duplicate ids: {dupes}")
    known = set(ids)
    for it in cat.items:
        missing = [p for p in it.prereqs if p not in known]
        if missing:
            problems.append(f"{it.id}: unknown prereqs {missing}")
        if it.kind == "course" and not it.resources:
            problems.append(f"{it.id}: course has no resources")
        if it.kind == "project" and not it.deliverables:
            problems.append(f"{it.id}: project has no deliverables")
    g = cat.graph()
    if not nx.is_directed_acyclic_graph(g):
        problems.append(f"prerequisite cycle: {nx.find_cycle(g)}")
    return problems


def load(path: Path = CATALOG_PATH) -> Catalog:
    """Load and validate the catalog; raise ValueError listing every problem."""
    with path.open(encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    cat = Catalog(items=raw.get("items", []))
    problems = validate(cat)
    if problems:
        raise ValueError("invalid catalog:\n  " + "\n  ".join(problems))
    return cat


def prerequisite_closure(cat: Catalog, ids: set[str]) -> set[str]:
    """ids plus every transitive prerequisite."""
    g = cat.graph()
    out = set(ids)
    for i in ids:
        out |= nx.ancestors(g, i)
    return out


def topo_order(cat: Catalog, ids: set[str]) -> list[str]:
    """Deterministic topological order of ids (ties broken by id)."""
    sub = cat.graph().subgraph(ids)
    return list(nx.lexicographical_topological_sort(sub))
