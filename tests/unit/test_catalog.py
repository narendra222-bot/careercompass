"""Phase 4: learning catalog structure, prerequisite DAG and name integrity."""

from __future__ import annotations

import pytest

from careercompass import catalog, tech_catalog
from careercompass.catalog import Catalog, CatalogItem

ALPHAS = {"15-1252.00": 0.25, "15-1299.08": 0.45, "15-1244.00": 0.30}


def _item(i: str, prereqs: list[str] | None = None, **kw: object) -> CatalogItem:
    base: dict[str, object] = {
        "id": i,
        "title": i,
        "kind": "course",
        "roles": ["devops engineer"],
        "hours": 1,
        "prereqs": prereqs or [],
        "resources": [{"title": "x", "url": "https://example.org/"}],
    }
    base.update(kw)
    return CatalogItem.model_validate(base)


def test_validate_detects_cycle_unknown_prereq_and_duplicates() -> None:
    cyc = Catalog(items=[_item("a", ["b"]), _item("b", ["a"])])
    assert any("cycle" in p for p in catalog.validate(cyc))
    unk = Catalog(items=[_item("a", ["zzz"])])
    assert any("unknown prereqs" in p for p in catalog.validate(unk))
    dup = Catalog(items=[_item("a"), _item("a")])
    assert any("duplicate" in p for p in catalog.validate(dup))


def test_gain_bounds_rejected() -> None:
    with pytest.raises(ValueError):
        _item("a", covers={"tech": {"Docker": 1.5}})


def test_topo_order_and_closure() -> None:
    cat = Catalog(items=[_item("linux"), _item("docker", ["linux"]), _item("k8s", ["docker"])])
    assert catalog.prerequisite_closure(cat, {"k8s"}) == {"linux", "docker", "k8s"}
    assert catalog.topo_order(cat, {"k8s", "linux", "docker"}) == ["linux", "docker", "k8s"]


def test_real_catalog_loads_and_is_dag() -> None:
    cat = catalog.load()
    devops = cat.for_role("devops engineer")
    courses = [i for i in devops if i.kind == "course"]
    projects = [i for i in devops if i.kind == "project"]
    order = catalog.topo_order(cat, {i.id for i in devops})
    print(
        f"\nDevOps: {len(courses)} courses, {len(projects)} projects, "
        f"{sum(i.hours for i in devops):.0f} total hours"
    )
    print("Topological order:", " -> ".join(order))
    assert len(projects) >= 3
    pos = {i: n for n, i in enumerate(order)}
    assert pos["linux-fundamentals"] < pos["docker-containers"] < pos["kubernetes-core"]
    assert pos["kubernetes-core"] < pos["helm-charts"] < pos["argocd-gitops"]
    # prerequisites of a role's items must also serve that role
    ids = {i.id for i in devops}
    assert catalog.prerequisite_closure(cat, ids) == ids


ROLES = ["devops engineer", "data analyst", "machine learning engineer"]


def test_covered_tech_names_are_curated_somewhere() -> None:
    """Catches typos: every taught tech must be on some role's curated list."""
    cat = catalog.load()
    curated = {t.lower() for r in ROLES for t in tech_catalog.load_curated(r)}
    unknown = sorted({t for it in cat.items for t in it.covers.tech if t.lower() not in curated})
    assert not unknown, f"catalog tech not on any curated list: {unknown}"


@pytest.mark.parametrize("role", ROLES)
def test_every_curated_tech_is_taught(role: str) -> None:
    cat = catalog.load()
    taught = {t.lower() for it in cat.for_role(role) for t in it.covers.tech}
    missing = [t for t in tech_catalog.load_curated(role) if t.lower() not in taught]
    assert not missing, f"{role}: curated tech with no catalog item: {missing}"


@pytest.mark.parametrize("role", ROLES)
def test_role_catalog_is_closed_and_has_projects(role: str) -> None:
    cat = catalog.load()
    items = cat.for_role(role)
    ids = {i.id for i in items}
    assert catalog.prerequisite_closure(cat, ids) == ids, f"{role}: prereq serves another role"
    projects = [i for i in items if i.kind == "project"]
    assert len(projects) >= 3
    print(
        f"\n{role}: {len(items) - len(projects)} courses, {len(projects)} projects, "
        f"{sum(i.hours for i in items):.0f} h"
    )


@pytest.mark.needs_data
@pytest.mark.parametrize("role", ROLES)
def test_covered_element_names_exist_in_role_blend(role: str) -> None:
    from careercompass import db
    from careercompass.planner import load_role_profile

    con = db.connect()
    socs = [s["soc"] for s in load_role_profile(role)["socs"]]
    names = {r["element_name"] for s in socs for r in db.get_requirements(con, s)}
    cat = catalog.load()
    unknown = sorted({e for it in cat.for_role(role) for e in it.covers.elements} - names)
    assert not unknown, f"{role}: catalog element names not in O*NET blend: {unknown}"
