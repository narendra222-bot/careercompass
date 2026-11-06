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

    samples = {
        "roadmap1_devops.json": StudentProfile(
            "Ravi (sample)",
            "CSE",
            3,
            10,
            "devops engineer",
            {"Linux": 0.7, "Bash": 0.6, "Git": 0.7, "Python": 0.5},
        ),
        "roadmap2_data_analyst.json": StudentProfile(
            "Priya (sample)", "B.Com", 3, 8, "data analyst", {"Excel": 0.5}
        ),
        "roadmap3_ml_engineer.json": StudentProfile(
            "Arjun (sample)",
            "CSE",
            4,
            12,
            "machine learning engineer",
            {"Python": 0.6, "Git": 0.6, "SQL": 0.4, "Linux": 0.6},
        ),
    }
    for fname, student in samples.items():
        out = docs / "samples" / fname
        out.write_text(generate_roadmap(student).model_dump_json(indent=2) + "\n")
        print(f"wrote {out.relative_to(ROOT)}")
    print(f"wrote {schema_path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
