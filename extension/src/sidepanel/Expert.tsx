import { useCallback, useEffect, useState } from "react";
import { api, type Skill, type SkillStatus, type Workflow, type WorkMap } from "../lib/api";
import { useStored } from "../lib/useStored";
import Capture from "./Capture";
import SkillCard from "./SkillCard";
import { CasesEditor, FieldsEditor } from "./WorkflowSetup";

export default function Expert({ active }: { active: boolean }) {
  const [name, setName] = useStored("expertName");
  const [workflows, setWorkflows] = useState<Workflow[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [newName, setNewName] = useState("");
  const [newApp, setNewApp] = useState("");
  const [newDescription, setNewDescription] = useState("");
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState("");

  const load = useCallback(() => {
    api<Workflow[]>("/api/workflows")
      .then(setWorkflows)
      .catch((e) => setError(String(e)));
  }, []);
  useEffect(() => {
    if (active) load();
  }, [active, load]);

  async function create() {
    setCreating(true);
    setError("");
    try {
      const wf = await api<Workflow>("/api/workflows", { body: { name: newName, app: newApp, description: newDescription } });
      setWorkflows((prev) => [...prev, wf]);
      setSelectedId(wf.id);
      setNewName("");
      setNewApp("");
      setNewDescription("");
    } catch (e) {
      setError(String(e));
    } finally {
      setCreating(false);
    }
  }

  const selected = workflows.find((w) => w.id === selectedId);

  return (
    <section>
      <label>
        Your name
        <input placeholder="e.g. Maria Lopez" value={name} onChange={(e) => setName(e.target.value)} />
      </label>
      {error && <p className="error">{error}</p>}

      {selected ? (
        <WorkflowDetail workflow={selected} expertName={name.trim()} onBack={() => setSelectedId(null)} onChanged={load} />
      ) : (
        <>
          <h4>Workflows</h4>
          {workflows.length === 0 && <p className="muted">No workflows yet. Create one to start teaching.</p>}
          {workflows.map((w) => (
            <button key={w.id} className="card link" onClick={() => setSelectedId(w.id)}>
              <h3>{w.name}</h3>
              <span className="muted">
                {w.app ? `${w.app} · ` : ""}
                {w.approved_skills} published · {w.draft_skills} to review · {w.sessions} recorded{" "}
                {w.sessions === 1 ? "session" : "sessions"}
              </span>
            </button>
          ))}

          <article className="card">
            <h4>New workflow</h4>
            <label>
              Name
              <input placeholder="e.g. Approving expense reports" value={newName} onChange={(e) => setNewName(e.target.value)} />
            </label>
            <label>
              Software it's done in
              <input placeholder="e.g. Concur, Salesforce, an internal tool" value={newApp} onChange={(e) => setNewApp(e.target.value)} />
            </label>
            <label>
              What the work is (optional)
              <textarea
                rows={2}
                placeholder="A sentence or two. Helps Claude understand what it's watching."
                value={newDescription}
                onChange={(e) => setNewDescription(e.target.value)}
              />
            </label>
            <button className="primary" onClick={create} disabled={creating || !newName.trim()}>
              Create workflow
            </button>
          </article>
        </>
      )}
    </section>
  );
}

function WorkflowDetail({
  workflow,
  expertName,
  onBack,
  onChanged,
}: {
  workflow: Workflow;
  expertName: string;
  onBack: () => void;
  onChanged: () => void;
}) {
  const [maps, setMaps] = useState<WorkMap[]>([]);
  const [recording, setRecording] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const load = useCallback(() => {
    api<WorkMap[]>(`/api/workflows/${workflow.id}/workmaps`)
      .then(setMaps)
      .catch((e) => setError(String(e)));
  }, [workflow.id]);
  useEffect(load, [load]);

  async function review(skills: Skill[], status: SkillStatus) {
    setBusy(true);
    setError("");
    try {
      for (const s of skills) await api(`/api/skills/${s.id}`, { method: "PATCH", body: { status } });
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
      load();
      onChanged();
    }
  }

  return (
    <>
      <div className="row">
        <button onClick={onBack} disabled={recording} title={recording ? "Stop the interview first" : undefined}>
          ← Workflows
        </button>
      </div>
      <div>
        <h3 className="title">{workflow.name}</h3>
        <p className="muted">
          {workflow.app ? `${workflow.app} · ` : ""}
          {workflow.approved_skills} published to trainees · {workflow.draft_skills} waiting for review
        </p>
        {workflow.description && <p className="muted">{workflow.description}</p>}
      </div>

      <article className="card">
        <h4>Record a session</h4>
        {!expertName && <p className="muted">Enter your name above to record.</p>}
        <Capture
          workflowId={workflow.id}
          expertName={expertName}
          onRecordingChange={setRecording}
          onBuilt={() => {
            load();
            onChanged();
          }}
        />
      </article>

      <FieldsEditor workflow={workflow} onSaved={onChanged} />
      <CasesEditor workflow={workflow} />

      {error && <p className="error">{error}</p>}
      <h4>Recorded sessions ({maps.length})</h4>
      {maps.length === 0 && <p className="muted">Nothing recorded yet. Skills you approve here are what trainees practice.</p>}
      {maps.map((m) => {
        const drafts = m.skills.filter((s) => s.status === "draft");
        return (
          <details key={m.id} className="workmap" open={drafts.length > 0}>
            <summary>
              <b>{m.expert_name}</b> · {new Date(m.recorded_at * 1000).toLocaleString()} · {m.skills.length} skills
              {drafts.length > 0 && <span className="pill draft">{drafts.length} to review</span>}
            </summary>
            <p className="muted">{m.summary}</p>
            {drafts.length > 1 && (
              <button className="primary" onClick={() => review(drafts, "approved")} disabled={busy}>
                Approve all {drafts.length}
              </button>
            )}
            {m.skills.map((s) => (
              <SkillCard
                key={s.id}
                skill={s}
                actions={
                  s.status === "approved" ? (
                    <button onClick={() => review([s], "draft")} disabled={busy}>
                      Unpublish
                    </button>
                  ) : (
                    <>
                      <button className="primary" onClick={() => review([s], "approved")} disabled={busy}>
                        Approve
                      </button>
                      <button onClick={() => review([s], "rejected")} disabled={busy}>
                        Reject
                      </button>
                    </>
                  )
                }
              />
            ))}
          </details>
        );
      })}
    </>
  );
}
