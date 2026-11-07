"use client";

import { useState } from "react";
import ReactMarkdown from "react-markdown";
import type { AssistantMessage as Msg, Roadmap } from "@/lib/types";
import { roadmapKey, roleName } from "@/lib/roadmap";
import { Icon } from "./Icons";
import { Mark } from "./Logo";
import { toast } from "./Toast";

type Props = {
  m: Msg;
  activeRoadmap?: Roadmap;
  onOpenRoadmap: (r: Roadmap) => void;
  onRetry?: () => void;
  isLast?: boolean;
};

function Thinking({ m }: { m: Msg }) {
  const writing = m.parts.length > 0;
  const live = m.status === "streaming" && !writing;
  const [open, setOpen] = useState<boolean | null>(null);
  const expanded = open ?? live;
  if (!m.steps.length && !live) return null;
  const last = m.steps[m.steps.length - 1];
  const label = live
    ? `${last ? last.label : "Thinking"}…`
    : m.thoughtMs != null
      ? `Thought for ${Math.max(1, Math.round(m.thoughtMs / 1000))}s`
      : "Thought process";
  return (
    <div>
      <button className={`think${live ? " live" : ""}`} aria-expanded={expanded} onClick={() => setOpen(!expanded)}>
        <Icon name="chevRight" className="i chev" />
        <span className="lbl">{label}</span>
      </button>
      {expanded && m.steps.length > 0 && (
        <div className="tsteps">
          {m.steps.map((s, i) => (
            <div key={i}>
              <Icon name="check" />
              <div>
                {s.label}
                {s.detail && <small>{s.detail}</small>}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export function AssistantMessage({ m, activeRoadmap, onOpenRoadmap, onRetry, isLast }: Props) {
  const streaming = m.status === "streaming";
  const text = m.parts.map((p) => (p.type === "text" ? p.text : "")).join("");
  const lastIdx = m.parts.length - 1;

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text);
      toast("Copied");
    } catch {
      toast("Couldn't copy. Select the text instead.");
    }
  };

  return (
    <div className="am">
      <Thinking m={m} />
      {m.parts.map((p, i) =>
        p.type === "text" ? (
          <div key={i} className={streaming && i === lastIdx ? "md streaming" : "md"}>
            <ReactMarkdown>{p.text}</ReactMarkdown>
            {streaming && i === lastIdx && <span className="typing" aria-hidden="true" />}
          </div>
        ) : (
          (() => {
            // chats saved before roadmaps were kept per message fall back to the latest one
            const roadmap = p.roadmap ?? activeRoadmap;
            if (!roadmap) return null;
            const current = !activeRoadmap || roadmapKey(roadmap) === roadmapKey(activeRoadmap);
            return (
              <button key={i} className="acard" onClick={() => onOpenRoadmap(roadmap)}>
                <span className="aic">
                  <Icon name="map" style={{ width: 22, height: 22 }} />
                </span>
                <span>
                  <b>{roleName(roadmap.target_role)} roadmap</b>
                  <small>
                    Roadmap · {roadmap.phases.length} phases ·{" "}
                    {roadmap.nodes.filter((n) => n.kind === "project").length} projects
                  </small>
                </span>
                <span className="open">{current ? "Open" : "Earlier version"}</span>
              </button>
            );
          })()
        ),
      )}
      {m.status === "done" && m.degraded && (
        <div className="busy">
          The AI service is busy right now, so this was a basic reply. Try again in a minute for a smarter answer.
        </div>
      )}
      {m.status === "error" && (
        <div className="err">
          <span>{m.error}</span>
          {onRetry && (
            <button className="btn" onClick={onRetry} style={{ padding: "5px 12px", fontSize: 13.5 }}>
              <Icon name="redo" style={{ width: 15, height: 15 }} />
              Try again
            </button>
          )}
        </div>
      )}
      {m.status === "done" && text && (
        <div className="acts">
          <button aria-label="Copy" onClick={copy}>
            <Icon name="copy" />
          </button>
          <button aria-label="Good response" onClick={() => toast("Thanks for the feedback")}>
            <Icon name="thumb" />
          </button>
          {onRetry && (
            <button aria-label="Retry" onClick={onRetry}>
              <Icon name="redo" />
            </button>
          )}
        </div>
      )}
      {(streaming || isLast) && <Mark size={26} className={`mark amark${streaming ? " spin" : ""}`} />}
    </div>
  );
}
