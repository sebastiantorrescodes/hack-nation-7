import { useEffect, useRef, useState } from "react";
import { api, type PracticeCase, type WorkRecord, type Workflow, type RecordField } from "../lib/api";
import { getAppTab, snapshotPage } from "../lib/page";
import { RecordForm, RecordView } from "./RecordForm";

type ShownRecord = WorkRecord & { id: string; label?: string };
type DecisionPoint = { skill_id: string; title: string; trigger: string[] };
type Grade = {
  correct: boolean;
  feedback: string;
  expert_action: { kind: string; detail: string };
  expert_explanation: string;
  expert_name: string;
};
type Violation = { skill_id: string; title: string; guardrail: string; failed: string[]; expert_explanation: string; expert_name: string };
type CheckResult = { ok: boolean; violations: Violation[]; unknown_fields?: string[]; detail?: string };
type Report = { headline: string; skills: { skill_id: string; title: string; status: string; note: string }[]; practice_next: string[] };

function violationMessage(v: Violation[]) {
  return v.map((x) => `${x.guardrail}. ${x.expert_name}: “${x.expert_explanation}”`).join("\n\n");
}

type Props = {
  workflow: Workflow;
  learner: string;
  /** True while a record is being worked, so the parent can keep the learner from navigating away mid-record. */
  onSessionChange: (inSession: boolean) => void;
};

