import { useEffect, useState, type FormEvent } from "react";
import { TextInput } from "../../components/Field";
import { useToast } from "../../components/Toast";
import { api, errorMessage } from "../../lib/api";
import type { AppSettings } from "../../lib/types";

export function PairsEditor({ pairs, onSaved }: { pairs: string[]; onSaved: (s: AppSettings) => void }) {
  const toast = useToast();
  const [draft, setDraft] = useState<string[]>(pairs);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => setDraft(pairs), [pairs]);
  const dirty = JSON.stringify(draft) !== JSON.stringify(pairs);

  const add = (e: FormEvent) => {
    e.preventDefault();
    const p = input.trim().toUpperCase();
    if (!/^[A-Z0-9]{2,10}\/[A-Z0-9]{2,10}$/.test(p)) {
      setErr("Use the form BASE/QUOTE, e.g. ETH/USDC");
      return;
    }
    setErr(null);
    if (!draft.includes(p)) setDraft([...draft, p]);
    setInput("");
  };

  const save = async () => {
    setBusy(true);
    setErr(null);
    try {
      const r = await api.put<{ settings: AppSettings }>("/api/settings", { patch: { general: { pairs: draft } } });
      onSaved(r.settings);
      toast.success(`Pairs saved (${draft.length}) — connectors are rebuilt`);
    } catch (e) {
      setErr(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="stack">
      <div className="row" aria-label="Trading pairs">
        {draft.length === 0 ? <span className="small muted">No pairs — the scanner has nothing to evaluate.</span> : null}
        {draft.map((p) => (
          <span key={p} className="chip">
            {p}
            <button type="button" aria-label={`Remove ${p}`} onClick={() => setDraft(draft.filter((x) => x !== p))}>×</button>
          </span>
        ))}
      </div>
      <form onSubmit={add} className="row">
        <TextInput value={input} onChange={(e) => setInput(e.target.value)} placeholder="Add pair, e.g. ETH/USDC" aria-label="New pair" style={{ maxWidth: 220 }} />
        <button type="submit" className="btn" disabled={!input.trim()}>Add</button>
        <button type="button" className="btn btn-primary" onClick={save} disabled={busy || !dirty}>{busy ? "Saving…" : "Save pairs"}</button>
        {dirty ? <button type="button" className="btn btn-ghost" onClick={() => setDraft(pairs)}>Discard</button> : null}
      </form>
      {err ? <div className="danger-text" role="alert">{err}</div> : null}
    </div>
  );
}
