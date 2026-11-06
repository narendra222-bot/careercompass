"""O*NET DuckDB database connection, schema builder, and query access layer.

Loads O*NET tab-delimited text files into a strongly-typed DuckDB database,
creates ratings tables and requirements aggregation views, and exposes
parameterized query helper functions and a CLI interface.
"""

from __future__ import annotations

import argparse
import csv
import logging
from pathlib import Path
from typing import Any

import duckdb

from careercompass import config

logger = logging.getLogger(__name__)


def _get_file_columns(file_path: Path) -> list[str]:
    """Read header of tab-delimited file to inspect available columns."""
    if not file_path.exists():
        raise FileNotFoundError(f"Required O*NET file not found: {file_path}")
    with file_path.open("r", encoding="utf-8", errors="replace") as f:
        reader = csv.reader(f, delimiter="\t")
        try:
            header = next(reader)
            return [col.strip() for col in header]
        except StopIteration:
            return []


def connect(
    read_only: bool = True,
    db_path: Path | str | None = None,
) -> duckdb.DuckDBPyConnection:
    """Connect to the DuckDB database.

    Args:
        read_only: Whether to open connection in read-only mode.
        db_path: Optional custom path to the DuckDB database file. Defaults to config.ONET_DB.

    Returns:
        duckdb.DuckDBPyConnection object.
    """
    target_path = Path(db_path) if db_path is not None else config.ONET_DB
    if not target_path.parent.exists() and not read_only:
        target_path.parent.mkdir(parents=True, exist_ok=True)
    return duckdb.connect(database=str(target_path), read_only=read_only)


