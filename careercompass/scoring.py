"""Deterministic scoring and gap analysis module for O*NET occupations and skills.

Pure functions implementing weight computation, coverage, multi-occupation blending,
domain readiness, tech readiness, overall readiness, gap analysis, proficiency estimation,
and sensitivity perturbation analysis.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ScoringConfig:
    """Scoring parameters for deterministic readiness model."""

    gamma: float = 1.5
    unreliable_discount: float = 0.5
    lambda_skill: float = 0.30
    lambda_knowledge: float = 0.25
    lambda_ability: float = 0.10
    lambda_tech: float = 0.35
    rho: float = 0.85


def weight(
    imp: float,
    reliable: bool,
    cfg: ScoringConfig = ScoringConfig(),
) -> float:
    """Compute requirement element importance weight.

    weight(imp, reliable) = ((imp-1)/4)**gamma * (1.0 if reliable else discount), >= 0.
    """
    if imp <= 1.0:
        return 0.0
    normalized_imp = (imp - 1.0) / 4.0
    discount = 1.0 if reliable else cfg.unreliable_discount
    w = (normalized_imp ** cfg.gamma) * discount
    return max(0.0, float(w))


def coverage(p: float, lvl: float) -> float:
    """Compute requirement element level coverage.

    coverage(p, lvl) = 1.0 if lvl <= 0.5 else min(1.0, p/lvl).
    """
    if lvl <= 0.5:
        return 1.0
    if p <= 0.0:
        return 0.0
    return min(1.0, float(p / lvl))


def blend(
    req_sets: list[list[dict[str, Any]]],
    alphas: list[float],
) -> list[dict[str, Any]]:
    """Alpha-weighted blend of requirements per (domain, element_name).

    Missing element in an occupation counts as imp=1, lvl=0, but is EXCLUDED from
    the reliability check. A blended element is reliable iff every occupation that
    actually contains the row (and has alpha >= 0.10) marks it reliable.

    Raises ValueError unless alphas sum to 1 within 1e-9 and are all >= 0.
    """
    if len(req_sets) != len(alphas):
        raise ValueError(
            f"Length mismatch: {len(req_sets)} requirement sets vs {len(alphas)} alphas"
        )
    if not alphas:
        raise ValueError("alphas cannot be empty")
    if any(a < 0.0 for a in alphas):
        raise ValueError("alphas must all be >= 0")
    if not math.isclose(sum(alphas), 1.0, rel_tol=1e-9, abs_tol=1e-9):
        raise ValueError(f"alphas must sum to 1 within 1e-9; sum is {sum(alphas)}")

    # Collect all unique elements
    elements_map: dict[tuple[str, str], dict[str, Any]] = {}

    for reqs in req_sets:
        for r in reqs:
            key = (r["domain"], r["element_name"])
            if key not in elements_map:
                elements_map[key] = {
                    "domain": r["domain"],
                    "element_name": r["element_name"],
                    "element_id": r.get("element_id", ""),
                }

    blended_results: list[dict[str, Any]] = []

    for key, base_info in elements_map.items():
        domain, element_name = key
        blended_imp = 0.0
        blended_lvl = 0.0
        element_id = base_info["element_id"]

        reliable_in_all = True
        present_in_qualifying_soc = False

        for reqs, alpha in zip(req_sets, alphas, strict=False):
            found_row = None
            for r in reqs:
                if r["domain"] == domain and r["element_name"] == element_name:
                    found_row = r
                    break

            if found_row is not None:
                blended_imp += alpha * float(found_row["imp"])
                blended_lvl += alpha * float(found_row["lvl"])
                if not element_id and found_row.get("element_id"):
                    element_id = found_row["element_id"]
                if alpha >= 0.10:
                    present_in_qualifying_soc = True
                    if not bool(found_row.get("reliable", True)):
                        reliable_in_all = False
            else:
                # Missing element counts as imp=1, lvl=0, excluded from reliability check
                blended_imp += alpha * 1.0
                blended_lvl += alpha * 0.0

        is_reliable = reliable_in_all if present_in_qualifying_soc else True

        blended_results.append(
            {
                "domain": domain,
                "element_id": element_id,
                "element_name": element_name,
                "imp": blended_imp,
                "lvl": blended_lvl,
                "reliable": is_reliable,
            }
        )

    return blended_results


def blend_tech(
    tech_sets: list[list[dict[str, Any]]],
    alphas: list[float],
) -> list[dict[str, Any]]:
    """Aggregate technology examples across occupations with alpha-weighted hot/in_demand."""
    if len(tech_sets) != len(alphas):
        raise ValueError(
            f"Length mismatch: {len(tech_sets)} tech sets vs {len(alphas)} alphas"
        )
    if not alphas:
        raise ValueError("alphas cannot be empty")
    if any(a < 0.0 for a in alphas):
        raise ValueError("alphas must all be >= 0")
    if not math.isclose(sum(alphas), 1.0, rel_tol=1e-9, abs_tol=1e-9):
        raise ValueError(f"alphas must sum to 1 within 1e-9; sum is {sum(alphas)}")

    tech_map: dict[str, dict[str, Any]] = {}

    for t_set, alpha in zip(tech_sets, alphas, strict=False):
        for t in t_set:
            ex = t["example"]
            if ex not in tech_map:
                tech_map[ex] = {
                    "example": ex,
                    "commodity_code": t.get("commodity_code", ""),
                    "commodity_title": t.get("commodity_title", ""),
                    "hot_weight": 0.0,
                    "in_demand_weight": 0.0,
                }
            tech_map[ex]["hot_weight"] += alpha * (1.0 if t.get("hot") else 0.0)
            tech_map[ex]["in_demand_weight"] += alpha * (
                1.0 if t.get("in_demand") else 0.0
            )

    blended_tech: list[dict[str, Any]] = []
    for ex_info in tech_map.values():
        blended_tech.append(
            {
                "example": ex_info["example"],
                "commodity_code": ex_info["commodity_code"],
                "commodity_title": ex_info["commodity_title"],
                "hot": ex_info["hot_weight"],
                "in_demand": ex_info["in_demand_weight"],
            }
        )

    return blended_tech


def domain_readiness(
    reqs: list[dict[str, Any]],
    prof: dict[str, float],
    domain: str,
    cfg: ScoringConfig = ScoringConfig(),
) -> float:
    """Compute domain readiness.

    domain_readiness(reqs, prof, domain) = sum(w*c)/sum(w).
    Returns 0.0 if no rows for domain; returns 1.0 if rows exist but sum(w) == 0.
    """
    domain_rows = [r for r in reqs if r["domain"] == domain]
    if not domain_rows:
        return 0.0

    total_w = 0.0
    weighted_cov = 0.0

    for r in domain_rows:
        w = weight(r["imp"], r["reliable"], cfg)
        p = prof.get(r["element_name"], 0.0)
        c = coverage(p, r["lvl"])
        total_w += w
        weighted_cov += w * c

    if total_w == 0.0:
        return 1.0

    return min(1.0, max(0.0, float(weighted_cov / total_w)))


def tech_readiness(
    techs: list[dict[str, Any]],
    mastery: dict[str, float],
) -> float:
    """Compute technology readiness.

    u = 1 + 0.5*hot + 0.5*in_demand.
    Grouped by commodity_title, group weight W = min(3, sum u),
    readiness = sum(W * weighted-mean mastery)/sum(W). Mastery in 0..1.
    Normalizes mastery keys (case and whitespace trimmed).
    """
    if not techs:
        return 0.0

    # Normalize mastery keys
    norm_mastery = {k.strip().lower(): float(v) for k, v in mastery.items()}

    groups: dict[str, list[tuple[float, float]]] = {}
    for t in techs:
        comm = t.get("commodity_title") or "Unknown"
        hot_val = float(t.get("hot", 0.0))
        in_demand_val = float(t.get("in_demand", 0.0))
        u = 1.0 + 0.5 * hot_val + 0.5 * in_demand_val

        ex_name = t.get("example", "")
        m = norm_mastery.get(ex_name.strip().lower(), 0.0)
        m_clamped = min(1.0, max(0.0, float(m)))

        if comm not in groups:
            groups[comm] = []
        groups[comm].append((u, m_clamped))

    total_group_W = 0.0
    weighted_group_readiness = 0.0

    for comm, items in groups.items():
        sum_u = sum(u for u, _ in items)
        if sum_u <= 0.0:
            continue
        group_mastery = sum(u * m for u, m in items) / sum_u
        W = min(3.0, sum_u)
        total_group_W += W
        weighted_group_readiness += W * group_mastery

    if total_group_W == 0.0:
        return 0.0

    return min(1.0, max(0.0, float(weighted_group_readiness / total_group_W)))


def tech_gaps(
    techs: list[dict[str, Any]],
    mastery: dict[str, float],
    cfg: ScoringConfig = ScoringConfig(),
) -> tuple[list[dict[str, Any]], list[str]]:
    """Compute technology gaps grouped by commodity_title.

    gap = W * (1 - group_mastery), where W = min(3, sum u), u = 1 + 0.5*hot + 0.5*in_demand.
    top_examples = up to 5 highest-u examples in the group (hot first).
    Normalizes mastery keys (case and whitespace trimmed).

    Returns:
        (gap_list, unmatched_mastery_keys)
        gap_list is sorted desc by gap, tie-break by commodity_title asc.
    """
    if not techs:
        return [], list(mastery.keys())

    norm_mastery = {k.strip().lower(): (k, float(v)) for k, v in mastery.items()}
    matched_norm_keys: set[str] = set()

    # Group tech items by commodity_title
    # Keep item records: {example, hot, in_demand, u, mastery}
    groups: dict[str, list[dict[str, Any]]] = {}

    for t in techs:
        comm = t.get("commodity_title") or "Unknown"
        ex_name = t.get("example", "")
        ex_norm = ex_name.strip().lower()

        hot_val = float(t.get("hot", 0.0))
        in_demand_val = float(t.get("in_demand", 0.0))
        u = 1.0 + 0.5 * hot_val + 0.5 * in_demand_val

        if ex_norm in norm_mastery:
            matched_norm_keys.add(ex_norm)
            m_val = norm_mastery[ex_norm][1]
        else:
            m_val = 0.0
        m_clamped = min(1.0, max(0.0, float(m_val)))

        item_record = {
            "example": ex_name,
            "hot": hot_val,
            "in_demand": in_demand_val,
            "u": u,
            "mastery": m_clamped,
        }

        if comm not in groups:
            groups[comm] = []
        groups[comm].append(item_record)

    unmatched_keys = [
        orig_key
        for norm_k, (orig_key, _) in norm_mastery.items()
        if norm_k not in matched_norm_keys
    ]

    results: list[dict[str, Any]] = []

    for comm, items in groups.items():
        raw_sum_u = sum(it["u"] for it in items)
        if raw_sum_u <= 0.0:
            continue
        group_mastery = sum(it["u"] * it["mastery"] for it in items) / raw_sum_u
        W = min(3.0, raw_sum_u)
        gap = W * (1.0 - group_mastery)

        # Top 5 highest-u examples (hot first, then highest u, then name asc)
        sorted_items = sorted(
            items,
            key=lambda x: (-x["hot"], -x["u"], x["example"]),
        )
        top_examples = [it["example"] for it in sorted_items[:5]]

        results.append(
            {
                "commodity_title": comm,
                "group_weight": float(W),
                "raw_sum_u": float(raw_sum_u),
                "mastery": float(group_mastery),
                "gap": float(gap),
                "top_examples": top_examples,
            }
        )

    # Sort desc by gap, tie-break by commodity_title asc
    results.sort(key=lambda x: (-x["gap"], x["commodity_title"]))
    return results, unmatched_keys


def readiness(
    reqs: list[dict[str, Any]],
    techs: list[dict[str, Any]],
    prof: dict[str, float],
    mastery: dict[str, float],
    cfg: ScoringConfig = ScoringConfig(),
) -> dict[str, float]:
    """Compute readiness across all domains and overall weighted readiness."""
    skill_r = domain_readiness(reqs, prof, "skill", cfg)
    know_r = domain_readiness(reqs, prof, "knowledge", cfg)
    ab_r = domain_readiness(reqs, prof, "ability", cfg)
    tech_r = tech_readiness(techs, mastery)

    overall = (
        cfg.lambda_skill * skill_r
        + cfg.lambda_knowledge * know_r
        + cfg.lambda_ability * ab_r
        + cfg.lambda_tech * tech_r
    )

    return {
        "skill": skill_r,
        "knowledge": know_r,
        "ability": ab_r,
        "technology": tech_r,
        "overall": min(1.0, max(0.0, float(overall))),
    }


def gaps(
    reqs: list[dict[str, Any]],
    prof: dict[str, float],
    eps: float = 1e-3,
    cfg: ScoringConfig = ScoringConfig(),
) -> list[dict[str, Any]]:
    """Compute requirement gaps for trainable domains only (excludes 'ability').

    g = w*(1-c), sorted desc by gap, then element_name asc.
    """
    gap_items: list[dict[str, Any]] = []

    for r in reqs:
        if r["domain"] == "ability":
            continue

        w = weight(r["imp"], r["reliable"], cfg)
        p = prof.get(r["element_name"], 0.0)
        c = coverage(p, r["lvl"])
        g = w * (1.0 - c)

        if g > eps:
            gap_items.append(
                {
                    "domain": r["domain"],
                    "element_id": r.get("element_id", ""),
                    "element_name": r["element_name"],
                    "imp": float(r["imp"]),
                    "lvl": float(r["lvl"]),
                    "reliable": bool(r.get("reliable", True)),
                    "gap": float(g),
                }
            )

    # Sort descending by gap, tie-break by element_name ascending
    gap_items.sort(key=lambda x: (-x["gap"], x["element_name"]))
    return gap_items


def estimate_proficiency(
    self_report: dict[str, float],
    evidence: dict[str, float],
    prior_cap: dict[str, float],
    elements: list[str],
    beta: float = 0.65,
) -> dict[str, float]:
    """Estimate proficiency p in [0, 7] for each element.

    Defaults: self 0, cap 0.5.
    With evidence: p = 7*(beta*evidence + (1-beta)*min(self/5, cap)).
    Without evidence: p = 7*min(self/5, cap).
    Results are clamped to [0, 7].
    """
    estimated: dict[str, float] = {}

    for elem in elements:
        self_val = float(self_report.get(elem, 0.0))
        cap_val = float(prior_cap.get(elem, 0.5))
        prior_term = min(self_val / 5.0, cap_val)

        if elem in evidence and evidence[elem] is not None:
            ev_val = float(evidence[elem])
            p = 7.0 * (beta * ev_val + (1.0 - beta) * prior_term)
        else:
            p = 7.0 * prior_term

        estimated[elem] = min(7.0, max(0.0, float(p)))

    return estimated


def _spearman_rank_corr(ranks1: list[float], ranks2: list[float]) -> float:
    """Compute Spearman rank correlation between two equal-length rank vectors."""
    n = len(ranks1)
    if n < 2:
        return float("nan")
    d_sq = sum((r1 - r2) ** 2 for r1, r2 in zip(ranks1, ranks2, strict=False))
    return 1.0 - (6.0 * d_sq) / (n * (n * n - 1.0))


def _rank_vector(seq: list[float]) -> list[int]:
    """Assign integer ranks from 1 to len(seq)."""
    sorted_idx = sorted(range(len(seq)), key=lambda i: seq[i])
    ranks = [0] * len(seq)
    for rank, idx in enumerate(sorted_idx, 1):
        ranks[idx] = rank
    return ranks


def sensitivity(
    reqs_by_soc: dict[str, list[dict[str, Any]]],
    alphas: dict[str, float],
    prof: dict[str, float],
    delta: float = 0.10,
    cfg: ScoringConfig = ScoringConfig(),
) -> dict[str, Any]:
    """Analyze stability of the top-10 gap list against perturbations in editorial alphas.

    Perturbs each alpha by +/-delta (clipped at 0, renormalized to sum to 1).
    Computes Jaccard overlap and Spearman rank correlation on intersection against baseline.
    Returns per-perturbation results and the minimum Jaccard and Spearman (ignoring nan).
    """
    socs = list(alphas.keys())
    base_alpha_list = [alphas[s] for s in socs]
    req_sets = [reqs_by_soc[s] for s in socs]

    base_blended = blend(req_sets, base_alpha_list)
    base_gaps = gaps(base_blended, prof, cfg=cfg)[:10]
    base_top10_names = [g["element_name"] for g in base_gaps]
    base_rank_map = {name: idx + 1 for idx, name in enumerate(base_top10_names)}

    perturbations: list[dict[str, Any]] = []
    jaccards: list[float] = []
    spearmans: list[float] = []

    for soc in socs:
        for direction in (+1, -1):
            pert_label = f"{soc} {'+' if direction > 0 else '-'}{delta:.2f}"
            new_alphas = dict(alphas)
            new_alphas[soc] = max(0.0, new_alphas[soc] + direction * delta)

            total_a = sum(new_alphas.values())
            if total_a <= 0.0:
                norm_alphas = {s: 1.0 / len(socs) for s in socs}
            else:
                norm_alphas = {s: v / total_a for s, v in new_alphas.items()}

            pert_alpha_list = [norm_alphas[s] for s in socs]
            pert_blended = blend(req_sets, pert_alpha_list)
            pert_gaps = gaps(pert_blended, prof, cfg=cfg)[:10]
            pert_top10_names = [g["element_name"] for g in pert_gaps]
            pert_rank_map = {name: idx + 1 for idx, name in enumerate(pert_top10_names)}

            # Jaccard overlap
            set_base = set(base_top10_names)
            set_pert = set(pert_top10_names)
            intersection = set_base & set_pert
            union = set_base | set_pert
            jaccard = len(intersection) / len(union) if union else 1.0
            jaccards.append(jaccard)

            # Spearman rank correlation on intersection
            common_items = sorted(list(intersection))
            if len(common_items) >= 2:
                r_base = [float(base_rank_map[it]) for it in common_items]
                r_pert = [float(pert_rank_map[it]) for it in common_items]
                r_base_order = [float(x) for x in _rank_vector(r_base)]
                r_pert_order = [float(x) for x in _rank_vector(r_pert)]
                spearman = _spearman_rank_corr(r_base_order, r_pert_order)
                spearmans.append(spearman)
            else:
                spearman = float("nan")

            perturbations.append(
                {
                    "perturbation": pert_label,
                    "jaccard": jaccard,
                    "spearman": spearman,
                    "shared_count": len(common_items),
                }
            )

    valid_spearmans = [s for s in spearmans if not math.isnan(s)]

    return {
        "perturbations": perturbations,
        "min_jaccard": min(jaccards) if jaccards else 1.0,
        "min_spearman": min(valid_spearmans) if valid_spearmans else float("nan"),
    }
