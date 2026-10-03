import { useEffect, useRef, useState } from "react";
import { api, type WorkMap } from "../lib/api";
import { snapshotPage } from "../lib/page";

type Claim = Record<string, unknown> & { id: string; label?: string };
type DecisionPoint = { skill_id: string; title: string; trigger: string[] };
type Grade = {
  correct: boolean;
  feedback: string;
  expert_action: { kind: string; detail: string };
  expert_explanation: string;
  expert_name: string;
};
type Violation = { skill_id: string; title: string; guardrail: string; failed: string[]; expert_explanation: string; expert_name: string };
type CheckResult = { ok: boolean; violations: Violation[] };
type Report = { headline: string; skills: { skill_id: string; title: string; status: string; note: string }[]; practice_next: string[] };

const SHOWN_FIELDS = ["payer", "patient_age", "place_of_service", "visit_type", "cpt_codes", "icd10_codes", "modifiers", "same_day_procedure", "prior_auth_on_file", "documentation_complete", "status", "physician_query"];
const show = (v: unknown) => (Array.isArray(v) ? v.join(", ") || "—" : v === null || v === undefined || v === "" ? "—" : String(v));

function violationMessage(v: Violation[]) {
  return v.map((x) => `${x.guardrail}. ${x.expert_name}: “${x.expert_explanation}”`).join("\n\n");
}

