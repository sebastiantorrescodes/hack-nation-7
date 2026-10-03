import { useEffect, useState } from "react";
import { api, fmtCond, type WorkMap } from "../lib/api";

export default function WorkMapView({ active }: { active: boolean }) {
  const [maps, setMaps] = useState<WorkMap[]>([]);
  const [selected, setSelected] = useState<string>("");
  const [error, setError] = useState("");

  useEffect(() => {
    if (!active) return;
    api<WorkMap[]>("/api/workmaps")
      .then((m) => {
        setMaps(m);
        setSelected((cur) => cur || m[m.length - 1]?.id || "");
      })
      .catch((e) => setError(String(e)));
  }, [active]);

  const wm = maps.find((m) => m.id === selected);

  return (
    <section>
      {error && <p className="error">{error}</p>}
      <select value={selected} onChange={(e) => setSelected(e.target.value)}>
        {maps.map((m) => (
          <option key={m.id} value={m.id}>
            {m.expert_name} · {m.skills.length} skills ({m.id})
          </option>
        ))}
      </select>
      {wm && (
        <>
          <p className="muted">{wm.summary}</p>
          {wm.skills.map((s) => (
            <article key={s.id} className="card">
              <h3>{s.title}</h3>
              <dl>
                <dt>When</dt>
                <dd>
                  {s.trigger.map((c, i) => (
                    <code key={i}>{fmtCond(c)}</code>
                  ))}
                </dd>
                <dt>Do</dt>
                <dd>
                  <b>{s.action.kind.replace(/_/g, " ")}</b>: {s.action.detail}
                </dd>
                <dt>Why</dt>
                <dd className="quote">“{s.expert_explanation}”</dd>
                <dt>Guardrail</dt>
                <dd>
                  {s.guardrail.description}
                  <div>
                    {s.guardrail.must.map((c, i) => (
                      <code key={i}>{fmtCond(c)}</code>
                    ))}
                  </div>
                </dd>
              </dl>
            </article>
          ))}
        </>
      )}
    </section>
  );
}