def build(
    db_path: Path | str | None = None,
    onet_dir: Path | str | None = None,
) -> None:
    """Idempotently ingest O*NET text files into DuckDB tables and views.

    Fails loudly (raises an exception) if any required table or view cannot be created.

    Args:
        db_path: Path to target DuckDB file. Defaults to config.ONET_DB.
        onet_dir: Path to directory containing raw O*NET .txt files. Defaults to config.ONET_DIR.
    """
    target_db = Path(db_path) if db_path is not None else config.ONET_DB
    target_dir = Path(onet_dir) if onet_dir is not None else config.ONET_DIR

    if not target_dir.exists():
        raise FileNotFoundError(f"O*NET source directory does not exist: {target_dir}")

    target_db.parent.mkdir(parents=True, exist_ok=True)

    con = duckdb.connect(database=str(target_db), read_only=False)

    try:
        # ------------------------------------------------------------------
        # 1. table occupation(soc, title, description)
        # ------------------------------------------------------------------
        occ_file = target_dir / "Occupation Data.txt"
        if not occ_file.exists():
            raise FileNotFoundError(f"Required file not found: {occ_file}")

        occ_cols = _get_file_columns(occ_file)
        if any(col not in occ_cols for col in ("O*NET-SOC Code", "Title", "Description")):
            raise ValueError(f"Unexpected columns in {occ_file.name}: {occ_cols}")

        con.execute(f"""
            CREATE OR REPLACE TABLE occupation AS
            SELECT
                "O*NET-SOC Code" AS soc,
                "Title" AS title,
                "Description" AS description
            FROM read_csv(
                '{occ_file}',
                delim='\\t',
                header=true,
                quote='',
                all_varchar=true
            )
            ORDER BY soc;
        """)
        occ_count = con.execute("SELECT COUNT(*) FROM occupation").fetchone()
        if not occ_count or occ_count[0] == 0:
            raise RuntimeError("Failed to populate table 'occupation' (row count is 0)")

        # ------------------------------------------------------------------
        # 2. table ratings(domain, soc, element_id, element_name, scale, v, suppress)
        # Handle O*NET 31.0 (Essential Skills + Transferable Skills) & legacy Skills.txt
        # ------------------------------------------------------------------
        rating_source_groups: list[tuple[str, list[Path]]] = []

        # Skills resolution
        skills_file = target_dir / "Skills.txt"
        essential_skills = target_dir / "Essential Skills.txt"
        transferable_skills = target_dir / "Transferable Skills.txt"

        if skills_file.exists():
            rating_source_groups.append(("skill", [skills_file]))
        elif essential_skills.exists() and transferable_skills.exists():
            rating_source_groups.append(("skill", [essential_skills, transferable_skills]))
        elif essential_skills.exists():
            rating_source_groups.append(("skill", [essential_skills]))
        else:
            raise FileNotFoundError(
                f"Skills data not found in {target_dir} (expected 'Skills.txt' or "
                f"'Essential Skills.txt' and 'Transferable Skills.txt')"
            )

        # Knowledge resolution
        knowledge_file = target_dir / "Knowledge.txt"
        if not knowledge_file.exists():
            raise FileNotFoundError(f"Required file not found: {knowledge_file}")
        rating_source_groups.append(("knowledge", [knowledge_file]))

        # Abilities resolution
        abilities_file = target_dir / "Abilities.txt"
        if not abilities_file.exists():
            raise FileNotFoundError(f"Required file not found: {abilities_file}")
        rating_source_groups.append(("ability", [abilities_file]))

        union_queries: list[str] = []
        for domain, files in rating_source_groups:
            for r_file in files:
                cols = _get_file_columns(r_file)
                required_cols = [
                    "O*NET-SOC Code",
                    "Element ID",
                    "Element Name",
                    "Scale ID",
                    "Data Value",
                ]
                missing = [c for c in required_cols if c not in cols]
                if missing:
                    raise ValueError(f"Missing required columns {missing} in {r_file.name}")

                has_suppress = "Recommend Suppress" in cols
                has_not_relevant = "Not Relevant" in cols

                suppress_conditions: list[str] = []
                if has_suppress:
                    suppress_conditions.append('"Recommend Suppress" = \'Y\'')
                if has_not_relevant:
                    suppress_conditions.append('"Not Relevant" = \'Y\'')

                if suppress_conditions:
                    suppress_expr = f"({' OR '.join(suppress_conditions)})"
                else:
                    suppress_expr = "FALSE"

                union_queries.append(f"""
                    SELECT
                        '{domain}' AS domain,
                        "O*NET-SOC Code" AS soc,
                        "Element ID" AS element_id,
                        "Element Name" AS element_name,
                        "Scale ID" AS scale,
                        TRY_CAST("Data Value" AS DOUBLE) AS v,
                        CASE WHEN {suppress_expr} THEN TRUE ELSE FALSE END AS suppress
                    FROM read_csv(
                        '{r_file}',
                        delim='\\t',
                        header=true,
                        quote='',
                        all_varchar=true
                    )
                """)

        full_ratings_query = " UNION ALL ".join(union_queries)
        con.execute(f"""
            CREATE OR REPLACE TABLE ratings AS
            {full_ratings_query}
            ORDER BY soc, domain, element_id, scale;
        """)
        ratings_count = con.execute("SELECT COUNT(*) FROM ratings").fetchone()
        if not ratings_count or ratings_count[0] == 0:
            raise RuntimeError("Failed to populate table 'ratings' (row count is 0)")

        # ------------------------------------------------------------------
        # 3. view requirements(soc, domain, element_id, element_name, imp, lvl, suppress_im, suppress_lv, reliable, suppress)
        # ------------------------------------------------------------------
        con.execute("""
            CREATE OR REPLACE VIEW requirements AS
            SELECT
                soc,
                domain,
                element_id,
                element_name,
                MAX(CASE WHEN scale = 'IM' THEN v END) AS imp,
                MAX(CASE WHEN scale = 'LV' THEN v END) AS lvl,
                BOOL_OR(suppress) FILTER (WHERE scale = 'IM') AS suppress_im,
                BOOL_OR(suppress) FILTER (WHERE scale = 'LV') AS suppress_lv,
                (NOT COALESCE(BOOL_OR(suppress) FILTER (WHERE scale = 'IM'), false)
                 AND NOT COALESCE(BOOL_OR(suppress) FILTER (WHERE scale = 'LV'), false)) AS reliable,
                NOT (NOT COALESCE(BOOL_OR(suppress) FILTER (WHERE scale = 'IM'), false)
                     AND NOT COALESCE(BOOL_OR(suppress) FILTER (WHERE scale = 'LV'), false)) AS suppress
            FROM ratings
            GROUP BY soc, domain, element_id, element_name;
        """)
        reqs_count = con.execute("SELECT COUNT(*) FROM requirements").fetchone()
        if not reqs_count or reqs_count[0] == 0:
            raise RuntimeError("Failed to populate view 'requirements' (row count is 0)")

        # ------------------------------------------------------------------
        # 4. table technology(soc, example, commodity_code, commodity_title, hot, in_demand)
        # Handle Technology Skills.txt (legacy) and Software Skills.txt (O*NET 31.0)
        # ------------------------------------------------------------------
        legacy_tech = target_dir / "Technology Skills.txt"
        v31_tech = target_dir / "Software Skills.txt"
        if legacy_tech.exists():
            tech_file = legacy_tech
        elif v31_tech.exists():
            tech_file = v31_tech
        else:
            raise FileNotFoundError(
                f"Technology skills file not found in {target_dir} "
                f"(expected 'Technology Skills.txt' or 'Software Skills.txt')"
            )

        tech_cols = _get_file_columns(tech_file)
        if "O*NET-SOC Code" not in tech_cols:
            raise ValueError(f"'O*NET-SOC Code' not found in {tech_file.name}")

        if "Workplace Example" in tech_cols:
            example_expr = '"Workplace Example"'
        elif "Example" in tech_cols:
            example_expr = '"Example"'
        else:
            raise ValueError(f"Neither 'Workplace Example' nor 'Example' found in {tech_file.name}")

        if "Commodity Code" in tech_cols:
            comm_code_expr = '"Commodity Code"'
        elif "Element ID" in tech_cols:
            comm_code_expr = '"Element ID"'
        else:
            comm_code_expr = "CAST(NULL AS VARCHAR)"

        if "Commodity Title" in tech_cols:
            comm_title_expr = '"Commodity Title"'
        elif "Element Name" in tech_cols:
            comm_title_expr = '"Element Name"'
        else:
            comm_title_expr = "CAST(NULL AS VARCHAR)"

        if "Hot Technology" in tech_cols:
            hot_expr = "CASE WHEN \"Hot Technology\" = 'Y' THEN TRUE ELSE FALSE END"
        else:
            hot_expr = "FALSE"

        if "In Demand" in tech_cols:
            in_demand_expr = "CASE WHEN \"In Demand\" = 'Y' THEN TRUE ELSE FALSE END"
        else:
            logger.warning(
                "Column 'In Demand' not found in %s; defaulting in_demand to FALSE.", tech_file.name
            )
            in_demand_expr = "FALSE"

        con.execute(f"""
            CREATE OR REPLACE TABLE technology AS
            SELECT
                "O*NET-SOC Code" AS soc,
                {example_expr} AS example,
                {comm_code_expr} AS commodity_code,
                {comm_title_expr} AS commodity_title,
                {hot_expr} AS hot,
                {in_demand_expr} AS in_demand
            FROM read_csv(
                '{tech_file}',
                delim='\\t',
                header=true,
                quote='',
                all_varchar=true
            )
            ORDER BY soc, example;
        """)
        tech_count = con.execute("SELECT COUNT(*) FROM technology").fetchone()
        if not tech_count or tech_count[0] == 0:
            raise RuntimeError("Failed to populate table 'technology' (row count is 0)")

        # ------------------------------------------------------------------
        # 5. table alt_titles(soc, title)
        # Handle Alternate Titles.txt (legacy) and Job Titles.txt (O*NET 31.0)
        # ------------------------------------------------------------------
        alt_legacy = target_dir / "Alternate Titles.txt"
        alt_v31 = target_dir / "Job Titles.txt"
        if alt_legacy.exists():
            alt_file = alt_legacy
            alt_cols = _get_file_columns(alt_file)
            title_col = '"Alternate Title"' if "Alternate Title" in alt_cols else '"Title"'
        elif alt_v31.exists():
            alt_file = alt_v31
            alt_cols = _get_file_columns(alt_file)
            title_col = '"Job Title"' if "Job Title" in alt_cols else '"Title"'
        else:
            raise FileNotFoundError(
                f"Titles file not found in {target_dir} "
                f"(expected 'Alternate Titles.txt' or 'Job Titles.txt')"
            )

        con.execute(f"""
            CREATE OR REPLACE TABLE alt_titles AS
            SELECT
                "O*NET-SOC Code" AS soc,
                {title_col} AS title
            FROM read_csv(
                '{alt_file}',
                delim='\\t',
                header=true,
                quote='',
                all_varchar=true
            )
            ORDER BY soc, title;
        """)
        alt_count = con.execute("SELECT COUNT(*) FROM alt_titles").fetchone()
        if not alt_count or alt_count[0] == 0:
            raise RuntimeError("Failed to populate table 'alt_titles' (row count is 0)")

        # ------------------------------------------------------------------
        # 6. table tasks(soc, task_id, task, task_type)
        # ------------------------------------------------------------------
        task_file = target_dir / "Task Statements.txt"
        if not task_file.exists():
            raise FileNotFoundError(f"Required file not found: {task_file}")

        con.execute(f"""
            CREATE OR REPLACE TABLE tasks AS
            SELECT
                "O*NET-SOC Code" AS soc,
                "Task ID" AS task_id,
                "Task" AS task,
                "Task Type" AS task_type
            FROM read_csv(
                '{task_file}',
                delim='\\t',
                header=true,
                quote='',
                all_varchar=true
            )
            ORDER BY soc, task_id;
        """)
        task_count = con.execute("SELECT COUNT(*) FROM tasks").fetchone()
        if not task_count or task_count[0] == 0:
            raise RuntimeError("Failed to populate table 'tasks' (row count is 0)")

        # ------------------------------------------------------------------
        # 7. table job_zone(soc, job_zone)
        # ------------------------------------------------------------------
        jz_file = target_dir / "Job Zones.txt"
        if not jz_file.exists():
            raise FileNotFoundError(f"Required file not found: {jz_file}")

        con.execute(f"""
            CREATE OR REPLACE TABLE job_zone AS
            SELECT
                "O*NET-SOC Code" AS soc,
                TRY_CAST("Job Zone" AS INTEGER) AS job_zone
            FROM read_csv(
                '{jz_file}',
                delim='\\t',
                header=true,
                quote='',
                all_varchar=true
            )
            ORDER BY soc;
        """)
        jz_count = con.execute("SELECT COUNT(*) FROM job_zone").fetchone()
        if not jz_count or jz_count[0] == 0:
            raise RuntimeError("Failed to populate table 'job_zone' (row count is 0)")

        # ------------------------------------------------------------------
        # 8. table content_model(element_id, element_name, description)
        # ------------------------------------------------------------------
        cm_file = target_dir / "Content Model Reference.txt"
        if not cm_file.exists():
            raise FileNotFoundError(f"Required file not found: {cm_file}")

        con.execute(f"""
            CREATE OR REPLACE TABLE content_model AS
            SELECT
                "Element ID" AS element_id,
                "Element Name" AS element_name,
                "Description" AS description
            FROM read_csv(
                '{cm_file}',
                delim='\\t',
                header=true,
                quote='',
                all_varchar=true
            )
            ORDER BY element_id;
        """)
        cm_count = con.execute("SELECT COUNT(*) FROM content_model").fetchone()
        if not cm_count or cm_count[0] == 0:
            raise RuntimeError("Failed to populate table 'content_model' (row count is 0)")

    finally:
        con.close()