export default function Tutor({ workflow, learner, onSessionChange }: Props) {
  const [cases, setCases] = useState<PracticeCase[]>([]);
  const [source, setSource] = useState<string>("live"); // "live" or a practice case id

  const [sessionId, setSessionId] = useState<string | null>(null);
  const [record, setRecord] = useState<ShownRecord | null>(null);
  const [edited, setEdited] = useState<WorkRecord | null>(null);
  const [points, setPoints] = useState<DecisionPoint[]>([]);
  const [predictions, setPredictions] = useState<Record<string, string>>({});
  const [grades, setGrades] = useState<Record<string, Grade>>({});
  const [check, setCheck] = useState<CheckResult | null>(null);
  const [report, setReport] = useState<Report | null>(null);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [recoverable, setRecoverable] = useState<{tabId: number; sessionId: string} | null>(null);
  const [fields, setFields] = useState<RecordField[]>(workflow.fields);

  const sessionRef = useRef<string | null>(null);
  const boundTab = useRef<number | null>(null);
  sessionRef.current = report ? null : sessionId;
  const isLive = record?.id === "live";
  const appName = workflow.app || "the app";

  useEffect(() => {
    api<PracticeCase[]>(`/api/workflows/${workflow.id}/cases`)
      .then((c) => {
        setCases(c);
        if (c.length) setSource(c[0].id);
      })
      .catch((e) => setError(String(e)));
    chrome.storage.local.get("guardSave").then(v => {
      if (v.guardSave?.workflowId === workflow.id) setRecoverable(v.guardSave);
    });
  }, [workflow.id]);

  useEffect(() => onSessionChange(!!sessionId && !report), [sessionId, report, onSessionChange]);

  // The content script in the app asks us before letting a Save/Submit click through.
  useEffect(() => {
    const onMsg = (msg: { type?: string }, _sender: chrome.runtime.MessageSender, sendResponse: (r: unknown) => void) => {
      if (msg.type !== "save-attempt") return;
      const id = sessionRef.current;
      if (_sender.tab?.id !== boundTab.current) return;
      if (!id) { sendResponse({ok: false, message: "The tutor is unavailable. Reopen it before saving."}); return; }
      (async () => {
        try {
          const page = await snapshotPage(boundTab.current ?? undefined);
          const res = await api<CheckResult>(`/api/tutor/sessions/${id}/check`, { body: { page } });
          setCheck(res);
          if (res.detail) setError(res.detail);
          sendResponse({ ok: res.ok, message: res.detail || violationMessage(res.violations) });
        } catch (e) {
          setError(`Save check could not be completed: ${String(e)}`);
          sendResponse({ ok: false, message: "The tutor could not verify this save. Retry when it is connected." });
        }
      })();
      return true; // keep the channel open for the async response
    };
    chrome.runtime.onMessage.addListener(onMsg);
    return () => chrome.runtime.onMessage.removeListener(onMsg);
  }, []);

  async function start() {
    setError("");
    setGrades({});
    setPredictions({});
    setCheck(null);
    setReport(null);
    setBusy(source === "live" ? `Reading the record from ${appName}…` : "Starting…");
    try {
      const tab = source === "live" ? await getAppTab() : undefined;
      boundTab.current = tab?.id ?? null;
      const body =
        source === "live"
          ? { learner_name: learner, workflow_id: workflow.id, page: await snapshotPage(boundTab.current ?? undefined) }
          : { learner_name: learner, workflow_id: workflow.id, case_id: source };
      const res = await api<{ session: { id: string }; record: ShownRecord; decision_points: DecisionPoint[] }>("/api/tutor/sessions", { body });
      setSessionId(res.session.id);
      setRecord(res.record);
      setEdited(structuredClone(res.record));
      setPoints(res.decision_points);
      await chrome.storage.local.set({ guardSave: source === "live" ? {tabId: boundTab.current, sessionId: res.session.id, workflowId: workflow.id} : false });
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy("");
    }
  }

  async function recover() {
    if (!recoverable) return;
    setBusy("Recovering tutoring session…"); setError("");
    try {
      const res = await api<{record: ShownRecord; fields: RecordField[]; decision_points: DecisionPoint[]; grades: Record<string, Grade>}>(`/api/tutor/sessions/${recoverable.sessionId}`);
      boundTab.current = recoverable.tabId;
      setSessionId(recoverable.sessionId); setRecord(res.record); setEdited(structuredClone(res.record));
      setFields(res.fields); setPoints(res.decision_points); setGrades(res.grades); setRecoverable(null);
    } catch(e) {setError(String(e));}
    finally {setBusy("");}
  }
  async function exitRecoveredSession() {
    await chrome.storage.local.set({guardSave: false}); setRecoverable(null);
  }

  async function submitPrediction(skillId: string) {
    if (!sessionId) return;
    setBusy("Checking your answer…");
    try {
      const g = await api<Grade>(`/api/tutor/sessions/${sessionId}/predict`, { body: { skill_id: skillId, prediction: predictions[skillId] ?? "" } });
      setGrades((prev) => ({ ...prev, [skillId]: g }));
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy("");
    }
  }

  async function savePractice() {
    if (!sessionId || !edited) return;
    setBusy("Checking your changes…");
    try {
      const result = await api<CheckResult>(`/api/tutor/sessions/${sessionId}/check`, { body: { record: edited } });
      setCheck(result);
      setError(result.detail ?? "");
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy("");
    }
  }

  async function finish() {
    if (!sessionId) return;
    setBusy("Writing your mastery report…");
    try {
      const result = await api<Report>(`/api/tutor/sessions/${sessionId}/report`);
      setReport(result);
      sessionRef.current = null;
      boundTab.current = null;
      await chrome.storage.local.set({ guardSave: false });
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy("");
    }
  }

  return (
    <section>
      {recoverable && !sessionId && <div className="card"><p>A tutoring session is still holding saves on its original tab.</p><button onClick={recover} disabled={!!busy}>Resume tutoring</button><button onClick={exitRecoveredSession} disabled={!!busy}>End teach mode</button></div>}
      {!sessionId && !recoverable && (
        <>
          <p className="muted">Work a record you haven't seen. Predict each decision, then save. The tutor checks your work before it saves.</p>
          <label>
            Record
            <select value={source} onChange={(e) => setSource(e.target.value)}>
              {cases.map((c) => (
                <option key={c.id} value={c.id}>
                  Practice: {c.label}
                </option>
              ))}
              <option value="live">The record open in {appName}</option>
            </select>
          </label>
          <button className="primary" onClick={start} disabled={!!busy}>
            Start
          </button>
        </>
      )}

      {busy && <p className="muted">{busy}</p>}
      {error && <p className="error">{error}</p>}

      {record && !report && (
        <>
          <article className="card">
            <h3>{record.label ?? record.id}</h3>
            <RecordView fields={fields} record={record} />
          </article>

          <h4>Decision points ({points.length})</h4>
          {points.length === 0 && <p className="muted">No skills from this Work Map apply to this record.</p>}
          {points.map((p, i) => {
            const g = grades[p.skill_id];
            return (
              <article key={p.skill_id} className="card">
                <p>
                  <b>Decision {i + 1}.</b> Something on this record needs a judgment call. What would you do, and why?
                </p>
                <textarea
                  rows={3}
                  value={predictions[p.skill_id] ?? ""}
                  onChange={(e) => setPredictions((prev) => ({ ...prev, [p.skill_id]: e.target.value }))}
                  disabled={!!g}
                />
                {!g && (
                  <button onClick={() => submitPrediction(p.skill_id)} disabled={!!busy || !predictions[p.skill_id]}>
                    Check my answer
                  </button>
                )}
                {g && (
                  <div className={g.correct ? "feedback ok" : "feedback bad"}>
                    <b>{g.correct ? "Right call." : "Not quite."}</b> {g.feedback}
                    <p className="quote">
                      {g.expert_name}: “{g.expert_explanation}”
                    </p>
                    <p className="muted">Skill: {p.title}</p>
                  </div>
                )}
              </article>
            );
          })}

          {!isLive && edited && (
            <article className="card">
              <h4>Make your changes</h4>
              <RecordForm fields={fields} record={edited} onChange={setEdited} />
              <button className="primary" onClick={savePractice} disabled={!!busy}>
                Save
              </button>
            </article>
          )}
          {isLive && (
            <p className="muted">Make your changes in {appName} and press Save or Submit there. The tutor checks your work before it goes through.</p>
          )}

          {check && (
            <div className={check.ok ? "feedback ok" : "feedback bad"}>
              {check.ok ? (
                <b>Passes the verified guardrails. The website handles the save.</b>
              ) : (
                <>
                  <b>Save blocked.</b>
                  {check.violations.map((v) => (
                    <div key={v.skill_id} className="violation">
                      <p>{v.guardrail}</p>
                      <p className="muted">Failed: {v.failed.join("; ")}</p>
                      <p className="quote">
                        {v.expert_name}: “{v.expert_explanation}”
                      </p>
                    </div>
                  ))}
                </>
              )}
            </div>
          )}

          <button onClick={finish} disabled={!!busy}>
            Finish and see my report
          </button>
        </>
      )}

      {report && (
        <article className="card">
          <h3>Mastery report</h3>
          <p>{report.headline}</p>
          <ul className="report">
            {report.skills.map((s) => (
              <li key={s.skill_id}>
                <span className={`pill ${s.status}`}>{s.status.replace("_", " ")}</span> <b>{s.title}</b>
                <div className="muted">{s.note}</div>
              </li>
            ))}
          </ul>
          {report.practice_next.length > 0 && (
            <>
              <h4>Practice next</h4>
              <ul>
                {report.practice_next.map((p, i) => (
                  <li key={i}>{p}</li>
                ))}
              </ul>
            </>
          )}
          <button
            onClick={() => {
              setSessionId(null);
              setRecord(null);
              setReport(null);
            }}
          >
            Another record
          </button>
        </article>
      )}
    </section>
  );
}
