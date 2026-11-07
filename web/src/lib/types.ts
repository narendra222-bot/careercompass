// Shapes shared by the chat UI. Roadmap mirrors careercompass/schemas.py (schema 1.0),
// typed only as far as the UI reads it.

export type Readiness = { core: { overall: number } };

export type RoadmapNode = {
  id: string;
  label: string;
  kind: "course" | "project" | "known" | "goal";
  phase: number | null;
  start_week: number | null;
  end_week: number | null;
  hours: number;
  resources: { title: string; url: string }[];
  deliverables: string[];
  skills_gained: string[];
};

export type RoadmapPhase = {
  phase: number;
  title: string;
  start_week: number;
  end_week: number;
  hours: number;
  node_ids: string[];
  readiness_after: Readiness;
};

export type Roadmap = {
  student: { branch: string; year: number; hours_per_week: number; target_role: string };
  target_role: string;
  soc_alignment: { soc: string; title: string; alpha: number }[];
  baseline_readiness: Readiness;
  final_readiness: Readiness;
  target_readiness: number;
  phases: RoadmapPhase[];
  nodes: RoadmapNode[];
  top_tech_gaps: { tech: string; gap: number }[];
  skipped_known: string[];
  dropped_for_time: string[];
  feasibility: {
    feasible: boolean;
    total_hours: number;
    total_weeks: number;
    hours_per_week: number;
    max_weeks: number | null;
  };
  explanation: string | null;
  provenance?: { data?: string; generator?: "deterministic-planner" | "llm-onet-planner" };
};

export type Step = { label: string; detail: string };

export type Part = { type: "text"; text: string } | { type: "roadmap"; roadmap: Roadmap };

export type Message =
  | { id: string; role: "user"; content: string }
  | {
      id: string;
      role: "assistant";
      parts: Part[];
      steps: Step[];
      thoughtMs?: number;
      status: "streaming" | "done" | "error";
      error?: string;
      outcome?: "ok" | "needs_input" | "chat" | "error";
      options?: string[];
      degraded?: boolean; // the AI service was busy, so a basic reply was used
    };

export type AssistantMessage = Extract<Message, { role: "assistant" }>;

export type Conversation = {
  id: string;
  title: string;
  createdAt: number;
  updatedAt: number;
  messages: Message[];
  pending: string[]; // fields the agent asked for in its last reply
  lastPlan?: Record<string, unknown> | null; // profile the current roadmap was built from
  roadmap?: Roadmap;
  done: string[]; // roadmap node ids the student has ticked
};

export type User = {
  id?: string;
  name: string;
  email: string;
  picture?: string | null;
  provider?: "google" | "email";
  token?: string; // session token from the API; absent for users from before sign-in existed
};

export type DoneEvent = {
  status: "ok" | "needs_input" | "chat" | "error";
  missing: string[];
  options: string[];
  title: string;
  profile: Record<string, unknown> | null;
  degraded?: boolean;
};
