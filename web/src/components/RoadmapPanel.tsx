"use client";

import { useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import type { Roadmap } from "@/lib/types";
import { contributions, pct, phaseNodes, planItems, readiness, roleName, weeksLabel } from "@/lib/roadmap";
import { Icon } from "./Icons";
import { toast } from "./Toast";

type Tab = "plan" | "time" | "why";

type Props = {
  roadmap: Roadmap;
  done: string[];
  onToggle: (id: string) => void;
  onClose: () => void;
};

export function RoadmapPanel({ roadmap: r, done, onToggle, onClose }: Props) {
  const [tab, setTab] = useState<Tab>("plan");
  const body = useRef<HTMLDivElement>(null);
  const contrib = contributions(r);
  const ready = readiness(r, done);
  const f = r.feasibility;
  const items = planItems(r);
  const known = r.nodes.filter((n) => n.kind === "known");
  const title = `${roleName(r.target_role)} roadmap`;
  const generated = r.provenance?.generator === "llm-onet-planner";

  const toggle = (id: string) => {
    const was = done.includes(id);
    onToggle(id);
    if (!was) toast(`+${((contrib[id] ?? 0) * 100).toFixed(1)} pts job readiness`);
  };

  const copyJson = async () => {
    try {
      await navigator.clipboard.writeText(JSON.stringify(r, null, 2));
      toast("Roadmap JSON copied");
    } catch {
      toast("Couldn't copy the roadmap");
    }
  };

  return (
    <div className="pin">
      <div className="phead">
        <h3>{title}</h3>
        <div className="r">
          <button className="ibtn" aria-label="Copy roadmap as JSON" onClick={copyJson}>
            <Icon name="copy" />
          </button>
          <button className="ibtn" aria-label="Download PDF" onClick={() => toast("PDF export is coming soon")}>
            <Icon name="download" />
          </button>
          <button className="ibtn" aria-label="Close roadmap" onClick={onClose}>
            <Icon name="close" />
          </button>
        </div>
      </div>
      <div className="tabs" role="tablist">
        {(
          [
            ["plan", "Roadmap"],
            ["time", "Timeline"],
            ["why", "Why this plan"],
          ] as const
        ).map(([k, l]) => (
          <button key={k} className="tab" role="tab" aria-selected={tab === k} onClick={() => setTab(k)}>
            {l}
          </button>
        ))}
      </div>

      <div className="pbody" ref={body}>
        {tab !== "why" && (
          <>
            <div className="stats">
              <div className="st">
                <small>Duration</small>
                <b>{f.total_weeks} wks</b>
              </div>
              <div className="st">
                <small>Total</small>
                <b>{Math.round(f.total_hours)} h</b>
              </div>
              <div className="st">
                <small>Per week</small>
                <b>{f.hours_per_week} h</b>
              </div>
              <div className="st">
                <small>Fits your time</small>
                <b className={f.feasible ? "ok" : ""}>{f.feasible ? "Yes" : "Tight"}</b>
              </div>
            </div>
            <div className="rd">
              <div className="top">
                <b>Job readiness {pct(ready)}</b>
                <span>target {pct(r.target_readiness)}</span>
              </div>
              <div className="rbar">
                <i style={{ width: `${Math.min(ready, 1) * 100}%`, background: "var(--accent)" }} />
                <span className="t" style={{ left: `${r.target_readiness * 100}%` }} />
              </div>
              <div className="top">
                <span>Started at {pct(r.baseline_readiness.core.overall)}</span>
                <span>
                  {done.length} of {items.length} done
                </span>
              </div>
            </div>
          </>
        )}

        {tab === "plan" && (
          <>
            <div className="src">
              <Icon name={generated ? "spark" : "check"} />
              <span>
                {generated ? "Drafted from O*NET job data for " : "Hand-checked course catalog · O*NET: "}
                <b>{r.soc_alignment.map((s) => s.title).join(", ")}</b>
                {generated ? " · every link opened and checked" : ""}
              </span>
            </div>
            {known.length > 0 && (
              <div className="skip">
                Skipped, you know these:
                {known.map((k) => (
                  <span key={k.id}>{k.label}</span>
                ))}
              </div>
            )}
            {r.phases.map((p) => (
              <div className="phase" key={p.phase} style={{ ["--c" as string]: "var(--accent)" }}>
                <span className="pnum">{p.phase}</span>
                <div>
                  <h4>{p.title.replace(/^Project:\s*/, "")}</h4>
                  <div className="meta">
                    Weeks {p.start_week === p.end_week ? p.start_week : `${p.start_week}–${p.end_week}`} ·{" "}
                    {Math.round(p.hours)} hours
                  </div>
                  {phaseNodes(r, p).map((n) => {
                    const isDone = done.includes(n.id);
                    return (
                      <button
                        key={n.id}
                        className={`item${isDone ? " done" : ""}`}
                        aria-pressed={isDone}
                        onClick={() => toggle(n.id)}
                      >
                        <span className="box">
                          <Icon name="check" />
                        </span>
                        <span>
                          <span className="lbl">{n.label.replace(/^Project:\s*/, "")}</span>
                          <span className={`kind ${n.kind === "project" ? "p" : "c"}`}>
                            {n.kind === "project" ? "Project" : "Course"}
                          </span>
                        </span>
                        <span className="h">
                          {Math.round(n.hours)}h · wk {n.start_week}
                        </span>
                        {n.kind === "project" && n.deliverables.length > 0 && (
                          <span className="del">{n.deliverables.slice(0, 2).join("; ")}</span>
                        )}
                        {n.kind === "course" && !n.resources[0] && (
                          <span className="del">
                            Find a course:{" "}
                            <a
                              href={`https://www.youtube.com/results?search_query=${encodeURIComponent(n.label + " full course")}`}
                              target="_blank"
                              rel="noreferrer"
                              onClick={(e) => e.stopPropagation()}
                            >
                              YouTube
                            </a>{" "}
                            ·{" "}
                            <a
                              href={`https://www.google.com/search?q=${encodeURIComponent(n.label + " free course")}`}
                              target="_blank"
                              rel="noreferrer"
                              onClick={(e) => e.stopPropagation()}
                            >
                              Google
                            </a>
                          </span>
                        )}
                        {n.kind === "course" && n.resources[0] && (
                          <span className="del">
                            Start with{" "}
                            <a
                              href={n.resources[0].url}
                              target="_blank"
                              rel="noreferrer"
                              onClick={(e) => e.stopPropagation()}
                            >
                              {n.resources[0].title}
                            </a>
                          </span>
                        )}
                      </button>
                    );
                  })}
                </div>
              </div>
            ))}
          </>
        )}

        {tab === "time" && <Timeline r={r} />}

        {tab === "why" && (
          <div className="why">
            <ReactMarkdown>{r.explanation ?? ""}</ReactMarkdown>
            <h5>O*NET occupations used</h5>
            <div className="soc">
              {r.soc_alignment.map((s) => (
                <div key={s.soc}>
                  <div>
                    {s.title}
                    <small>{s.soc}</small>
                    <span className="b">
                      <i style={{ width: `${s.alpha * 100}%` }} />
                    </span>
                  </div>
                  <b>{pct(s.alpha)}</b>
                </div>
              ))}
            </div>
            {r.top_tech_gaps.length > 0 && (
              <>
                <h5>Biggest tool gaps at the start</h5>
                <p>
                  {r.top_tech_gaps
                    .slice(0, 6)
                    .map((g) => g.tech)
                    .join(", ")}
                </p>
              </>
            )}
            <h5>Source</h5>
            <p style={{ color: "var(--muted)", fontSize: 14 }}>
              {r.provenance?.data ?? "O*NET 31.0 Database"}. Course list curated by the CareerCompass team.
            </p>
          </div>
        )}
      </div>
    </div>
  );
}

function Timeline({ r }: { r: Roadmap }) {
  const W = Math.max(r.feasibility.total_weeks, ...r.nodes.map((n) => n.end_week ?? 0), 1);
  const ticks = [1, ...[5, 10, 15, 20, 25, 30, 35, 40, 45, 50].filter((w) => w < W), W];
  return (
    <div className="gwrap">
      <div className="gantt">
        <div className="grow axis">
          <div style={{ paddingLeft: 14 }}>Item</div>
          <div className="ticks">
            {ticks.map((w) => (
              <span key={w} style={{ left: `${((w - 0.5) / W) * 100}%` }}>
                wk {w}
              </span>
            ))}
          </div>
        </div>
        {r.phases.map((p) => (
          <div key={p.phase}>
            <div className="grow">
              <div className="gph" style={{ ["--c" as string]: "var(--accent)" }}>
                <i />
                Phase {p.phase} · {p.title.replace(/^Project:\s*/, "")}
              </div>
            </div>
            {phaseNodes(r, p).map((n) => (
              <div className="grow" key={n.id}>
                <div className="gname" title={n.label}>
                  {n.label.replace(/^Project:\s*/, "")}
                </div>
                <div className="lane" style={{ backgroundSize: `calc(100% / ${W}) 100%` }}>
                  <span
                    className="gbar"
                    title={`${n.label} · ${weeksLabel(n.start_week, n.end_week)} · ${n.hours} h`}
                    style={{
                      ["--c" as string]: "var(--accent)",
                      left: `${(((n.start_week ?? 1) - 1) / W) * 100}%`,
                      width: `${(((n.end_week ?? 1) - (n.start_week ?? 1) + 1) / W) * 100}%`,
                      opacity: n.kind === "project" ? 1 : 0.55,
                    }}
                  />
                </div>
              </div>
            ))}
          </div>
        ))}
      </div>
    </div>
  );
}
