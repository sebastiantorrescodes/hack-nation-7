import { useCallback, useEffect, useRef, useState } from "react";
import { api, type Workflow } from "../lib/api";
import { useStored } from "../lib/useStored";
import Tutor from "./Tutor";

export default function Trainee({ active }: { active: boolean }) {
  const [name, setName] = useStored("learnerName");
  const [workflows, setWorkflows] = useState<Workflow[]>([]);
  const [selected, setSelected] = useState<Workflow | null>(null);
  const [inSession, setInSession] = useState(false);
  const [error, setError] = useState("");

  const learner = name.trim();
  // Progress is per learner, but refetching on every keystroke is wasteful: the name input reloads on blur.
  const learnerRef = useRef(learner);
  learnerRef.current = learner;
  const load = useCallback(() => {
    api<Workflow[]>(`/api/workflows/published?learner=${encodeURIComponent(learnerRef.current)}`)
      .then(async items => {
        setWorkflows(items);
        const saved = await chrome.storage.local.get("guardSave");
        const resume = items.find(w => w.id === saved.guardSave?.workflowId);
        if (resume) setSelected(resume);
      })
      .catch((e) => setError(String(e)));
  }, []);
  useEffect(() => {
    if (active && !selected) load();
  }, [active, selected, load]);

  return (
    <section>
      <label>
        Your name
        <input
          placeholder="e.g. Sam Rivera"
          value={name}
          onChange={(e) => setName(e.target.value)}
          onBlur={load}
          disabled={inSession}
        />
      </label>
      {error && <p className="error">{error}</p>}

      {selected ? (
        <>
          <div className="row">
            <button onClick={() => setSelected(null)} disabled={inSession} title={inSession ? "Finish this record first" : undefined}>
              ← Workflows
            </button>
          </div>
          <div>
            <h3 className="title">{selected.name}</h3>
            <p className="muted">
              {selected.app ? `${selected.app} · ` : ""}
              {selected.approved_skills} skills taught by experts
            </p>
          </div>
          <Tutor workflow={selected} learner={learner} onSessionChange={setInSession} />
        </>
      ) : (
        <>
          <h4>What you can practice</h4>
          {!learner && <p className="muted">Enter your name to start practicing and track your progress.</p>}
          {workflows.length === 0 && <p className="muted">No workflows are published yet. An expert needs to approve some skills first.</p>}
          {workflows.map((w) => {
            const mastered = w.mastered_skills ?? 0;
            return (
              <button key={w.id} className="card link" onClick={() => setSelected(w)} disabled={!learner}>
                <h3>{w.name}</h3>
                <span className="muted">
                  {w.app ? `${w.app} · ` : ""}
                  {w.approved_skills} skills · {learner ? `${mastered} mastered` : "not started"}
                </span>
                {learner && (
                  <div className="progress" aria-label={`${mastered} of ${w.approved_skills} mastered`}>
                    <div style={{ width: `${(100 * mastered) / w.approved_skills}%` }} />
                  </div>
                )}
              </button>
            );
          })}
        </>
      )}
    </section>
  );
}
