"""Download O*NET 31.0 and build the local data CareerCompass needs.

Steps (each is skipped if its output already exists, use --force to redo):
  1. Download the O*NET 31.0 text release and unzip it to data/onet_text/
  2. Build the DuckDB database data/onet.duckdb
  3. Build the search index (ChromaDB + BM25) in data/chroma/

Works on Linux, macOS and Windows:  python scripts/build_data.py
"""

from __future__ import annotations

import argparse
import io
import os
import shutil
import sys
import urllib.request
import zipfile
from pathlib import Path

os.environ.setdefault("ANONYMIZED_TELEMETRY", "False")  # quiet ChromaDB
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from careercompass import config  # noqa: E402

ONET_URL = "https://www.onetcenter.org/dl_files/database/db_31_0_text.zip"


def download(dest: Path) -> None:
    print(f"Downloading O*NET 31.0 from {ONET_URL} (about 13 MB)...")
    with urllib.request.urlopen(ONET_URL, timeout=120) as r:
        data = r.read()
    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        for info in z.infolist():
            name = Path(info.filename).name  # the zip has one top-level folder; flatten it
            if info.is_dir() or not name.endswith(".txt"):
                continue
            with z.open(info) as src, open(dest / name, "wb") as out:
                shutil.copyfileobj(src, out)
    print(f"Saved {len(list(dest.glob('*.txt')))} files to {dest}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--force", action="store_true", help="rebuild everything")
    args = parser.parse_args()

    onet_dir = ROOT / config.ONET_DIR if not config.ONET_DIR.is_absolute() else config.ONET_DIR
    onet_db = ROOT / config.ONET_DB if not config.ONET_DB.is_absolute() else config.ONET_DB

    if args.force or not (onet_dir / "Occupation Data.txt").exists():
        download(onet_dir)
    else:
        print(f"O*NET text files already in {onet_dir}")

    from careercompass import db, retrieval

    if args.force or not onet_db.exists():
        print(f"Building {onet_db}...")
        db.build(db_path=onet_db, onet_dir=onet_dir)
        db.print_stats()
    else:
        print(f"Database already built: {onet_db}")

    print("Building the search index (first run downloads a small embedding model)...")
    n = retrieval.build(db_path=onet_db)
    print(f"Indexed {n} occupations. Data is ready.")


if __name__ == "__main__":
    main()