def get_requirements(
    con: duckdb.DuckDBPyConnection,
    soc: str,
    include_unreliable: bool = True,
) -> list[dict[str, Any]]:
    """Retrieve skill/knowledge/ability requirements for a SOC code.

    Includes rows where imp and lvl are NOT NULL, and includes the reliable field.
    If include_unreliable=False, filters to reliable only.
    Uses parameterized query.
    """
    filter_reliable = "" if include_unreliable else "AND reliable = TRUE"
    rel = con.execute(
        f"""
        SELECT
            soc,
            domain,
            element_id,
            element_name,
            imp,
            lvl,
            suppress_im,
            suppress_lv,
            reliable,
            suppress
        FROM requirements
        WHERE soc = ?
          AND imp IS NOT NULL
          AND lvl IS NOT NULL
          {filter_reliable}
        ORDER BY domain, imp DESC, lvl DESC
        """,
        [soc],
    )
    cols = [desc[0] for desc in rel.description] if rel.description else []
    return [dict(zip(cols, row, strict=True)) for row in rel.fetchall()]


def get_tech(
    con: duckdb.DuckDBPyConnection,
    soc: str,
) -> list[dict[str, Any]]:
    """Retrieve technology skills for a given SOC code.

    Uses parameterized query.
    """
    rel = con.execute(
        """
        SELECT
            soc,
            example,
            commodity_code,
            commodity_title,
            hot,
            in_demand
        FROM technology
        WHERE soc = ?
        ORDER BY hot DESC, example ASC
        """,
        [soc],
    )
    cols = [desc[0] for desc in rel.description] if rel.description else []
    return [dict(zip(cols, row, strict=True)) for row in rel.fetchall()]


