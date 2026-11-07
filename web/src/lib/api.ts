// Client for the FastAPI backend. /api/chat answers with Server-Sent Events.
import type { DoneEvent, Roadmap, Step } from "./types";

export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export type ChatPayload = {
  messages: { role: "user" | "assistant"; content: string }[];
  pending: string[];
  last_plan?: Record<string, unknown> | null;
  roadmap?: Roadmap | null;
};

export type ChatHandlers = {
  onStep: (s: Step) => void;
  onText: (delta: string) => void;
  onRoadmap: (r: Roadmap) => void;
  onDone: (d: DoneEvent) => void;
  onError: (message: string) => void;
};

export async function streamChat(payload: ChatPayload, h: ChatHandlers, signal?: AbortSignal): Promise<void> {
  let res: Response;
  try {
    res = await fetch(`${API_URL}/api/chat`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(payload),
      signal,
    });
  } catch (e) {
    if ((e as Error).name === "AbortError") return;
    h.onError("Can't reach the CareerCompass server. Check that the backend is running.");
    return;
  }
  if (!res.ok || !res.body) {
    h.onError(
      res.status === 422
        ? "That message couldn't be sent. Keep each message under 2,000 characters."
        : `The server answered with an error (${res.status}). Try again in a moment.`,
    );
    return;
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  let finished = false;
  try {
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      let cut: number;
      while ((cut = buf.indexOf("\n\n")) >= 0) {
        const block = buf.slice(0, cut);
        buf = buf.slice(cut + 2);
        let event = "message";
        let data = "";
        for (const line of block.split("\n")) {
          if (line.startsWith("event: ")) event = line.slice(7);
          else if (line.startsWith("data: ")) data += line.slice(6);
        }
        if (!data) continue;
        const d = JSON.parse(data);
        if (event === "step") h.onStep(d);
        else if (event === "text") h.onText(d.delta);
        else if (event === "roadmap") h.onRoadmap(d.roadmap);
        else if (event === "done") {
          finished = true;
          h.onDone(d);
        } else if (event === "error") {
          finished = true;
          h.onError(d.message);
        }
      }
    }
  } catch (e) {
    if ((e as Error).name === "AbortError") return;
    h.onError("The connection dropped while I was answering. Try again.");
    return;
  }
  if (!finished) h.onError("The answer was cut off. Try again.");
}
