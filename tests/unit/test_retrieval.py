"""Unit tests for CareerCompass occupation retrieval module.

All tests require O*NET data and the Chroma/BM25 retrieval index.
"""

from __future__ import annotations

import math
import os
from pathlib import Path

# Silence Chroma telemetry errors
os.environ["ANONYMIZED_TELEMETRY"] = "False"

import pytest

from careercompass import retrieval

pytestmark = pytest.mark.needs_data

SOFTWARE_DEV_SOC = "15-1252.00"
SYSADMIN_SOC = "15-1244.00"


@pytest.fixture(scope="session", autouse=True)
def ensure_retrieval_built() -> None:
    """Ensure ChromaDB and BM25 index are built once per test session."""
    chroma_path = Path("data/chroma")
    if not chroma_path.exists() or not any(chroma_path.iterdir()):
        retrieval.build()


@pytest.mark.slow
def test_build_idempotence(tmp_path: Path) -> None:
    """build() run twice yields identical document count."""
    tmp_chroma = tmp_path / "chroma"
    count1 = retrieval.build(chroma_dir=tmp_chroma)
    count2 = retrieval.build(chroma_dir=tmp_chroma)
    assert count1 == count2
    assert count1 >= 900


def test_search_software_developer_in_top_3() -> None:
    """search('software developer') has 15-1252.00 in top 3."""
    results = retrieval.search("software developer", k=3)
    top_socs = [r["soc"] for r in results]
    assert SOFTWARE_DEV_SOC in top_socs, (
        f"Expected {SOFTWARE_DEV_SOC} in top 3 for 'software developer', got {top_socs}"
    )


def test_search_network_administrator_in_top_3() -> None:
    """search('network administrator') top 3 contains a Network/Systems Administrator SOC."""
    results = retrieval.search("network administrator", k=3)
    top_socs = [r["soc"] for r in results]
    network_admin_socs = {SYSADMIN_SOC, "15-1241.00"}
    matching = [soc for soc in top_socs if soc in network_admin_socs]
    assert len(matching) > 0, (
        f"Expected Network/Systems Administrator SOC in top 3, got {top_socs}"
    )
    assert top_socs[0] == SYSADMIN_SOC


def test_blend_weights_sum_to_one() -> None:
    """blend_weights alphas sum to 1 within 1e-9, and every alpha >= 0.10 or [1.0]."""
    results = retrieval.search("software developer", k=5)
    alphas = retrieval.blend_weights(results, temp=0.04, margin_cutoff=0.10, top=3)
    assert len(alphas) >= 1
    assert math.isclose(sum(alphas), 1.0, rel_tol=1e-9, abs_tol=1e-9)
    if alphas == [1.0]:
        assert True
    else:
        for alpha in alphas:
            assert alpha >= 0.10 - 1e-9
            assert alpha <= 1.0 + 1e-9


def test_curated_devops_profile() -> None:
    """Curated 'devops engineer' returns the 3 expected SOCs with source curated_profile."""
    results = retrieval.search("devops engineer", k=3)
    assert len(results) == 3
    assert all(r["source"] == "curated_profile" for r in results)
    returned_socs = [r["soc"] for r in results]
    expected_socs = ["15-1299.08", "15-1244.00", "15-1252.00"]
    assert returned_socs == expected_socs
    assert math.isclose(sum(r["alpha"] for r in results), 1.0, rel_tol=1e-9)


def test_confidence_escalation() -> None:
    """Confidence escalates for ambiguous queries and does not escalate for clear queries."""
    for q in ["something with data", "computers", "IT job"]:
        conf = retrieval.confidence(q)
        assert conf["escalate"] is True, f"Expected escalation for ambiguous query '{q}', got {conf}"

    for q in ["data scientist", "network administrator"]:
        conf = retrieval.confidence(q)
        assert conf["escalate"] is False, f"Expected no escalation for clear query '{q}', got {conf}"
