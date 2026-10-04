export const API_BASE = import.meta.env.VITE_API_BASE ?? "http://localhost:8000";

export async function api<T>(path: string, init?: { method?: string; body?: unknown }): Promise<T> {
  const {accessToken} = await chrome.storage.session.get("accessToken");
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), path.endsWith("/workmap") ? 200000 : 60000);
  try {
    const res = await fetch(API_BASE + path, {
      method: init?.method ?? (init?.body !== undefined ? "POST" : "GET"),
      headers: { ...(init?.body !== undefined ? { "Content-Type": "application/json" } : {}), ...(accessToken ? {Authorization: `Bearer ${accessToken}`} : {}) },
      body: init?.body !== undefined ? JSON.stringify(init.body) : undefined,
      signal: controller.signal,
    });
    if (!res.ok) {
      if (res.status === 401) window.dispatchEvent(new Event("apprentice-sign-in"));
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      throw new Error(typeof err.detail === "string" ? err.detail : JSON.stringify(err.detail));
    }
    return await res.json();
  } catch (error) {
    if (controller.signal.aborted) throw new Error("Request timed out. Pending capture writes are retained; retry when connected.");
    throw error;
  } finally { clearTimeout(timeout); }
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
  version: number;
  supersedes: string | null;
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
  revision: number;
  teach_back_confirmed: boolean;
};
export type FieldType = "string" | "number" | "boolean" | "list";
/** One field of the records a workflow works on. Skill triggers and guardrails are conditions over these. */
export type RecordField = { name: string; type: FieldType; description: string };
export type WorkRecord = Record<string, unknown>;
export type PracticeCase = { id: string; workflow_id: string; label: string; data: WorkRecord };
export type Workflow = {
  id: string;
  name: string;
  app: string;
  description: string;
  fields: RecordField[];
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
  question_id?: string | null;
};
export type CaptureSession = { id: string; expert_name: string; started_at: number; workmap_id: string | null; workflow_id: string; phase: "capture" | "debrief" | "finished"; workmap_revision: number; events?: ScreenEvent[]; transcript?: {t: number; role: "expert" | "agent"; text: string; segment_id?: number; client_id?: string}[] };

export const showValue = (v: unknown) =>
  Array.isArray(v) ? v.join(", ") || "—" : v === null || v === undefined || v === "" ? "—" : String(v);

export const fmtCond =(c: Condition) => `${c.field} ${c.op.replace(/_/g, " ")} ${c.values.join(", ")}`.trim();

export async function apiAudio(path: string): Promise<Blob> {
  const {accessToken} = await chrome.storage.session.get("accessToken");
  const response = await fetch(API_BASE + path, {headers: accessToken ? {Authorization: `Bearer ${accessToken}`} : {}});
  if (!response.ok) {
    if (response.status === 401) window.dispatchEvent(new Event("apprentice-sign-in"));
    const error = await response.json().catch(() => ({detail: response.statusText}));
    throw new Error(error.detail || "Voice preview failed.");
  }
  return response.blob();
}
