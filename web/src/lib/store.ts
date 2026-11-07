// Browser persistence for the signed-in user and their conversations.
// Everything lives in localStorage for now; a server-side store can replace this later.
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

export const loadConversations = () => read<Conversation[]>(CONVS_KEY, []);
export const saveConversations = (cs: Conversation[]) => write(CONVS_KEY, cs);

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
