"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { Icon } from "@/components/Icons";
import { Mark } from "@/components/Logo";
import { auth } from "@/lib/server";
import { getUser, setUser } from "@/lib/store";
import type { User } from "@/lib/types";

type GoogleId = {
  accounts: {
    id: {
      initialize: (o: { client_id: string; callback: (r: { credential: string }) => void }) => void;
      renderButton: (el: HTMLElement, o: Record<string, unknown>) => void;
    };
  };
};

const GSI_SRC = "https://accounts.google.com/gsi/client";

function loadGsi(): Promise<GoogleId> {
  const w = window as unknown as { google?: GoogleId };
  if (w.google?.accounts?.id) return Promise.resolve(w.google);
  return new Promise((resolve, reject) => {
    const s = document.createElement("script");
    s.src = GSI_SRC;
    s.async = true;
    s.onload = () => (w.google ? resolve(w.google) : reject(new Error("Google script loaded without API")));
    s.onerror = () => reject(new Error("Couldn't load Google sign-in"));
    document.head.appendChild(s);
  });
}

export default function LoginPage() {
  const router = useRouter();
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [config, setConfig] = useState<{ google_client_id: string | null; email_login: boolean } | null>(null);
  const [configError, setConfigError] = useState("");
  const googleBox = useRef<HTMLDivElement>(null);

  const done = (u: User) => {
    setUser(u);
    router.push("/chat");
  };

  useEffect(() => {
    if (getUser()?.token) {
      router.replace("/chat");
      return;
    }
    auth
      .config()
      .then(setConfig)
      .catch((e: Error) => setConfigError(e.message));
  }, [router]);

  useEffect(() => {
    const cid = config?.google_client_id;
    if (!cid || !googleBox.current) return;
    let alive = true;
    loadGsi()
      .then((g) => {
        if (!alive || !googleBox.current) return;
        g.accounts.id.initialize({
          client_id: cid,
          callback: async ({ credential }) => {
            setBusy(true);
            setError("");
            try {
              done(await auth.google(credential));
            } catch (e) {
              setError((e as Error).message);
              setBusy(false);
            }
          },
        });
        g.accounts.id.renderButton(googleBox.current, {
          theme: "outline",
          size: "large",
          shape: "rectangular",
          text: "continue_with",
          width: Math.min(400, googleBox.current.clientWidth || 400),
        });
      })
      .catch((e: Error) => alive && setError(e.message));
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [config]);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!name.trim()) return setError("Enter your name so CareerCompass knows what to call you.");
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email.trim()))
      return setError("Enter a valid email address, like ravi@college.edu.");
    setBusy(true);
    setError("");
    try {
      done(await auth.email(name.trim(), email.trim()));
    } catch (err) {
      setError((err as Error).message);
      setBusy(false);
    }
  };

  return (
    <div className="login" style={{ minHeight: "100vh" }}>
      <Link className="btn ghost back" href="/">
        <Icon name="back" />
        Back
      </Link>
      <div className="lcard">
        <Mark size={44} />
        <h1>Start your roadmap</h1>
        <p>Sign in to save your plans and track your progress on any device.</p>

        {configError && <div className="lerr">{configError}</div>}

        {config?.google_client_id ? (
          <div ref={googleBox} className="gbox" aria-label="Continue with Google" />
        ) : (
          config && (
            <button className="btn" type="button" style={{ width: "100%", padding: 12 }} disabled>
              <span style={{ fontWeight: 700 }}>G</span>
              Google sign-in isn&apos;t set up yet
            </button>
          )
        )}

        {config?.email_login && (
          <>
            <div className="or">or</div>
            <form onSubmit={submit} noValidate>
              <input
                id="name"
                placeholder="Your name"
                aria-label="Your name"
                value={name}
                onChange={(e) => setName(e.target.value)}
                autoComplete="name"
              />
              <input
                id="email"
                type="email"
                placeholder="Your college email"
                aria-label="Email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                autoComplete="email"
              />
              {error && <div className="lerr">{error}</div>}
              <button className="btn dark" style={{ width: "100%", padding: 12 }} disabled={busy}>
                {busy ? "Signing in…" : "Continue with email"}
              </button>
            </form>
          </>
        )}
        {!config?.email_login && error && <div className="lerr">{error}</div>}
        <small>Your chats are saved to your account. We never share your data with colleges without asking.</small>
      </div>
    </div>
  );
}
