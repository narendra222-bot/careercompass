"""Phase 5: roadmap JSON contract and catalog verification."""

from __future__ import annotations

import json
from typing import Any

import pytest
from pydantic import ValidationError

from careercompass import catalog, schemas
from careercompass.schemas import Roadmap

pytestmark = pytest.mark.needs_data


@pytest.fixture(scope="module")
def roadmap() -> Roadmap:
    from careercompass import db
    from careercompass.planner import StudentProfile, generate_roadmap

    student = StudentProfile(
        "Ravi", "CSE", 3, 10, "devops engineer", {"Linux": 0.7, "Bash": 0.6, "git": 0.7}
    )
    r: Roadmap = generate_roadmap(student, db.connect())
    return r


def _dump(r: Roadmap) -> dict[str, Any]:
    d: dict[str, Any] = r.model_dump(mode="json")
    return d


def test_json_round_trip(roadmap: Roadmap) -> None:
    text = roadmap.model_dump_json()
    again = Roadmap.model_validate_json(text)
    assert again == roadmap
    assert json.loads(text)["schema_version"] == "1.0"


def test_graph_shape(roadmap: Roadmap) -> None:
    kinds = {n.kind for n in roadmap.nodes}
    assert kinds == {"course", "project", "known", "goal"}
    goal = [n for n in roadmap.nodes if n.kind == "goal"]
    assert len(goal) == 1
    projects = {n.id for n in roadmap.nodes if n.kind == "project"}
    into_goal = {e.source for e in roadmap.edges if e.target == goal[0].id}
    assert into_goal == projects
    # every phase node exists and every scheduled node belongs to exactly one phase
    scheduled = [i for p in roadmap.phases for i in p.node_ids]
    assert len(scheduled) == len(set(scheduled))
    assert set(scheduled) == {n.id for n in roadmap.nodes if n.kind in ("course", "project")}


def test_soc_alignment_has_titles(roadmap: Roadmap) -> None:
    assert all(s.title != s.soc for s in roadmap.soc_alignment)
    assert abs(sum(s.alpha for s in roadmap.soc_alignment) - 1) < 1e-9


def test_verify_clean(roadmap: Roadmap) -> None:
    assert schemas.verify(roadmap, catalog.load()) == []


def test_verify_catches_invented_url_hours_and_node(roadmap: Roadmap) -> None:
    d = _dump(roadmap)
    course = next(n for n in d["nodes"] if n["kind"] == "course" and n["resources"])
    course["resources"].append({"title": "Great course", "url": "https://totally-real.example/"})
    course["hours"] = 1.0
    d["nodes"].append({"id": "made-up-course", "label": "Made up", "kind": "course"})
    problems = schemas.verify(Roadmap.model_validate(d), catalog.load())
    text = " | ".join(problems)
    assert "non-catalog URL" in text
    assert "hours changed" in text
    assert "made-up-course" in text


def test_validator_rejects_bad_graphs(roadmap: Roadmap) -> None:
    d = _dump(roadmap)
    d["edges"].append(
        {"id": "x", "source": "nope", "target": d["nodes"][0]["id"], "kind": "prereq"}
    )
    with pytest.raises(ValidationError, match="unknown nodes"):
        Roadmap.model_validate(d)

    d = _dump(roadmap)
    e = d["edges"][0]
    d["edges"].append(
        {"id": "back", "source": e["target"], "target": e["source"], "kind": "prereq"}
    )
    with pytest.raises(ValidationError, match="cycle"):
        Roadmap.model_validate(d)


def test_extra_fields_forbidden(roadmap: Roadmap) -> None:
    d = _dump(roadmap)
    d["nodes"][0]["salary"] = "100k"
    with pytest.raises(ValidationError):
        Roadmap.model_validate(d)


def test_readiness_bounds_enforced(roadmap: Roadmap) -> None:
    d = _dump(roadmap)
    d["final_readiness"]["core"]["overall"] = 1.5
    with pytest.raises(ValidationError):
        Roadmap.model_validate(d)
