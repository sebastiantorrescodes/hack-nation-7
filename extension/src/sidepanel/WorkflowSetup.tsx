import { useCallback, useEffect, useState } from "react";
import { api, type FieldType, type PracticeCase, type RecordField, type WorkRecord, type Workflow } from "../lib/api";
import { snapshotPage } from "../lib/page";
import { RecordForm, RecordView } from "./RecordForm";

const TYPES: FieldType[] = ["string", "number", "boolean", "list"];

/** The workflow's record fields: proposed by Claude from the first recording, correctable by the expert. */
export function FieldsEditor({ workflow, onSaved }: { workflow: Workflow; onSaved: () => void }) {
  const [draft, setDraft] = useState<RecordField[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const update = (i: number, change: Partial<RecordField>) =>
    setDraft((d) => d && d.map((f, j) => (j === i ? { ...f, ...change } : f)));

  async function save() {
    if (!draft) return;
    setBusy(true);
    setError("");
    try {
      await api(`/api/workflows/${workflow.id}`, { method: "PATCH", body: { fields: draft } });
      setDraft(null);
      onSaved();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <article className="card">
      <div className="card-head">
        <h4>Record fields ({workflow.fields.length})</h4>
        {!draft && <button onClick={() => setDraft(structuredClone(workflow.fields))}>Edit</button>}
      </div>
      <p className="muted">
        What the records in this workflow look like. Skills are written as conditions on these fields. They're proposed
        automatically when you build a Work Map; you can correct them here.
      </p>
      {error && <p className="error">{error}</p>}

      {!draft && workflow.fields.length > 0 && (
        <dl>
          {workflow.fields.map((f) => (
            <div key={f.name} className="contents">
              <dt>
                {f.name} <span className="pill">{f.type}</span>
              </dt>
              <dd>{f.description}</dd>
            </div>
          ))}
        </dl>
      )}

      {draft && (
        <>
          {draft.map((f, i) => (
            <div key={i} className="field-row">
              <input placeholder="snake_case_name" value={f.name} onChange={(e) => update(i, { name: e.target.value })} />
              <select value={f.type} onChange={(e) => update(i, { type: e.target.value as FieldType })}>
                {TYPES.map((t) => (
                  <option key={t}>{t}</option>
                ))}
              </select>
              <button onClick={() => setDraft(draft.filter((_, j) => j !== i))} title="Remove field">
                ✕
              </button>
              <input
                className="wide"
                placeholder="What it means"
                value={f.description}
                onChange={(e) => update(i, { description: e.target.value })}
              />
            </div>
          ))}
          <div className="row">
            <button onClick={() => setDraft([...draft, { name: "", type: "string", description: "" }])}>+ Field</button>
            <span className="spacer" />
            <button onClick={() => setDraft(null)} disabled={busy}>
              Cancel
            </button>
            <button className="primary" onClick={save} disabled={busy}>
              Save fields
            </button>
          </div>
        </>
      )}
    </article>
  );
}

/** Practice cases: records as they arrive, before anyone works them, for trainees to practice on. */
export function CasesEditor({ workflow }: { workflow: Workflow }) {
  const [cases, setCases] = useState<PracticeCase[]>([]);
  const [label, setLabel] = useState("");
  const [manual, setManual] = useState<WorkRecord | null>(null);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");

  const load = useCallback(() => {
    api<PracticeCase[]>(`/api/workflows/${workflow.id}/cases`)
      .then(setCases)
      .catch((e) => setError(String(e)));
  }, [workflow.id]);
  useEffect(load, [load]);

  async function add(body: () => Promise<{ data: WorkRecord } | { page: string }>, message: string) {
    setBusy(message);
    setError("");
    try {
      await api(`/api/workflows/${workflow.id}/cases`, { body: { label, ...(await body()) } });
      setLabel("");
      setManual(null);
      load();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy("");
    }
  }

  async function remove(id: string) {
    try {
      await api(`/api/cases/${id}`, { method: "DELETE" });
      load();
    } catch (e) {
      setError(String(e));
    }
  }

  const noFields = workflow.fields.length === 0;

  return (
    <article className="card">
      <h4>Practice cases ({cases.length})</h4>
      <p className="muted">Records as they arrive, before anyone works them. Trainees practice on these.</p>
      {error && <p className="error">{error}</p>}

      {cases.map((c) => (
        <details key={c.id} className="case">
          <summary>{c.label}</summary>
          <RecordView fields={workflow.fields} record={c.data} />
          <div className="row">
            <button onClick={() => remove(c.id)}>Delete</button>
          </div>
        </details>
      ))}

      {noFields ? (
        <p className="muted">Add record fields (or build a Work Map) before adding practice cases.</p>
      ) : (
        <>
          <input placeholder="Label, e.g. 'Hotel stay, missing receipt'" value={label} onChange={(e) => setLabel(e.target.value)} />
          {manual ? (
            <>
              <RecordForm fields={workflow.fields} record={manual} onChange={setManual} />
              <div className="row">
                <span className="spacer" />
                <button onClick={() => setManual(null)}>Cancel</button>
                <button className="primary" onClick={() => add(async () => ({ data: manual }), "Saving…")} disabled={!!busy || !label.trim()}>
                  Save case
                </button>
              </div>
            </>
          ) : (
            <div className="row">
              <button
                className="primary"
                onClick={() => add(async () => ({ page: await snapshotPage() }), "Reading the record from the current tab…")}
                disabled={!!busy || !label.trim()}
                title="Open the record, untouched, in the active tab"
              >
                Add from current tab
              </button>
              <button onClick={() => setManual({})} disabled={!!busy}>
                Enter by hand
              </button>
            </div>
          )}
          {busy && <p className="muted">{busy}</p>}
        </>
      )}
    </article>
  );
}
