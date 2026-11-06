"""Deterministic roadmap planner: gaps + catalog + weekly hours -> phased plan.

No LLM is involved. Every number in the output is reproducible from O*NET, the
student profile, data/catalog.yaml and the editorial constants below.

Algorithm
  1. Score the student: blended O*NET requirements, degree priors, declared tool
     mastery -> baseline readiness and per-technology gaps.
  2. Select items: every proof-of-work project for the role plus its prerequisite
     closure; then optional courses whose readiness gain per hour clears
     MIN_GAIN_PER_HOUR. Courses whose technologies the student already knows
     (mastery >= SKIP_MASTERY for all of them) are skipped and count as done.
  3. Phase: one phase per project, in prerequisite order. A phase holds the project
     and every not-yet-scheduled item it needs. Leftover optional items form a
     final phase.
  4. Schedule: items run sequentially at hours_per_week; readiness is recomputed
     after each phase by applying the catalog coverage gains.
  5. Feasibility: when max_weeks is given and the plan does not fit, optional items
     are dropped first; if it still does not fit, the plan is returned with
     feasible=False and the hours/week or weeks that would make it fit.

All constants: editorial, human_reviewed: false.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from careercompass import catalog as catalog_mod
from careercompass import priors, scoring, tech_catalog
from careercompass.catalog import Catalog, CatalogItem
from careercompass.scoring import ScoringConfig

ROLE_PROFILES_PATH = Path(__file__).resolve().parent.parent / "data" / "role_profiles.yaml"

SKIP_MASTERY: float = 0.6
MIN_GAIN_PER_HOUR: float = 0.0005
TARGET_READINESS: float = 0.80
READINESS_DECIMALS: int = 4


@dataclass
class StudentProfile:
    name: str
    branch: str
    year: int
    hours_per_week: float
    target_role: str
    tech_mastery: dict[str, float] = field(default_factory=dict)
    max_weeks: int | None = None


@dataclass
class _State:
    prof: dict[str, float]
    mastery: dict[str, float]


def load_role_profile(role: str, path: Path = ROLE_PROFILES_PATH) -> dict[str, Any]:
    with path.open(encoding="utf-8") as fh:
        data: dict[str, Any] = yaml.safe_load(fh) or {}
    for key, entry in data.items():
        if key.strip().lower() == role.strip().lower():
            return {"role": key, **entry}
    raise KeyError(f"no role profile {role!r}")


class Planner:
    def __init__(
        self,
        reqs_by_soc: dict[str, list[dict[str, Any]]],
        techs_by_soc: dict[str, list[dict[str, Any]]],
        alphas: dict[str, float],
        role: str,
        cat: Catalog,
        cfg: ScoringConfig | None = None,
    ) -> None:
        self.alphas = alphas
        self.role = role
        self.cat = cat
        self.cfg = cfg or ScoringConfig()
        socs = list(alphas)
        self.reqs = scoring.blend([reqs_by_soc[s] for s in socs], [alphas[s] for s in socs])
        self.lvl = {r["element_name"]: float(r["lvl"]) for r in self.reqs}
        self.techs_by_soc = techs_by_soc
        self.curated = tech_catalog.load_curated(role)
        base = scoring.tech_gap_items(techs_by_soc, alphas, {}, curated=self.curated)
        self.tech_weight = {it["example"]: it["weight"] for it in base["items"]}
        self.items = {it.id: it for it in cat.for_role(role)}

    # ---- scoring -------------------------------------------------------

    def _tech_key(self, name: str) -> str | None:
        """Map a catalog/student tech name to a tech_gap_items item name."""
        low = name.strip().lower()
        for k in self.tech_weight:
            if k.lower() == low:
                return k
        return None

    def readiness(self, st: _State) -> dict[str, float]:
        cfg = self.cfg
        skill = scoring.domain_readiness(self.reqs, st.prof, "skill", cfg)
        know = scoring.domain_readiness(self.reqs, st.prof, "knowledge", cfg)
        abil = scoring.domain_readiness(self.reqs, st.prof, "ability", cfg)
        tech = sum(w * st.mastery.get(k, 0.0) for k, w in self.tech_weight.items())
        overall = (
            cfg.lambda_skill * skill
            + cfg.lambda_knowledge * know
            + cfg.lambda_ability * abil
            + cfg.lambda_tech * tech
        )
        # Rounded: DB tie order varies, which perturbs float sums in the last bits.
        return {
            "skill": round(skill, READINESS_DECIMALS),
            "knowledge": round(know, READINESS_DECIMALS),
            "ability": round(abil, READINESS_DECIMALS),
            "technology": round(min(1.0, tech), READINESS_DECIMALS),
            "overall": round(min(1.0, overall), READINESS_DECIMALS),
        }

    def apply(self, st: _State, item: CatalogItem) -> _State:
        prof = dict(st.prof)
        mastery = dict(st.mastery)
        for t, gain in item.covers.tech.items():
            key = self._tech_key(t)
            if key is not None:
                mastery[key] = min(1.0, mastery.get(key, 0.0) + gain)
        for e, gain in item.covers.elements.items():
            if e in self.lvl:
                prof[e] = min(7.0, prof.get(e, 0.0) + gain * self.lvl[e])
        return _State(prof, mastery)

    # ---- planning ------------------------------------------------------

    def initial_state(self, student: StudentProfile) -> tuple[_State, list[str]]:
        prof = priors.seed_proficiency(student.branch, student.year, self.reqs)
        mastery: dict[str, float] = {}
        unmatched: list[str] = []
        for name, m in student.tech_mastery.items():
            key = self._tech_key(name)
            if key is None:
                res = scoring.tech_gap_items(
                    self.techs_by_soc, self.alphas, {name: m}, curated=self.curated
                )
                key = next(iter(res["matched"].values()), None)
            if key is None:
                unmatched.append(name)
            else:
                mastery[key] = min(1.0, max(0.0, float(m)))
        return _State(prof, mastery), unmatched

    def _known(self, item: CatalogItem, st: _State) -> bool:
        if item.kind != "course" or not item.covers.tech:
            return False
        keys = [self._tech_key(t) for t in item.covers.tech]
        return all(k is not None and st.mastery.get(k, 0.0) >= SKIP_MASTERY for k in keys)

    def plan(self, student: StudentProfile) -> dict[str, Any]:
        if student.hours_per_week <= 0:
            raise ValueError("hours_per_week must be > 0")
        st0, unmatched = self.initial_state(student)
        r0 = self.readiness(st0)

        skipped = sorted(i for i, it in self.items.items() if self._known(it, st0))
        projects = [i for i, it in self.items.items() if it.kind == "project"]
        core = catalog_mod.prerequisite_closure(self.cat, set(projects)) - set(skipped)
        core &= set(self.items)

        # optional courses ranked by readiness gain per hour on top of the core plan
        st_core = st0
        for i in catalog_mod.topo_order(self.cat, core):
            st_core = self.apply(st_core, self.items[i])
        base_overall = self.readiness(st_core)["overall"]
        optional: list[tuple[float, str]] = []
        for i in self.items:
            if i in core or i in skipped:
                continue
            needs = catalog_mod.prerequisite_closure(self.cat, {i}) - core - set(skipped)
            st = st_core
            for j in catalog_mod.topo_order(self.cat, needs):
                st = self.apply(st, self.items[j])
            hours = sum(self.items[j].hours for j in needs)
            gain = self.readiness(st)["overall"] - base_overall
            if hours > 0 and gain / hours >= MIN_GAIN_PER_HOUR:
                optional.append((gain / hours, i))
        optional.sort(key=lambda x: (-x[0], x[1]))
        extra = set()
        for _, i in optional:
            extra |= catalog_mod.prerequisite_closure(self.cat, {i}) - set(skipped)

        dropped: list[str] = []
        hpw = student.hours_per_week

        def total_hours(ids: set[str]) -> float:
            return sum(self.items[i].hours for i in ids)

        if student.max_weeks is not None:
            budget = hpw * student.max_weeks
            for _, i in reversed(optional):
                if total_hours(core | extra) <= budget:
                    break
                if i in extra:
                    extra.discard(i)
                    dropped.append(i)

        selected = core | extra
        phases = self._phases(selected, projects, st0, hpw)
        total = total_hours(selected)
        weeks = math.ceil(total / hpw)
        feasible = student.max_weeks is None or weeks <= student.max_weeks
        fix = None
        if not feasible and student.max_weeks:
            fix = {
                "needed_weeks_at_current_hours": weeks,
                "needed_hours_per_week_for_max_weeks": math.ceil(total / student.max_weeks),
            }

        return {
            "student": student.__dict__,
            "role": self.role,
            "socs": self.alphas,
            "baseline_readiness": r0,
            "final_readiness": phases[-1]["readiness_after"] if phases else r0,
            "target_readiness": TARGET_READINESS,
            "phases": phases,
            "skipped_known": skipped,
            "dropped_for_time": sorted(dropped),
            "unmatched_tech": unmatched,
            "total_hours": total,
            "total_weeks": weeks,
            "feasible": feasible,
            "feasibility_fix": fix,
            "top_tech_gaps": [
                {"tech": k, "gap": round(w * (1 - st0.mastery.get(k, 0.0)), 4)}
                for k, w in sorted(
                    self.tech_weight.items(),
                    key=lambda kv: (-(kv[1] * (1 - st0.mastery.get(kv[0], 0.0))), kv[0]),
                )[:10]
            ],
        }

    def _phases(
        self, selected: set[str], projects: list[str], st0: _State, hpw: float
    ) -> list[dict[str, Any]]:
        order = catalog_mod.topo_order(self.cat, selected)
        ordered_projects = [i for i in order if i in projects]
        groups: list[list[str]] = []
        scheduled: set[str] = set()
        for p in ordered_projects:
            need = catalog_mod.prerequisite_closure(self.cat, {p}) & selected
            group = [i for i in order if i in need and i not in scheduled]
            scheduled |= set(group)
            groups.append(group)
        rest = [i for i in order if i not in scheduled]
        if rest:
            groups.append(rest)

        phases: list[dict[str, Any]] = []
        st = st0
        cum = 0.0
        for n, group in enumerate(groups, 1):
            items_out = []
            for i in group:
                it = self.items[i]
                start = math.floor(cum / hpw) + 1
                cum += it.hours
                end = math.ceil(cum / hpw)
                st = self.apply(st, it)
                items_out.append(
                    {
                        "id": it.id,
                        "title": it.title,
                        "kind": it.kind,
                        "hours": it.hours,
                        "start_week": start,
                        "end_week": end,
                        "prereqs": [p for p in it.prereqs if p in selected],
                        "resources": [{"title": r.title, "url": str(r.url)} for r in it.resources],
                        "deliverables": it.deliverables,
                        "covers": it.covers.model_dump(),
                    }
                )
            proj = next((x for x in items_out if x["kind"] == "project"), None)
            phases.append(
                {
                    "phase": n,
                    "title": proj["title"] if proj else "Electives",
                    "milestone": proj["id"] if proj else None,
                    "start_week": items_out[0]["start_week"],
                    "end_week": items_out[-1]["end_week"],
                    "hours": sum(x["hours"] for x in items_out),
                    "items": items_out,
                    "readiness_after": self.readiness(st),
                }
            )
        return phases


def build_plan(student: StudentProfile, con: Any = None) -> dict[str, Any]:
    """Convenience entry point: load O*NET data, role profile and catalog, then plan."""
    from careercompass import db

    con = con or db.connect()
    profile = load_role_profile(student.target_role)
    alphas = {s["soc"]: float(s["alpha"]) for s in profile["socs"]}
    reqs_by_soc = {s: db.get_requirements(con, s) for s in alphas}
    techs_by_soc = {s: db.get_tech(con, s) for s in alphas}
    planner = Planner(reqs_by_soc, techs_by_soc, alphas, profile["role"], catalog_mod.load())
    return planner.plan(student)
