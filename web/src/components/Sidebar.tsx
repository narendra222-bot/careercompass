"use client";

import { useEffect, useRef, useState } from "react";
import type { Conversation, User } from "@/lib/types";
import { Icon } from "./Icons";
import { Logo } from "./Logo";
import { toast } from "./Toast";

type Props = {
  user: User;
  conversations: Conversation[];
  activeId: string | null;
  onNew: () => void;
  onOpen: (id: string) => void;
  onDelete: (id: string) => void;
  onToggleCollapse: () => void;
  onLogout: () => void;
};

export function Sidebar({ user, conversations, activeId, onNew, onOpen, onDelete, onToggleCollapse, onLogout }: Props) {
  const [menu, setMenu] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);
  const sorted = [...conversations].sort((a, b) => b.updatedAt - a.updatedAt);

  useEffect(() => {
    if (!menu) return;
    const close = (e: MouseEvent) => {
      if (!menuRef.current?.contains(e.target as Node)) setMenu(false);
    };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, [menu]);

  return (
    <aside className="sbar">
      <div className="stop">
        <Logo size={24} fontSize={17} />
        <button className="ibtn" onClick={onToggleCollapse} aria-label="Toggle sidebar">
          <Icon name="sidebar" />
        </button>
      </div>
      <div className="snav">
        <button className="si new" onClick={onNew}>
          <span className="plus">
            <Icon name="plus" />
          </span>
          <span className="t">New chat</span>
        </button>
        <button className="si" onClick={() => toast("A full chats page is coming soon")}>
          <Icon name="chat" />
          <span className="t">Chats</span>
        </button>
        <button className="si" onClick={() => toast("A roadmaps page is coming soon")}>
          <Icon name="map" />
          <span className="t">Roadmaps</span>
        </button>
        <button className="si" onClick={() => toast("Saved items are coming soon")}>
          <Icon name="bookmark" />
          <span className="t">Saved</span>
        </button>
      </div>
      <div className="rec">
        <h6>Recents</h6>
        {sorted.length === 0 && <div className="noconv">Your chats will show up here.</div>}
        {sorted.map((c) => (
          <div key={c.id} className="ri-row" aria-current={c.id === activeId}>
            <button className="ri" aria-current={c.id === activeId} onClick={() => onOpen(c.id)} title={c.title}>
              {c.title}
            </button>
            <button className="del" aria-label={`Delete ${c.title}`} onClick={() => onDelete(c.id)}>
              <Icon name="trash" />
            </button>
          </div>
        ))}
      </div>
      <div className="user" ref={menuRef}>
        {menu && (
          <div className="umenu">
            <div className="who">{user.email}</div>
            <button
              onClick={() => {
                setMenu(false);
                onLogout();
              }}
            >
              <Icon name="logout" />
              Log out
            </button>
          </div>
        )}
        <button onClick={() => setMenu(!menu)} aria-expanded={menu}>
          {user.picture ? (
            // eslint-disable-next-line @next/next/no-img-element
            <img className="av" src={user.picture} alt="" referrerPolicy="no-referrer" />
          ) : (
            <span className="av">{user.name.trim().charAt(0).toUpperCase() || "?"}</span>
          )}
          <span className="uname">
            {user.name}
            <small>Free plan</small>
          </span>
        </button>
      </div>
    </aside>
  );
}
