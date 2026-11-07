"use client";

import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { streamChat, type ChatPayload } from "@/lib/api";
import { useHydrated } from "@/lib/hooks";
import { roadmapKey } from "@/lib/roadmap";
import { clearUser, getUser, loadConversations, newId, saveConversations, takeDraft } from "@/lib/store";
import type { AssistantMessage as AMsg, Conversation, Message, Roadmap, User } from "@/lib/types";
import { AssistantMessage } from "./AssistantMessage";
import { Composer } from "./Composer";
import { Icon, type IconName } from "./Icons";
import { Mark } from "./Logo";
import { RoadmapPanel } from "./RoadmapPanel";
import { Sidebar } from "./Sidebar";
import { ThemeToggle } from "./ThemeToggle";
import { toast } from "./Toast";

const STARTERS: { icon: IconName; label: string; text: string }[] = [
  {
    icon: "map",
    label: "Plan my DevOps career",
    text: "I'm in 3rd year CSE. I know Linux, Git and a bit of Python. I want to become a DevOps engineer.",
  },
  {
    icon: "chart",
    label: "B.Com to data analyst",
    text: "I'm in B.Com final year and good with Excel. I want to become a data analyst. I can study 8 hours a week.",
  },
  {
    icon: "spark",
    label: "Become an ML engineer",
    text: "I'm a 4th year CSE student, strong in Python and I know some SQL. I want to be a machine learning engineer.",
  },
  {
    icon: "clock",
    label: "Job-ready in 6 months",
    text: "2nd year ECE, 12 hours a week, I want to be job-ready as a DevOps engineer in 6 months.",
  },
];

/** Next steps that make sense for this roadmap. */
function followUps(r: Roadmap | undefined): string[] {
  if (!r) return [];
  const f = r.feasibility;
  const out = [f.total_weeks > 26 ? "I want to finish in 6 months" : "Can I finish in 3 months?"];
  out.push(
    f.hours_per_week < 15
      ? `I can study ${Math.round(f.hours_per_week + 5)} hours a week`
      : `I can only do ${Math.max(5, Math.round(f.hours_per_week - 5))} hours a week`,
  );
  out.push("Explain my first phase");
  return out;
}

function greeting(name: string) {
  const h = new Date().getHours();
  const first = name.trim().split(/\s+/)[0] || "there";
  return `${h < 12 ? "Good morning" : h < 17 ? "Good afternoon" : "Good evening"}, ${first}`;
}

const assistantText = (m: AMsg) =>
  m.parts
    .map((p) => (p.type === "text" ? p.text : ""))
    .join("")
    .trim();

/** History the backend sees: user turns plus finished assistant replies. */
function toPayload(messages: Message[]): ChatPayload["messages"] {
  const out: ChatPayload["messages"] = [];
  for (const m of messages) {
    if (m.role === "user") out.push({ role: "user", content: m.content.slice(0, 2000) });
    else if (m.status === "done" && assistantText(m))
      out.push({ role: "assistant", content: assistantText(m).slice(0, 2000) });
  }
  return out.slice(-40);
}

/** Chat needs the browser (saved user and chats), so it renders after hydration. */
export function ChatApp() {
  const hydrated = useHydrated();
  if (!hydrated) return <div className="loading">Loading…</div>;
  return <Chat />;
}

