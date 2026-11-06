"""Technology name normalization and curated role technology lists.

O*NET spells technologies with vendor prefixes and suffixes ("IBM Terraform",
"Jenkins CI", "Amazon Web Services AWS software"). Students and curated lists use
plain names ("Terraform", "Jenkins", "AWS"). This module maps between the two so a
declared skill never silently matches nothing.

Matching order for a name against the O*NET examples of a blend:
  1. exact   - case/whitespace-insensitive equality with an example
  2. alias   - TECH_ALIASES entry, or equal canonical_tech() forms
  3. NONE    - no example matched

All curated lists are editorial, human_reviewed: false.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

CURATED_PATH = Path(__file__).resolve().parent.parent / "data" / "tech_curated.yaml"

# Leading vendor names O*NET prepends to product names. Longest first.
VENDOR_PREFIXES: tuple[str, ...] = (
    "amazon web services",
    "grafana labs",
    "red hat",
    "ibm",
)

# Trailing generic words O*NET appends to product names.
GENERIC_SUFFIXES: tuple[str, ...] = (
    "software",
    "ci",
)

# Explicit plain-name -> O*NET example spellings, for cases canonical_tech() cannot
# derive. Keys are lowercase. Verified against O*NET 31.0 for the DevOps blend.
TECH_ALIASES: dict[str, tuple[str, ...]] = {
    "ansible": ("Ansible software", "Red Hat Ansible Engine"),
    "grafana": ("Grafana Labs Grafana Cloud",),
    "aws": ("Amazon Web Services AWS software",),
    "terraform": ("IBM Terraform",),
    "jenkins": ("Jenkins CI",),
    "sql": ("Structured query language SQL",),
    "excel": ("Microsoft Excel",),
    "power bi": ("Microsoft Power BI",),
}


def _norm(name: str) -> str:
    """Lowercase and collapse internal whitespace."""
    return re.sub(r"\s+", " ", name).strip().lower()


def canonical_tech(name: str) -> str:
    """Return a comparison key: lowercase, one vendor prefix and generic suffixes removed.

    canonical_tech("IBM Terraform") == "terraform"
    canonical_tech("Amazon Web Services AWS software") == "aws"
    canonical_tech("Red Hat Enterprise Linux") == "enterprise linux"  (not "linux")
    """
    s = _norm(name)
    for prefix in VENDOR_PREFIXES:
        if s.startswith(prefix + " "):
            s = s[len(prefix) + 1 :]
            break
    changed = True
    while changed:
        changed = False
        for suffix in GENERIC_SUFFIXES:
            if s.endswith(" " + suffix):
                s = s[: -(len(suffix) + 1)]
                changed = True
    return s


def resolve(name: str, examples: list[str]) -> tuple[str, list[str]]:
    """Match a plain technology name to O*NET example names.

    Returns (match_type, matched_examples) with match_type in {"exact", "alias", "NONE"}.
    """
    key = _norm(name)
    exact = [e for e in examples if _norm(e) == key]
    if exact:
        return "exact", exact

    alias_targets = {_norm(a) for a in TECH_ALIASES.get(key, ())}
    canon = canonical_tech(name)
    alias = [e for e in examples if _norm(e) in alias_targets or canonical_tech(e) == canon]
    if alias:
        return "alias", sorted(alias)
    return "NONE", []


def load_curated(role: str, path: Path = CURATED_PATH) -> list[str]:
    """Load the curated technology list for a role profile (case-insensitive role key)."""
    with path.open(encoding="utf-8") as fh:
        data: dict[str, Any] = yaml.safe_load(fh) or {}
    for key, entry in data.items():
        if key.strip().lower() == role.strip().lower():
            return [str(t) for t in entry.get("technologies", [])]
    raise KeyError(f"No curated technology list for role {role!r} in {path}")


def match_curated(curated: list[str], examples: list[str]) -> list[dict[str, Any]]:
    """Return one row per curated technology: {curated, match, examples}."""
    rows: list[dict[str, Any]] = []
    for tech in curated:
        match, matched = resolve(tech, examples)
        rows.append({"curated": tech, "match": match, "examples": matched})
    return rows
