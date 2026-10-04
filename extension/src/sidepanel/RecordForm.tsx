import { showValue, type RecordField, type WorkRecord } from "../lib/api";

const label = (f: RecordField) => f.name.replace(/_/g, " ");

/** Read-only grid of a record's fields. */
export function RecordView({ fields, record }: { fields: RecordField[]; record: WorkRecord }) {
  return (
    <dl className="grid">
      {fields.map((f) => (
        <div key={f.name} title={f.description}>
          <dt>{label(f)}</dt>
          <dd>{showValue(record[f.name])}</dd>
        </div>
      ))}
    </dl>
  );
}

/** Editable record, one input per field, typed by the workflow's field definitions. */
export function RecordForm({ fields, record, onChange }: { fields: RecordField[]; record: WorkRecord; onChange: (r: WorkRecord) => void }) {
  const set = (name: string, value: unknown) => onChange({ ...record, [name]: value });
  return (
    <>
      {fields.map((f) => {
        const v = record[f.name];
        if (f.type === "boolean") {
          return (
            <label key={f.name} className="check" title={f.description}>
              {label(f)}
              <select value={v === true ? "true" : v === false ? "false" : "unknown"} onChange={e => set(f.name, e.target.value === "unknown" ? null : e.target.value === "true")}>
                <option value="unknown">Unknown</option><option value="true">Yes</option><option value="false">No</option>
              </select>
            </label>
          );
        }
        return (
          <label key={f.name} title={f.description}>
            {label(f)}
            {f.type === "number" ? (
              <input
                type="number"
                value={typeof v === "number" ? v : ""}
                onChange={(e) => set(f.name, e.target.value === "" ? null : Number(e.target.value))}
              />
            ) : f.type === "list" ? (
              <input
                placeholder="comma-separated"
                value={Array.isArray(v) ? v.join(", ") : ""}
                onChange={(e) =>
                  set(
                    f.name,
                    e.target.value.split(",").map((s) => s.trim()).filter(Boolean),
                  )
                }
              />
            ) : (
              <input value={v === null || v === undefined ? "" : String(v)} onChange={(e) => set(f.name, e.target.value)} />
            )}
          </label>
        );
      })}
    </>
  );
}
