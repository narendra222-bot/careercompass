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


def test_covered_tech_names_are_curated() -> None:
    cat = catalog.load()
    curated = {t.lower() for t in tech_catalog.load_curated("devops engineer")}
    unknown = sorted(
        {
            t
            for it in cat.for_role("devops engineer")
            for t in it.covers.tech
            if t.lower() not in curated
        }
    )
    assert not unknown, f"catalog tech not in curated devops list: {unknown}"


def test_every_curated_devops_tech_is_taught() -> None:
    cat = catalog.load()
    taught = {t.lower() for it in cat.for_role("devops engineer") for t in it.covers.tech}
    missing = [t for t in tech_catalog.load_curated("devops engineer") if t.lower() not in taught]
    assert not missing, f"curated tech with no catalog item: {missing}"


@pytest.mark.needs_data
def test_covered_element_names_exist_in_onet() -> None:
    from careercompass import db

    con = db.connect()
    names = {r["element_name"] for s in ALPHAS for r in db.get_requirements(con, s)}
    cat = catalog.load()
    unknown = sorted({e for it in cat.items for e in it.covers.elements} - names)
    print(f"\nunknown element names: {unknown}")
    assert not unknown, f"catalog element names not in O*NET: {unknown}"