function Chat() {
  const router = useRouter();
  const [user] = useState<User | null>(getUser);
  const ready = !!user;
  const [convs, setConvs] = useState<Conversation[]>(loadConversations);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [panel, setPanel] = useState(false);
  const [collapsed, setCollapsed] = useState(false);
  const [mobOpen, setMobOpen] = useState(false);
  const convsRef = useRef<Conversation[]>([]);
  const scroller = useRef<HTMLDivElement>(null);
  const stick = useRef(true);
  const abort = useRef<AbortController | null>(null);
  const draftSent = useRef(false);

  useLayoutEffect(() => {
    convsRef.current = convs;
  }, [convs]);
  const active = convs.find((c) => c.id === activeId) ?? null;

  // ---------- load ----------
  useEffect(() => {
    if (!user) {
      router.replace("/login");
      return;
    }
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = "";
      abort.current?.abort();
    };
  }, [router, user]);

  useEffect(() => {
    if (ready) saveConversations(convs);
  }, [convs, ready]);

  // ---------- updates ----------
  const updateConv = useCallback((cid: string, fn: (c: Conversation) => Conversation) => {
    setConvs((cs) => cs.map((c) => (c.id === cid ? { ...fn(c), updatedAt: Date.now() } : c)));
  }, []);

  const updateMsg = useCallback(
    (cid: string, mid: string, fn: (m: AMsg) => AMsg) =>
      updateConv(cid, (c) => ({
        ...c,
        messages: c.messages.map((m) => (m.id === mid && m.role === "assistant" ? fn(m) : m)),
      })),
    [updateConv],
  );

  /** Stream one assistant reply for the conversation's current messages. */
  const run = useCallback(
    async (cid: string, history: Message[], ctx: Pick<Conversation, "pending" | "lastPlan" | "roadmap">) => {
      const mid = newId();
      const t0 = Date.now();
      let wrote = false;
      const placeholder: AMsg = { id: mid, role: "assistant", parts: [], steps: [], status: "streaming" };
      updateConv(cid, (c) => ({ ...c, messages: [...history, placeholder] }));
      setBusy(true);
      stick.current = true;
      const ctl = new AbortController();
      abort.current = ctl;

      await streamChat(
        {
          messages: toPayload(history),
          pending: ctx.pending,
          last_plan: ctx.lastPlan ?? null,
          roadmap: ctx.roadmap ?? null,
        },
        {
          onStep: (s) => updateMsg(cid, mid, (m) => ({ ...m, steps: [...m.steps, s] })),
          onText: (delta) => {
            const first = !wrote;
            wrote = true;
            updateMsg(cid, mid, (m) => {
              const parts = [...m.parts];
              const last = parts[parts.length - 1];
              if (last && last.type === "text") parts[parts.length - 1] = { type: "text", text: last.text + delta };
              else parts.push({ type: "text", text: delta.replace(/^\s+/, "") });
              return { ...m, parts, thoughtMs: first ? Date.now() - t0 : m.thoughtMs };
            });
          },
          onRoadmap: (r) => {
            updateConv(cid, (c) => {
              const ids = new Set(r.nodes.map((n) => n.id));
              return {
                ...c,
                roadmap: r,
                done: c.done.filter((id) => ids.has(id)),
                messages: c.messages.map((m) =>
                  m.id === mid && m.role === "assistant"
                    ? { ...m, parts: [...m.parts, { type: "roadmap", roadmap: r }] }
                    : m,
                ),
              };
            });
            setPanel(true);
          },
          onDone: (d) => {
            updateConv(cid, (c) => ({
              ...c,
              pending: d.status === "needs_input" ? d.missing : [],
              lastPlan: d.status === "ok" && d.profile ? d.profile : c.lastPlan,
              title: c.title === "New chat" && d.title !== "New roadmap" ? d.title : c.title,
              messages: c.messages.map((m) =>
                m.id === mid && m.role === "assistant"
                  ? {
                      ...m,
                      status: "done",
                      outcome: d.status,
                      options: d.options ?? [],
                      degraded: !!d.degraded,
                      thoughtMs: m.thoughtMs ?? Date.now() - t0,
                    }
                  : m,
              ),
            }));
          },
          onError: (message) =>
            updateMsg(cid, mid, (m) => ({
              ...m,
              status: "error",
              error: message,
              thoughtMs: m.thoughtMs ?? Date.now() - t0,
            })),
        },
        ctl.signal,
      );
      if (abort.current === ctl) abort.current = null;
      setBusy(false);
    },
    [updateConv, updateMsg],
  );

  const send = useCallback(
    (text: string) => {
      if (busy) return;
      let cid = activeId;
      let conv = convsRef.current.find((c) => c.id === cid);
      if (!conv) {
        cid = newId();
        conv = {
          id: cid,
          title: "New chat",
          createdAt: Date.now(),
          updatedAt: Date.now(),
          messages: [],
          pending: [],
          done: [],
        };
        const fresh = conv;
        setConvs((cs) => [fresh, ...cs]);
        setActiveId(cid);
        setPanel(false);
      }
      const history: Message[] = [...conv.messages, { id: newId(), role: "user", content: text }];
      void run(cid!, history, conv);
    },
    [activeId, busy, run],
  );

  const retry = useCallback(() => {
    if (!active || busy) return;
    const msgs = [...active.messages];
    while (msgs.length && msgs[msgs.length - 1].role === "assistant") msgs.pop();
    if (!msgs.length) return;
    void run(active.id, msgs, active);
  }, [active, busy, run]);

  // first message typed on the landing page
  useEffect(() => {
    if (!ready || draftSent.current) return;
    draftSent.current = true;
    const d = takeDraft();
    if (d) send(d);
  }, [ready, send]);

  // ---------- scrolling ----------
  const onScroll = () => {
    const el = scroller.current;
    if (el) stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 120;
  };
  useLayoutEffect(() => {
    const el = scroller.current;
    if (el && stick.current) el.scrollTop = el.scrollHeight;
  }, [active?.messages]);

  // ---------- actions ----------
  const openConv = (id: string) => {
    setActiveId(id);
    setMobOpen(false);
    stick.current = true;
    setPanel(!!convsRef.current.find((c) => c.id === id)?.roadmap);
  };
  const newChat = () => {
    setActiveId(null);
    setPanel(false);
    setMobOpen(false);
  };
  const del = (id: string) => {
    if (busy && id === activeId) {
      toast("Wait for the reply to finish first");
      return;
    }
    setConvs((cs) => cs.filter((c) => c.id !== id));
    if (id === activeId) newChat();
    toast("Chat deleted");
  };
  const logout = () => {
    clearUser();
    router.replace("/");
  };
  const toggleDone = (id: string) => {
    if (!active) return;
    updateConv(active.id, (c) => ({
      ...c,
      done: c.done.includes(id) ? c.done.filter((x) => x !== id) : [...c.done, id],
    }));
  };

  if (!ready || !user) return <div className="loading">Loading…</div>;

  const msgs = active?.messages ?? [];
  const last = msgs[msgs.length - 1];
  const lastDone = last && last.role === "assistant" && last.status === "done" ? last : null;
  const quick = lastDone?.options?.length ? lastDone.options : undefined;

  const cls = ["app", collapsed && "collapsed", mobOpen && "mob-open", panel && active?.roadmap && "panel-open"]
    .filter(Boolean)
    .join(" ");

  return (
    <div className={cls}>
      <div className="scrim" onClick={() => setMobOpen(false)} />
      <Sidebar
        user={user}
        conversations={convs}
        activeId={activeId}
        onNew={newChat}
        onOpen={openConv}
        onDelete={del}
        onToggleCollapse={() => (window.innerWidth <= 900 ? setMobOpen(false) : setCollapsed(!collapsed))}
        onLogout={logout}
      />

      <main className="main">
        <div className="mtop">
          <button className="ibtn mobmenu" onClick={() => setMobOpen(true)} aria-label="Open menu">
            <Icon name="menu" />
          </button>
          {active && <div className="ctitle">{active.title}</div>}
          <div className="r">
            {active?.roadmap && !panel && (
              <button className="btn" style={{ padding: "6px 12px", fontSize: 13.5 }} onClick={() => setPanel(true)}>
                <Icon name="map" style={{ width: 16, height: 16 }} />
                Roadmap
              </button>
            )}
            <ThemeToggle />
          </div>
        </div>

        {!active ? (
          <div className="empty">
            <h1 className="greet">
              <Mark size={38} />
              <span>{greeting(user.name)}</span>
            </h1>
            <Composer
              id="first-message"
              onSend={send}
              placeholder="How can I help with your career today?"
              disabled={busy}
              autoFocus
            />
            <div className="chips">
              {STARTERS.map((s) => (
                <button key={s.label} className="chip" onClick={() => send(s.text)}>
                  <Icon name={s.icon} />
                  {s.label}
                </button>
              ))}
            </div>
          </div>
        ) : (
          <>
            <div className="scroll" ref={scroller} onScroll={onScroll}>
              <div className="thread">
                {msgs.map((m, i) =>
                  m.role === "user" ? (
                    <div key={m.id} className="um">
                      <span className="av">{user.name.trim().charAt(0).toUpperCase()}</span>
                      <div style={{ whiteSpace: "pre-wrap" }}>{m.content}</div>
                    </div>
                  ) : (
                    <div key={m.id}>
                      <AssistantMessage
                        m={m}
                        isLast={i === msgs.length - 1}
                        activeRoadmap={active.roadmap}
                        onOpenRoadmap={(r) => {
                          if (!active.roadmap || roadmapKey(r) !== roadmapKey(active.roadmap)) {
                            const ids = new Set(r.nodes.map((n) => n.id));
                            updateConv(active.id, (c) => ({
                              ...c,
                              roadmap: r,
                              done: c.done.filter((id) => ids.has(id)),
                            }));
                          }
                          setPanel(true);
                        }}
                        onRetry={i === msgs.length - 1 && !busy ? retry : undefined}
                      />
                      {i === msgs.length - 1 && quick && (
                        <div className="opts" style={{ marginTop: 10 }}>
                          {quick.map((q) => (
                            <button key={q} className="opt" onClick={() => send(q)}>
                              {q}
                            </button>
                          ))}
                        </div>
                      )}
                      {i === msgs.length - 1 && lastDone?.outcome === "ok" && (
                        <div className="follow" style={{ marginTop: 12 }}>
                          {followUps(active.roadmap).map((q) => (
                            <button key={q} onClick={() => send(q)}>
                              <Icon name="arrow" />
                              {q}
                            </button>
                          ))}
                        </div>
                      )}
                    </div>
                  ),
                )}
              </div>
            </div>
            <div className="dock">
              <Composer id="reply" onSend={send} placeholder="Reply to CareerCompass…" disabled={busy} compact />
              <div className="disc">CareerCompass can make mistakes. Check course details before you enrol.</div>
            </div>
          </>
        )}
      </main>

      <aside className="pane" aria-label="Roadmap">
        {active?.roadmap && (
          <RoadmapPanel
            roadmap={active.roadmap}
            done={active.done}
            onToggle={toggleDone}
            onClose={() => setPanel(false)}
          />
        )}
      </aside>
    </div>
  );
}
