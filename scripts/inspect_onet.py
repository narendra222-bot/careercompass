"""Inspection script for O*NET raw text files.

Reads each specified O*NET data file, outputs headers, row counts, sample rows,
and checks for specific columns (Scale ID, Recommend Suppress, Not Relevant, In Demand).
Saves output to docs/onet_inspection.txt and prints to stdout.
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

# Add project root to sys.path so config can be imported directly
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

try:
    from careercompass.config import ONET_DIR
except ImportError:
    ONET_DIR = PROJECT_ROOT / "data" / "onet_text"

FILES_TO_INSPECT = [
    "Occupation Data.txt",
    "Content Model Reference.txt",
    "Scales Reference.txt",
    "Skills.txt",
    "Essential Skills.txt",
    "Transferable Skills.txt",
    "Knowledge.txt",
    "Abilities.txt",
    "Technology Skills.txt",
    "Software Skills.txt",
    "Task Statements.txt",
    "Alternate Titles.txt",
    "Job Titles.txt",
    "Job Zones.txt",
]


def inspect_file(file_path: Path) -> dict:
    """Inspect a single O*NET tab-delimited file."""
    if not file_path.exists():
        return {
            "file": file_path.name,
            "exists": False,
            "error": f"File not found at {file_path}",
        }

    header: list[str] = []
    row_count = 0
    sample_rows: list[list[str]] = []
    scale_ids: set[str] = set()
    recommend_suppress_vals: set[str] = set()
    not_relevant_vals: set[str] = set()

    with file_path.open("r", encoding="utf-8", errors="replace") as f:
        reader = csv.reader(f, delimiter="\t")
        try:
            header = next(reader)
        except StopIteration:
            return {
                "file": file_path.name,
                "exists": True,
                "header": [],
                "row_count": 0,
                "sample_rows": [],
            }

        header_indices = {col: idx for idx, col in enumerate(header)}
        scale_idx = header_indices.get("Scale ID")
        suppress_idx = header_indices.get("Recommend Suppress")
        not_relevant_idx = header_indices.get("Not Relevant")

        for row in reader:
            if not row:
                continue
            row_count += 1
            if len(sample_rows) < 3:
                sample_rows.append(row)

            if scale_idx is not None and len(row) > scale_idx:
                scale_ids.add(row[scale_idx])
            if suppress_idx is not None and len(row) > suppress_idx:
                recommend_suppress_vals.add(row[suppress_idx])
            if not_relevant_idx is not None and len(row) > not_relevant_idx:
                not_relevant_vals.add(row[not_relevant_idx])

    return {
        "file": file_path.name,
        "exists": True,
        "header": header,
        "row_count": row_count,
        "sample_rows": sample_rows,
        "scale_ids": sorted(scale_ids),
        "recommend_suppress_vals": sorted(recommend_suppress_vals),
        "not_relevant_vals": sorted(not_relevant_vals),
    }


def run_inspection(onet_dir: Path | None = None, output_file: Path | None = None) -> str:
    """Run inspection across all target files and compile report."""
    base_dir = onet_dir if onet_dir is not None else ONET_DIR
    out_path = (
        output_file
        if output_file is not None
        else PROJECT_ROOT / "docs" / "onet_inspection.txt"
    )

    lines: list[str] = []
    lines.append("=" * 80)
    lines.append(f"O*NET DATA INSPECTION REPORT (Source Directory: {base_dir})")
    lines.append("=" * 80)

    if not base_dir.exists():
        lines.append(f"\nWARNING: Source directory '{base_dir}' does not exist.")
        report_text = "\n".join(lines)
        print(report_text)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(report_text, encoding="utf-8")
        return report_text

    column_discrepancies: list[str] = []

    optional_v31 = {
        "Essential Skills.txt",
        "Transferable Skills.txt",
        "Software Skills.txt",
        "Job Titles.txt",
    }
    for filename in FILES_TO_INSPECT:
        file_path = base_dir / filename
        info = inspect_file(file_path)

        # For optional 31.0 vs legacy equivalents, skip printing missing if the other is present
        if filename in optional_v31 and not info["exists"]:
            continue

        lines.append(f"\nFILE: {filename}")
        lines.append("-" * 60)

        if not info["exists"]:
            lines.append(f"  Status: MISSING ({info['error']})")
            if filename == "Skills.txt":
                column_discrepancies.append(
                    "Skills.txt: ABSENT in O*NET 31.0 "
                    "(restructured into 'Essential Skills.txt' and 'Transferable Skills.txt')"
                )
            elif filename == "Technology Skills.txt":
                column_discrepancies.append(
                    "Technology Skills.txt: ABSENT in O*NET 31.0 (renamed to 'Software Skills.txt')"
                )
            elif filename == "Alternate Titles.txt":
                column_discrepancies.append(
                    "Alternate Titles.txt: ABSENT in O*NET 31.0 (renamed to 'Job Titles.txt')"
                )
            continue

        header = info["header"]
        lines.append(f"  Exact Header ({len(header)} columns): {header}")
        lines.append(f"  Total Data Rows: {info['row_count']}")
        lines.append("  First 3 Data Rows:")
        for idx, s_row in enumerate(info["sample_rows"], 1):
            lines.append(f"    [{idx}] {s_row}")

        if filename in {"Skills.txt", "Essential Skills.txt", "Transferable Skills.txt"}:
            lines.append(f"  Distinct 'Scale ID' values: {info['scale_ids']}")
            supp_vals = info['recommend_suppress_vals']
            lines.append(f"  Distinct 'Recommend Suppress' values: {supp_vals}")
            lines.append(f"  Distinct 'Not Relevant' values: {info['not_relevant_vals']}")

            if "Recommend Suppress" not in header:
                column_discrepancies.append(f"{filename}: 'Recommend Suppress' column is ABSENT")
            if "Not Relevant" not in header:
                column_discrepancies.append(f"{filename}: 'Not Relevant' column is ABSENT")

        if filename in {"Technology Skills.txt", "Software Skills.txt"}:
            if "In Demand" not in header:
                column_discrepancies.append(f"{filename}: 'In Demand' column is ABSENT")
            else:
                lines.append("  'In Demand' column: PRESENT")
            if "Hot Technology" not in header:
                column_discrepancies.append(f"{filename}: 'Hot Technology' column is ABSENT")
            else:
                lines.append("  'Hot Technology' column: PRESENT")

    lines.append("\n" + "=" * 80)
    lines.append("SCAFFOLD SCHEMA DISCREPANCY SUMMARY")
    lines.append("=" * 80)
    if column_discrepancies:
        for disc in column_discrepancies:
            lines.append(f"  - {disc}")
    else:
        lines.append("  No column discrepancies detected against expected scaffold.")

    report_text = "\n".join(lines) + "\n"

    # Save to target output file
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(report_text, encoding="utf-8")
    print(report_text)

    return report_text


if __name__ == "__main__":
    run_inspection()
