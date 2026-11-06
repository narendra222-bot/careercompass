"""Configuration settings for CareerCompass loaded from .env."""

from os import getenv
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

ONET_DIR: Path = Path(getenv("ONET_DIR", "data/onet_text"))
ONET_DB: Path = Path(getenv("ONET_DB", "data/onet.duckdb"))
ONET_RELEASE: str = getenv("ONET_RELEASE", "29_1")
LLM_PROVIDER: str = getenv("LLM_PROVIDER", "openai")
LLM_MODEL: str = getenv("LLM_MODEL", "gpt-4o-mini")

__all__ = [
    "ONET_DIR",
    "ONET_DB",
    "ONET_RELEASE",
    "LLM_PROVIDER",
    "LLM_MODEL",
]
