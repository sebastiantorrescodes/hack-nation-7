import type { ReactNode } from "react";
import { fmtCond, type Skill } from "../lib/api";

export default function SkillCard({ skill: s, actions }: { skill: Skill; actions?: ReactNode }) {
  return (
    <article className="card">
      <div className="card-head">
        <h3>{s.title}</h3>
        <span className={`pill ${s.status}`}>{s.status === "approved" ? "published" : s.status}</span>
      </div>
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
      {actions && <div className="row">{actions}</div>}
    </article>
  );
}
