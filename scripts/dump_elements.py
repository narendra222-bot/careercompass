"""Dump the target O*NET element names for the DevOps blend to docs/target_elements.txt.

Blend: 15-1252.00 (0.25), 15-1299.08 (0.45), 15-1244.00 (0.30).
Prints the top 25 skill and knowledge elements by blended importance, the 30 most
common technology examples, and the curated DevOps match table.

Run: .venv/bin/python scripts/dump_elements.py
"""

from __future__ import annotations

import io
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from careercompass import db, scoring, tech_catalog  # noqa: E402

ALPHAS = {"15-1252.00": 0.25, "15-1299.08": 0.45, "15-1244.00": 0.30}
OUT = ROOT / "docs" / "target_elements.txt"


def main() -> None:
    con = db.connect()
    socs = list(ALPHAS)
    reqs = scoring.blend([db.get_requirements(con, s) for s in socs], [ALPHAS[s] for s in socs])
    techs_by_soc = {s: db.get_tech(con, s) for s in socs}

    buf = io.StringIO()
    w = buf.write
    w("TARGET ELEMENTS: DevOps blend " + ", ".join(f"{s}={a}" for s, a in ALPHAS.items()) + "\n")

    for domain in ("skill", "knowledge"):
        rows = sorted(
            (r for r in reqs if r["domain"] == domain), key=lambda r: (-r["imp"], r["element_name"])
        )[:25]
        w(f"\n== TOP 25 {domain.upper()} BY IMP ==\n")
        w(f"{'#':>3}  {'element_name':<36} {'imp':>5} {'lvl':>5}  reliable\n")
        for i, r in enumerate(rows, 1):
            w(
                f"{i:>3}  {r['element_name']:<36} {r['imp']:>5.2f} {r['lvl']:>5.2f}  "
                f"{r['reliable']}\n"
            )

    res = scoring.tech_gap_items(techs_by_soc, ALPHAS, mastery={})
    common = sorted(
        res["items"], key=lambda it: (-it["in_n_occupations"], -it["raw"], it["example"])
    )[:30]
    w("\n== 30 MOST COMMON TECHNOLOGY EXAMPLES ==\n")
    w(f"{'#':>3}  {'example':<42} {'n_occ':>5} {'raw':>6}  commodity_title\n")
    for i, it in enumerate(common, 1):
        w(
            f"{i:>3}  {it['example']:<42} {it['in_n_occupations']:>5} {it['raw']:>6.3f}  "
            f"{it['commodity_title']}\n"
        )

    examples = sorted({t["example"] for ts in techs_by_soc.values() for t in ts})
    curated = tech_catalog.load_curated("devops engineer")
    w("\n== CURATED DEVOPS MATCH TABLE ==\n")
    for row in tech_catalog.match_curated(curated, examples):
        w(f"  {row['curated']:<16} {row['match']:<6} {', '.join(row['examples']) or '-'}\n")

    OUT.write_text(buf.getvalue(), encoding="utf-8")
    print(buf.getvalue())
    print(f"Saved to {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