def get_occupation(
    con: duckdb.DuckDBPyConnection,
    soc: str,
) -> dict[str, Any] | None:
    """Retrieve occupation metadata for a given SOC code.

    Uses parameterized query.
    """
    rel = con.execute(
        """
        SELECT soc, title, description
        FROM occupation
        WHERE soc = ?
        """,
        [soc],
    )
    row = rel.fetchone()
    if row is None:
        return None
    cols = [desc[0] for desc in rel.description] if rel.description else []
    return dict(zip(cols, row, strict=True))


def list_occupations(
    con: duckdb.DuckDBPyConnection,
) -> list[dict[str, Any]]:
    """Retrieve list of all occupations."""
    rel = con.execute(
        """
        SELECT soc, title, description
        FROM occupation
        ORDER BY soc ASC
        """
    )
    cols = [desc[0] for desc in rel.description] if rel.description else []
    return [dict(zip(cols, row, strict=True)) for row in rel.fetchall()]


def get_stats(con: duckdb.DuckDBPyConnection) -> dict[str, int]:
    """Retrieve row counts for all tables and views."""
    tables = [
        "occupation",
        "ratings",
        "requirements",
        "technology",
        "alt_titles",
        "tasks",
        "job_zone",
        "content_model",
    ]
    stats: dict[str, int] = {}
    for tbl in tables:
        count_res = con.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()
        stats[tbl] = count_res[0] if count_res else 0
    return stats


def print_stats() -> None:
    """Print database statistics to stdout."""
    con = connect(read_only=True)
    try:
        stats = get_stats(con)
        print("=" * 45)
        print("O*NET DuckDB Database Row Counts")
        print("=" * 45)
        for tbl, count in stats.items():
            print(f"  {tbl:<18}: {count:,}")
        print("=" * 45)
    finally:
        con.close()


def main() -> None:
    """CLI entrypoint for careercompass.db."""
    parser = argparse.ArgumentParser(description="CareerCompass O*NET Database CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("build", help="Build O*NET DuckDB database from raw text files")
    subparsers.add_parser("stats", help="Show row counts for all database tables and views")

    args = parser.parse_args()

    if args.command == "build":
        print(f"Building O*NET DuckDB database at: {config.ONET_DB}...")
        build()
        print("Build complete.")
        print_stats()
    elif args.command == "stats":
        print_stats()


if __name__ == "__main__":
    main()
