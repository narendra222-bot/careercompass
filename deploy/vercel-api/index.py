"""Vercel entrypoint for the CareerCompass API (FastAPI).

The function's files are read-only, so the search index is copied to /tmp on a cold start
(ChromaDB opens it for writing). Everything else is read in place.
"""

import os
import shutil
from pathlib import Path

HERE = Path(__file__).resolve().parent
os.chdir(HERE)  # the app reads data/... relative to the working directory

_chroma = Path("/tmp/chroma")
if not _chroma.exists():
    shutil.copytree(HERE / "data" / "chroma", _chroma)
os.environ.setdefault("CHROMA_PATH", str(_chroma))
os.environ.setdefault("EMBED_BACKEND", "fastembed")
os.environ.setdefault("FASTEMBED_CACHE_PATH", str(HERE / "models"))
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("ANONYMIZED_TELEMETRY", "False")

from careercompass.api import app  # noqa: E402

__all__ = ["app"]