export default function Tutor({ active }: { active: boolean }) {
  const [learner, setLearner] = useState("");
  const [maps, setMaps] = useState<WorkMap[]>([]);
  const [workmapId, setWorkmapId] = useState("demo");
  const [practiceClaims, setPracticeClaims] = useState<Claim[]>([]);
  const [source, setSource] = useState<string>("live"); // "live" or a practice claim id

  const [sessionId, setSessionId] = useState<string | null>(null);
  const [claim, setClaim] = useState<Claim | null>(null);
  const [edited, setEdited] = useState<Claim | null>(null);
  const [points, setPoints] = useState<DecisionPoint[]>([]);
  const [predictions, setPredictions] = useState<Record<string, string>>({});
  const [grades, setGrades] = useState<Record<string, Grade>>({});
  const [check, setCheck] = useState<CheckResult | null>(null);
  const [report, setReport] = useState<Report | null>(null);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");

  const sessionRef = useRef<string | null>(null);
  sessionRef.current = sessionId;
  const isLive = claim?.id === "live";

  useEffect(() => {
    if (!active) return;
    api<WorkMap[]>("/api/workmaps").then(setMaps).catch((e) => setError(String(e)));
    api<Claim[]>("/api/tutor/claims").then(setPracticeClaims).catch(() => {});
  }, [active]);

  // The content script in OpenEMR asks us before letting a Save click through.
  useEffect(() => {
    const onMsg = (msg: { type?: string }, _sender: chrome.runtime.MessageSender, sendResponse: (r: unknown) => void) => {
      if (msg.type !== "save-attempt") return;
      const id = sessionRef.current;
      if (!id) {
        sendResponse({ ok: true });
        return;
      }
      (async () => {
        try {
          const page = await snapshotPage();
          const res = await api<CheckResult>(`/api/tutor/sessions/${id}/check`, { body: { page } });
          setCheck(res);
          sendResponse({ ok: res.ok, message: violationMessage(res.violations) });
        } catch (e) {
          setError(`Save check failed, letting the save through: ${String(e)}`);
          sendResponse({ ok: true });
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
    setBusy(source === "live" ? "Reading the claim from OpenEMR…" : "Starting…");
    try {
      const body =
        source === "live"
          ? { learner_name: learner || "Learner", workmap_id: workmapId, page: await snapshotPage() }
          : { learner_name: learner || "Learner", workmap_id: workmapId, claim_id: source };
      const res = await api<{ session: { id: string }; claim: Claim; decision_points: DecisionPoint[] }>("/api/tutor/sessions", { body });
      setSessionId(res.session.id);
      setClaim(res.claim);
      setEdited(structuredClone(res.claim));
      setPoints(res.decision_points);
      await chrome.storage.local.set({ guardSave: source === "live" });
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy("");
    }
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
    setBusy("Checking the claim…");
    try {
      setCheck(await api<CheckResult>(`/api/tutor/sessions/${sessionId}/check`, { body: { claim: edited } }));
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
      setReport(await api<Report>(`/api/tutor/sessions/${sessionId}/report`));
      await chrome.storage.local.set({ guardSave: false });
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy("");
    }
  }

  function setField(field: string, value: string, list = false) {
    setEdited((prev) => (prev ? { ...prev, [field]: list ? value.split(",").map((s) => s.trim()).filter(Boolean) : value } : prev));
  }

  return (
    <section>
      {!sessionId && (
        <>
          <p className="muted">Work a claim you haven't seen. Predict each decision, then save. The tutor checks the claim before it saves.</p>
          <input placeholder="Your name" value={learner} onChange={(e) => setLearner(e.target.value)} />
          <label>
            Work Map
            <select value={workmapId} onChange={(e) => setWorkmapId(e.target.value)}>
              {maps.map((m) => (
                <option key={m.id} value={m.id}>
                  {m.expert_name} · {m.skills.length} skills
                </option>
              ))}
            </select>
          </label>
          <label>
            Claim
            <select value={source} onChange={(e) => setSource(e.target.value)}>
              <option value="live">The claim open in OpenEMR</option>
              {practiceClaims.map((c) => (
                <option key={c.id} value={c.id}>
                  Practice: {c.label}
                </option>
              ))}
            </select>
          </label>
          <button className="primary" onClick={start} disabled={!!busy}>
            Start
          </button>
        </>
      )}

      {busy && <p className="muted">{busy}</p>}
      {error && <p className="error">{error}</p>}

      {claim && !report && (
        <>
          <article className="card">
            <h3>{claim.label ?? claim.id}</h3>
            <dl className="grid">
              {SHOWN_FIELDS.map((f) => (
                <div key={f}>
                  <dt>{f.replace(/_/g, " ")}</dt>
                  <dd>{show(claim[f])}</dd>
                </div>
              ))}
            </dl>
          </article>

          <h4>Decision points ({points.length})</h4>
          {points.length === 0 && <p className="muted">No skills from this Work Map apply to this claim.</p>}
          {points.map((p, i) => {
            const g = grades[p.skill_id];
            return (
              <article key={p.skill_id} className="card">
                <p>
                  <b>Decision {i + 1}.</b> Something on this claim needs a judgment call. What would you do, and why?
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
              <h4>Fix the claim</h4>
              <label>
                CPT codes
                <input value={show(edited.cpt_codes).replace("—", "")} onChange={(e) => setField("cpt_codes", e.target.value, true)} />
              </label>
              <label>
                Modifiers
                <input value={show(edited.modifiers).replace("—", "")} onChange={(e) => setField("modifiers", e.target.value, true)} />
              </label>
              <label>
                Status
                <select value={String(edited.status ?? "draft")} onChange={(e) => setField("status", e.target.value)}>
                  {["draft", "ready", "held", "submitted"].map((s) => (
                    <option key={s}>{s}</option>
                  ))}
                </select>
              </label>
              <label>
                Physician query
                <input value={String(edited.physician_query ?? "")} onChange={(e) => setField("physician_query", e.target.value)} />
              </label>
              <button className="primary" onClick={savePractice} disabled={!!busy}>
                Save claim
              </button>
            </article>
          )}
          {isLive && <p className="muted">Make your changes in OpenEMR and press Save there. The tutor checks the claim before it goes through.</p>}

          {check && (
            <div className={check.ok ? "feedback ok" : "feedback bad"}>
              {check.ok ? (
                <b>Claim passes every guardrail. Saved.</b>
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
              setClaim(null);
              setReport(null);
            }}
          >
            New claim
          </button>
        </article>
      )}
    </section>
  );
}
