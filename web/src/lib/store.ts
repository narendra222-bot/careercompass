// Browser persistence: the signed-in user (with their session token) and a local copy of
// their conversations. The server is the source of truth; this copy makes the app open
// instantly and keeps chats if the server can't be reached.
import type { Conversation, User } from "./types";

const USER_KEY = "cc.user";
const CONVS_KEY = "cc.conversations";
const DRAFT_KEY = "cc.draft";

function read<T>(key: string, fallback: T): T {
  try {
    const raw = localStorage.getItem(key);
    return raw ? (JSON.parse(raw) as T) : fallback;
  } catch {
    return fallback;
  }
}

function write(key: string, value: unknown): void {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch {
    /* storage full or blocked: the app keeps working in memory */
  }
}

export const getUser = () => read<User | null>(USER_KEY, null);
export const setUser = (u: User) => write(USER_KEY, u);
export const clearUser = () => {
  try {
    localStorage.removeItem(USER_KEY);
  } catch {}
};

const convKey = (u: User) => `${CONVS_KEY}.${u.id ?? u.email}`;
export const loadConversations = (u: User) => read<Conversation[]>(convKey(u), []);
export const saveConversations = (u: User, cs: Conversation[]) => write(convKey(u), cs);

/** Chats saved before accounts existed (one shared list); handed over once, then removed. */
export const takeLegacyConversations = (): Conversation[] => {
  const cs = read<Conversation[]>(CONVS_KEY, []);
  try {
    localStorage.removeItem(CONVS_KEY);
  } catch {}
  return cs;
};

// Text typed on the landing page before signing in, sent as the first message.
export const setDraft = (t: string) => {
  try {
    sessionStorage.setItem(DRAFT_KEY, t);
  } catch {}
};
export const takeDraft = (): string | null => {
  try {
    const t = sessionStorage.getItem(DRAFT_KEY);
    sessionStorage.removeItem(DRAFT_KEY);
    return t;
  } catch {
    return null;
  }
};

export const newId = () =>
  typeof crypto !== "undefined" && "randomUUID" in crypto
    ? crypto.randomUUID()
    : Math.random().toString(36).slice(2) + Date.now().toString(36);
