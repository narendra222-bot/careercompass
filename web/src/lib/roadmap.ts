// Derived numbers for the roadmap panel.
import type { Roadmap, RoadmapNode, RoadmapPhase } from "./types";

/** Identity of a roadmap version that survives a save/load round trip. */
export const roadmapKey = (r: Roadmap) =>
  [
    r.target_role,
    r.feasibility.total_weeks,
    r.feasibility.total_hours,
    r.student.hours_per_week,
    r.nodes.map((n) => n.id).join(","),
  ].join("|");

export const pct = (x: number) => `${Math.round(x * 100)}%`;

export function phaseNodes(r: Roadmap, p: RoadmapPhase): RoadmapNode[] {
  const byId = new Map(r.nodes.map((n) => [n.id, n]));
  return p.node_ids
    .map((id) => byId.get(id))
    .filter((n): n is RoadmapNode => !!n)
    .sort((a, b) => (a.start_week ?? 0) - (b.start_week ?? 0));
}

/** Readiness points each item earns: its phase's gain, shared out by hours. */
export function contributions(r: Roadmap): Record<string, number> {
  const out: Record<string, number> = {};
  let prev = r.baseline_readiness.core.overall;
  for (const p of r.phases) {
    const gain = p.readiness_after.core.overall - prev;
    prev = p.readiness_after.core.overall;
    for (const n of phaseNodes(r, p)) out[n.id] = p.hours ? (gain * n.hours) / p.hours : 0;
  }
  return out;
}

export function readiness(r: Roadmap, done: string[]): number {
  const c = contributions(r);
  return r.baseline_readiness.core.overall + done.reduce((s, id) => s + (c[id] ?? 0), 0);
}

export const planItems = (r: Roadmap) => r.nodes.filter((n) => n.kind === "course" || n.kind === "project");

export const ROLE_NAMES: Record<string, string> = {
  "devops engineer": "DevOps Engineer",
  "data analyst": "Data Analyst",
  "machine learning engineer": "Machine Learning Engineer",
};
const SMALL = new Set(["ai", "ml", "ui", "ux", "qa", "it", "seo", "hr", "sre", "iot", "ar", "vr", "bi"]);
export const roleName = (r: string) =>
  ROLE_NAMES[r.toLowerCase()] ??
  (r.split(/\s+/).some((w) => /[A-Z]/.test(w.slice(1)))
    ? r
    : r
        .toLowerCase()
        .split(/\s+/)
        .map((w) => (SMALL.has(w) ? w.toUpperCase() : w.charAt(0).toUpperCase() + w.slice(1)))
        .join(" "));

export const weeksLabel = (a: number | null, b: number | null) =>
  a == null ? "" : a === b ? `wk ${a}` : `wk ${a}–${b}`;
