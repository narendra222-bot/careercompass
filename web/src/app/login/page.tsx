"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { Icon } from "@/components/Icons";
import { Mark } from "@/components/Logo";
import { toast } from "@/components/Toast";
import { getUser, setUser } from "@/lib/store";

export default function LoginPage() {
  const router = useRouter();
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    if (getUser()) router.replace("/chat");
  }, [router]);

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!name.trim()) return setError("Enter your name so CareerCompass knows what to call you.");
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email.trim()))
      return setError("Enter a valid email address, like ravi@college.edu.");
    setUser({ name: name.trim(), email: email.trim() });
    router.push("/chat");
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
        <p>Log in to save your plans and track your progress.</p>
        <button
          className="btn"
          type="button"
          style={{ width: "100%", padding: 12 }}
          onClick={() => toast("Google sign-in is coming soon. Use your email for now.")}
        >
          <span style={{ fontWeight: 700 }}>G</span>
          Continue with Google
        </button>
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
          <button className="btn dark" style={{ width: "100%", padding: 12 }}>
            Continue with email
          </button>
        </form>
        <small>Your chats are saved in this browser. We never share your data with colleges without asking.</small>
      </div>
    </div>
  );
}
