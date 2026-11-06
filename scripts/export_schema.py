"""Write the roadmap JSON Schema and the Roadmap 1 sample for the frontend.

Outputs
  docs/roadmap.schema.json           JSON Schema of schemas.Roadmap
  docs/samples/roadmap1_devops.json  Tier-3 CSE student -> DevOps Engineer

Run: .venv/bin/python scripts/export_schema.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from careercompass.planner import StudentProfile, generate_roadmap  # noqa: E402
from careercompass.schemas import Roadmap  # noqa: E402


def main() -> None:
    docs = ROOT / "docs"
    (docs / "samples").mkdir(parents=True, exist_ok=True)
    schema_path = docs / "roadmap.schema.json"
    schema_path.write_text(json.dumps(Roadmap.model_json_schema(), indent=2) + "\n")

    student = StudentProfile(
        name="Ravi (sample)",
        branch="CSE",
        year=3,
        hours_per_week=10,
        target_role="devops engineer",
        tech_mastery={"Linux": 0.7, "Bash": 0.6, "Git": 0.7, "Python": 0.5},
    )
    sample_path = docs / "samples" / "roadmap1_devops.json"
    sample_path.write_text(generate_roadmap(student).model_dump_json(indent=2) + "\n")
    print(f"wrote {schema_path.relative_to(ROOT)} and {sample_path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
