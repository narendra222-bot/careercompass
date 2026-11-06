"""Unit tests for CareerCompass O*NET DuckDB layer.

All tests require O*NET data and are marked with @pytest.mark.needs_data.
"""

from __future__ import annotations

import re
from collections.abc import Generator
from pathlib import Path

import duckdb
import pytest

from careercompass import config, db

pytestmark = pytest.mark.needs_data

SOC_PATTERN = re.compile(r"^\d{2}-\d{4}\.\d{2}$")
SOFTWARE_DEV_SOC = "15-1252.00"


@pytest.fixture(scope="module")
def con() -> Generator[duckdb.DuckDBPyConnection, None, None]:
    """Ensure database is built and provide read-only connection."""
    # Build database if not already built
    if not config.ONET_DB.exists():
        db.build()

    connection = db.connect(read_only=True)
    yield connection
    connection.close()


def test_occupation_count(con: duckdb.DuckDBPyConnection) -> None:
    """Occupation count must be greater than 900."""
    res = con.execute("SELECT COUNT(*) FROM occupation").fetchone()
    assert res is not None
    count = res[0]
    assert count > 900, f"Expected >900 occupations, found {count}"


def test_software_developer_exists(con: duckdb.DuckDBPyConnection) -> None:
    """SOC '15-1252.00' exists and its title contains 'Software'."""
    occ = db.get_occupation(con, SOFTWARE_DEV_SOC)
    assert occ is not None, f"Occupation {SOFTWARE_DEV_SOC} not found"
    assert "Software" in occ["title"], f"Expected 'Software' in title, got '{occ['title']}'"


def test_software_developer_requirements_count(con: duckdb.DuckDBPyConnection) -> None:
    """For 15-1252.00, requirements returns >= 30 skill+knowledge rows."""
    reqs = db.get_requirements(con, SOFTWARE_DEV_SOC)
    skill_and_knowledge = [r for r in reqs if r["domain"] in {"skill", "knowledge"}]
    assert len(skill_and_knowledge) >= 30, (
        f"Expected >= 30 skill+knowledge requirements for {SOFTWARE_DEV_SOC}, "
        f"got {len(skill_and_knowledge)}"
    )


def test_requirements_value_ranges(con: duckdb.DuckDBPyConnection) -> None:
    """Every imp is in [1, 5] and every lvl is in [0, 7]."""
    reqs = db.get_requirements(con, SOFTWARE_DEV_SOC)
    assert len(reqs) > 0, f"No requirements found for {SOFTWARE_DEV_SOC}"

    for r in reqs:
        imp = r["imp"]
        lvl = r["lvl"]
        assert imp is not None, f"imp is None for {r}"
        assert lvl is not None, f"lvl is None for {r}"
        assert 1.0 <= imp <= 5.0, f"imp {imp} outside [1, 5] for {r}"
        assert 0.0 <= lvl <= 7.0, f"lvl {lvl} outside [0, 7] for {r}"


def test_no_suppressed_requirements_returned(con: duckdb.DuckDBPyConnection) -> None:
    """When include_unreliable=False, no returned row has suppress = TRUE or reliable = FALSE."""
    reqs = db.get_requirements(con, SOFTWARE_DEV_SOC, include_unreliable=False)
    for r in reqs:
        assert r["suppress"] is False, f"Suppressed row returned: {r}"
        assert r["reliable"] is True, f"Unreliable row returned: {r}"


def test_programming_present_for_software_developer(con: duckdb.DuckDBPyConnection) -> None:
    """Programming must be present for 15-1252.00 with non-null imp and lvl at include_unreliable=True."""
    reqs = db.get_requirements(con, SOFTWARE_DEV_SOC, include_unreliable=True)
    programming_rows = [r for r in reqs if r["element_name"] == "Programming"]
    assert len(programming_rows) == 1, f"Expected 1 Programming row, found {len(programming_rows)}"
    prog = programming_rows[0]
    assert prog["imp"] is not None, f"Expected non-null imp, got {prog}"
    assert prog["lvl"] is not None, f"Expected non-null lvl, got {prog}"
    print(
        f"Programming values: reliable={prog['reliable']}, "
        f"suppress_im={prog['suppress_im']}, suppress_lv={prog['suppress_lv']}"
    )


def test_include_unreliable_filtering(con: duckdb.DuckDBPyConnection) -> None:
    """include_unreliable=False returns fewer or equal rows than True."""
    reqs_all = db.get_requirements(con, SOFTWARE_DEV_SOC, include_unreliable=True)
    reqs_filtered = db.get_requirements(con, SOFTWARE_DEV_SOC, include_unreliable=False)
    assert len(reqs_filtered) <= len(reqs_all), (
        f"Expected len(reqs_filtered) <= len(reqs_all), got {len(reqs_filtered)} > {len(reqs_all)}"
    )


def test_technology_hot(con: duckdb.DuckDBPyConnection) -> None:
    """Technology for 15-1252.00 is non-empty and contains at least one row with hot=TRUE."""
    tech = db.get_tech(con, SOFTWARE_DEV_SOC)
    assert len(tech) > 0, f"Technology list empty for {SOFTWARE_DEV_SOC}"
    hot_items = [t for t in tech if t["hot"] is True]
    assert len(hot_items) >= 1, f"Expected at least one hot technology for {SOFTWARE_DEV_SOC}"


def test_build_idempotence(tmp_path: Path) -> None:
    """build() run twice yields identical row counts."""
    test_db = tmp_path / "idempotence.duckdb"
    db.build(db_path=test_db)
    con_before = db.connect(read_only=True, db_path=test_db)
    stats_before = db.get_stats(con_before)
    con_before.close()

    # Re-run build
    db.build(db_path=test_db)

    con_after = db.connect(read_only=True, db_path=test_db)
    stats_after = db.get_stats(con_after)
    con_after.close()

    assert stats_before == stats_after, (
        f"Row counts differed after rebuild:\nBefore: {stats_before}\nAfter: {stats_after}"
    )


def test_soc_pattern(con: duckdb.DuckDBPyConnection) -> None:
    r"""Every soc matches ^\d{2}-\d{4}\.\d{2}$."""
    occupations = db.list_occupations(con)
    assert len(occupations) > 0, "No occupations retrieved"

    invalid_socs = [
        occ["soc"] for occ in occupations if not SOC_PATTERN.match(occ["soc"])
    ]
    assert len(invalid_socs) == 0, f"Found occupations with invalid SOC format: {invalid_socs[:10]}"
