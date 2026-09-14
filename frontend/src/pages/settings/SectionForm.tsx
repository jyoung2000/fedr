import { useEffect, useMemo, useState } from "react";
import { Checkbox, Field, Select, TextArea, TextInput } from "../../components/Field";
import { useToast } from "../../components/Toast";
import { api, errorMessage } from "../../lib/api";
import type { AppSettings } from "../../lib/types";
import type { FieldDef, SectionDef } from "./schema";

type Draft = Record<string, string | boolean>;

function toDraft(fields: FieldDef[], values: Record<string, unknown>): Draft {
  const d: Draft = {};
  for (const f of fields) {
    const v = values[f.key];
    switch (f.type) {
      case "bool":
        d[f.key] = Boolean(v);
        break;
      case "list":
        d[f.key] = Array.isArray(v) ? v.join(", ") : "";
        break;
      case "dict_decimal":
      case "json":
        d[f.key] = v === undefined ? "" : JSON.stringify(v, null, f.type === "json" ? 2 : 0);
        break;
      default:
        d[f.key] = v === null || v === undefined ? "" : String(v);
    }
  }
  return d;
}

function parse(f: FieldDef, raw: string | boolean): unknown {
  switch (f.type) {
    case "bool":
      return Boolean(raw);
    case "int": {
      const s = String(raw).trim();
      if (s === "") {
        if (f.nullable) return null;
        throw new Error(`${f.label} is required`);
      }
      const n = Number(s);
      if (!Number.isInteger(n)) throw new Error(`${f.label} must be a whole number`);
      return n;
    }
    case "decimal": {
      const s = String(raw).trim();
      if (s === "") {
        if (f.nullable) return null;
        throw new Error(`${f.label} is required`);
      }
      if (!/^-?\d+(\.\d+)?$/.test(s)) throw new Error(`${f.label} must be a number`);
      return s;
    }
    case "list":
      return String(raw).split(",").map((x) => x.trim()).filter(Boolean);
    case "dict_decimal":
    case "json": {
      const s = String(raw).trim();
      if (s === "") return f.type === "json" ? {} : {};
      try {
        return JSON.parse(s);
      } catch {
        throw new Error(`${f.label}: invalid JSON`);
      }
    }
    default:
      return String(raw);
  }
}

interface Props {
  section: SectionDef;
  values: Record<string, unknown>;
  onSaved: (s: AppSettings) => void;
}

/** Generic per-section settings form; saves only changed fields via PUT /api/settings {patch}. */
export function SectionForm({ section, values, onSaved }: Props) {
  const toast = useToast();
  const valuesKey = JSON.stringify(values);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const initial = useMemo(() => toDraft(section.fields, values), [section, valuesKey]);
  const [draft, setDraft] = useState<Draft>(initial);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [fieldErr, setFieldErr] = useState<Record<string, string>>({});
  useEffect(() => setDraft(initial), [initial]);

  const dirty = section.fields.filter((f) => draft[f.key] !== initial[f.key]);

  const save = async () => {
    const patch: Record<string, unknown> = {};
    const errs: Record<string, string> = {};
    for (const f of dirty) {
      try {
        patch[f.key] = parse(f, draft[f.key]);
      } catch (e) {
        errs[f.key] = errorMessage(e);
      }
    }
    setFieldErr(errs);
    if (Object.keys(errs).length) return;
    setBusy(true);
    setErr(null);
    try {
      const r = await api.put<{ settings: AppSettings }>("/api/settings", { patch: { [section.key]: patch } });
      onSaved(r.settings);
      toast.success(`${section.title} saved (${Object.keys(patch).length} field${Object.keys(patch).length === 1 ? "" : "s"})`);
    } catch (e) {
      setErr(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };

  const set = (k: string, v: string | boolean) => setDraft((d) => ({ ...d, [k]: v }));

  return (
    <div className="stack">
      {section.description ? <p className="small muted">{section.description}</p> : null}
      <div className="form-grid">
        {section.fields.map((f) => {
          if (f.type === "bool") {
            return <Checkbox key={f.key} label={f.label} help={f.help} checked={Boolean(draft[f.key])} onChange={(v) => set(f.key, v)} disabled={f.readOnly} />;
          }
          if (f.type === "dict_decimal") {
            let obj: Record<string, string> = {};
            try {
              obj = JSON.parse(String(draft[f.key] || "{}"));
            } catch {
              obj = {};
            }
            const keys = Object.keys(obj);
            return (
              <div key={f.key} className="field" style={{ gridColumn: "1 / -1" }}>
                <span className="label">{f.label}</span>
                {keys.length === 0 ? <span className="help">No entries.</span> : null}
                <div className="form-grid">
                  {keys.map((k) => (
                    <Field key={k} label={k}>{(id) => <TextInput id={id} type="number" step="any" value={obj[k]} onChange={(e) => set(f.key, JSON.stringify({ ...obj, [k]: e.target.value }))} />}</Field>
                  ))}
                </div>
                {f.help ? <span className="help">{f.help}</span> : null}
                {fieldErr[f.key] ? <span className="error" role="alert">{fieldErr[f.key]}</span> : null}
              </div>
            );
          }
          if (f.type === "json") {
            return (
              <Field key={f.key} label={f.label} help={f.help} error={fieldErr[f.key]} className="field-wide">
                {(id) => <TextArea id={id} value={String(draft[f.key])} onChange={(e) => set(f.key, e.target.value)} rows={4} spellCheck={false} />}
              </Field>
            );
          }
          if (f.type === "select") {
            return (
              <Field key={f.key} label={f.label} help={f.help} error={fieldErr[f.key]}>
                {(id) => (
                  <Select id={id} value={String(draft[f.key])} onChange={(e) => set(f.key, e.target.value)} disabled={f.readOnly}>
                    {f.options?.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
                  </Select>
                )}
              </Field>
            );
          }
          const numeric = f.type === "int" || f.type === "decimal";
          return (
            <Field key={f.key} label={f.label} help={f.help} unit={f.unit} error={fieldErr[f.key]}>
              {(id) => <TextInput id={id} type={numeric ? "number" : "text"} step={f.type === "int" ? 1 : f.step ?? "any"} min={f.min} max={f.max} value={String(draft[f.key])} onChange={(e) => set(f.key, e.target.value)} readOnly={f.readOnly} aria-invalid={Boolean(fieldErr[f.key])} placeholder={f.nullable ? "blank = none" : undefined} />}
            </Field>
          );
        })}
      </div>
      {err ? <div className="danger-text" role="alert">{err}</div> : null}
      <div className="form-actions">
        <button type="button" className="btn btn-primary" onClick={save} disabled={busy || dirty.length === 0}>{busy ? "Saving…" : `Save ${section.title}`}</button>
        <button type="button" className="btn" onClick={() => { setDraft(initial); setFieldErr({}); setErr(null); }} disabled={busy || dirty.length === 0}>Discard changes</button>
        {dirty.length ? <span className="small muted">{dirty.length} unsaved change{dirty.length === 1 ? "" : "s"}</span> : null}
      </div>
    </div>
  );
}
