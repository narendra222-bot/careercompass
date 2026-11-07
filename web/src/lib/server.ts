// Calls to the CareerCompass API for sign-in and saved chats.
import { API_URL } from "./api";
import type { Conversation, User } from "./types";

export class SessionExpired extends Error {}

async function call<T>(path: string, init: RequestInit = {}, token?: string): Promise<T> {
  const headers: Record<string, string> = { "content-type": "application/json" };
  if (token) headers.Authorization = `Bearer ${token}`;
  let res: Response;
  try {
    res = await fetch(`${API_URL}${path}`, { ...init, headers: { ...headers, ...(init.headers ?? {}) } });
  } catch {
    throw new Error("Can't reach the CareerCompass server. Check that the backend is running.");
  }
  if (res.status === 401 && token) throw new SessionExpired("Your session has ended. Please sign in again.");
  if (!res.ok) {
    let detail = `Request failed (${res.status})`;
    try {
      const body = await res.json();
      if (typeof body.detail === "string") detail = body.detail;
    } catch {}
    throw new Error(detail);
  }
  return (await res.json()) as T;
}

type Session = { token: string; user: Omit<User, "token"> };
const withToken = (s: Session): User => ({ ...s.user, token: s.token });

export const auth = {
  config: () => call<{ google_client_id: string | null; email_login: boolean }>("/api/auth/config"),
  email: async (name: string, email: string) =>
    withToken(await call<Session>("/api/auth/email", { method: "POST", body: JSON.stringify({ name, email }) })),
  google: async (credential: string) =>
    withToken(await call<Session>("/api/auth/google", { method: "POST", body: JSON.stringify({ credential }) })),
};

export const chats = {
  list: (token: string) => call<Conversation[]>("/api/conversations", {}, token),
  save: (token: string, c: Conversation) =>
    call<{ status: string }>(
      `/api/conversations/${encodeURIComponent(c.id)}`,
      { method: "PUT", body: JSON.stringify(c) },
      token,
    ),
  remove: async (token: string, id: string) => {
    try {
      await call(`/api/conversations/${encodeURIComponent(id)}`, { method: "DELETE" }, token);
    } catch (e) {
      if (e instanceof SessionExpired) throw e;
      /* already gone on the server: nothing to do */
    }
  },
};
