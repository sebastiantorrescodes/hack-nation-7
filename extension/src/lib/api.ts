export const API_BASE = import.meta.env.VITE_API_BASE ?? "http://localhost:8000";

export async function api<T>(path: string, init?: { method?: string; body?: unknown }): Promise<T> {
  const res = await fetch(API_BASE + path, {
    method: init?.method ?? (init?.body !== undefined ? "POST" : "GET"),
    headers: init?.body !== undefined ? { "Content-Type": "application/json" } : undefined,
    body: init?.body !== undefined ? JSON.stringify(init.body) : undefined,
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(typeof err.detail === "string" ? err.detail : JSON.stringify(err.detail));
  }
  return res.json();
}

export type Condition = { field: string; op: string; values: string[] };
export type Skill = {
  id: string;
  title: string;
  trigger: Condition[];
  action: { kind: string; detail: string };
  expert_explanation: string;
  guardrail: { description: string; must: Condition[] };
  evidence: { t: number; quote: string }[];
  status: SkillStatus;
  expert_name: string;
};
export type SkillStatus = "draft" | "approved" | "rejected";
export type WorkMap = {
  id: string;
  session_id: string;
  workflow_id: string;
  expert_name: string;
  summary: string;
  recorded_at: number;
  skills: Skill[];
};
export type Workflow = {
  id: string;
  name: string;
  app: string;
  approved_skills: number;
  draft_skills: number;
  sessions: number;
  mastered_skills: number | null;
};
export type ScreenEvent = {
  t: number;
  kind: string;
  description: string;
  is_decision_point: boolean;
  ask_why: string | null;
};
export type CaptureSession = { id: string; expert_name: string; started_at: number; workmap_id: string | null };

export const fmtCond = (c: Condition) => `${c.field} ${c.op.replace(/_/g, " ")} ${c.values.join(", ")}`.trim();
