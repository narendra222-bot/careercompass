"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { useBrowserValue } from "@/lib/hooks";
import { getUser, setDraft } from "@/lib/store";
import { Icon } from "./Icons";
import { Logo } from "./Logo";
import { ThemeToggle } from "./ThemeToggle";

export function LandingNav() {
  const [scrolled, setScrolled] = useState(false);
  const signedIn = useBrowserValue(() => !!getUser(), false);
  useEffect(() => {
    const on = () => setScrolled(window.scrollY > 8);
    window.addEventListener("scroll", on, { passive: true });
    return () => window.removeEventListener("scroll", on);
  }, []);
  return (
    <header className={`lnav${scrolled ? " scrolled" : ""}`}>
      <div className="wrapx">
        <Logo size={30} />
        <nav>
          <a href="#how">How it works</a>
          <a href="#features">Features</a>
          <a href="#samples">Sample roadmaps</a>
          <a href="#colleges">For colleges</a>
        </nav>
        <div className="r">
          <ThemeToggle />
          {signedIn ? (
            <Link className="btn dark" href="/chat">
              Open CareerCompass
            </Link>
          ) : (
            <>
              <Link className="btn ghost" href="/login">
                Log in
              </Link>
              <Link className="btn dark" href="/login">
                Try CareerCompass
              </Link>
            </>
          )}
        </div>
      </div>
    </header>
  );
}

/** Hero message box: keeps what was typed and carries it into the first chat. */
export function HeroForm() {
  const router = useRouter();
  const [text, setText] = useState("");
  const go = () => {
    const t = text.trim();
    if (t) setDraft(t.slice(0, 2000));
    router.push(getUser() ? "/chat" : "/login");
  };
  return (
    <form
      className="hbox composer"
      onSubmit={(e) => {
        e.preventDefault();
        go();
      }}
    >
      <textarea
        id="hero-text"
        rows={2}
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            go();
          }
        }}
        aria-label="Describe yourself"
        placeholder="I'm in 3rd year CSE, I know Linux and Git, and I want to become a DevOps engineer…"
      />
      <div className="crow">
        <span style={{ fontSize: 13, color: "var(--faint)" }}>Free for students</span>
        <span style={{ marginLeft: "auto" }} />
        <button className="send" aria-label="Start">
          <Icon name="up" />
        </button>
      </div>
    </form>
  );
}
