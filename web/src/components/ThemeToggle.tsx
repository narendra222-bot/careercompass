"use client";

import { useEffect, useState } from "react";
import { Icon } from "./Icons";

const KEY = "cc.theme";

function systemDark() {
  return window.matchMedia("(prefers-color-scheme: dark)").matches;
}

/** Light/dark switch. With no saved choice the page follows the system setting. */
export function ThemeToggle() {
  const [dark, setDark] = useState<boolean | null>(null);

  useEffect(() => {
    const read = () => {
      const t = document.documentElement.dataset.theme;
      setDark(t ? t === "dark" : systemDark());
    };
    read();
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    mq.addEventListener("change", read);
    return () => mq.removeEventListener("change", read);
  }, []);

  const flip = () => {
    const next = dark ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    try {
      localStorage.setItem(KEY, next);
    } catch {}
    setDark(!dark);
  };

  return (
    <button className="ibtn" onClick={flip} aria-label={dark ? "Switch to light mode" : "Switch to dark mode"}>
      {dark === null ? null : <Icon name={dark ? "sun" : "moon"} />}
    </button>
  );
}

/** Inline script for <head>: applies a saved theme before first paint. */
export const themeScript = `try{var t=localStorage.getItem("${KEY}");if(t)document.documentElement.dataset.theme=t}catch(e){}`;
