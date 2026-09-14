import { useState, type FormEvent } from "react";
import { Card } from "../../components/Card";
import { Field, Select, TextInput } from "../../components/Field";
import { EmptyState } from "../../components/States";
import { Table } from "../../components/Table";
import { useToast } from "../../components/Toast";
import { api, errorMessage } from "../../lib/api";
import { fmtAmount, fmtMoney } from "../../lib/format";
import type { InventoryLine } from "../../lib/types";

export function InventorySection({ lines, onChanged }: { lines: InventoryLine[]; onChanged: () => Promise<void> }) {
  const toast = useToast();
  const venues = Array.from(new Set(lines.map((l) => l.venue))).sort();
  const [venue, setVenue] = useState("");
  const [asset, setAsset] = useState("");
  const [target, setTarget] = useState("0");
  const [minimum, setMinimum] = useState("0");
  const [maximum, setMaximum] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const prefill = (l: InventoryLine) => {
    setVenue(l.venue);
    setAsset(l.asset);
    setTarget(l.target);
    setMinimum(l.minimum);
    setMaximum(l.maximum ?? "");
  };

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setErr(null);
    try {
      await api.post("/api/trading/inventory/target", { venue, asset: asset.toUpperCase(), target: target || "0", minimum: minimum || "0", maximum: maximum === "" ? null : maximum });
      toast.success(`Targets saved for ${asset.toUpperCase()} on ${venue}`);
      await onChanged();
    } catch (e2) {
      setErr(errorMessage(e2));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card title="Inventory">
      <Table
        dense
        caption="Inventory per venue and asset"
        columns={[
          { key: "venue", header: "Venue", render: (l) => <span><strong>{l.venue}</strong> <span className="tag">{l.kind}</span></span> },
          { key: "asset", header: "Asset", render: (l) => l.asset },
          { key: "avail", header: "Available", render: (l) => <span className="mono">{fmtAmount(l.available)}</span>, align: "right" },
          { key: "res", header: "Reserved", render: (l) => <span className="mono">{fmtAmount(l.reserved)}</span>, align: "right" },
          { key: "tgt", header: "Target / min / max", render: (l) => <span className="mono small">{fmtAmount(l.target)} / {fmtAmount(l.minimum)} / {l.maximum === null ? "∞" : fmtAmount(l.maximum)}</span>, align: "right" },
          { key: "usd", header: "USD", render: (l) => <span className="mono">{l.usd === null ? "unknown" : fmtMoney(l.usd, { sign: false })}</span>, align: "right" },
          { key: "act", header: "", noLabel: true, render: (l) => <button type="button" className="btn btn-sm" onClick={() => prefill(l)}>Set targets</button> },
        ]}
        rows={lines}
        rowKey={(l) => `${l.venue}-${l.asset}`}
        empty={<EmptyState title="No inventory" />}
      />
      <form onSubmit={submit} className="mt-16">
        <h3 className="mb-8">Set inventory targets</h3>
        <div className="form-grid">
          <Field label="Venue">{(id) => (
            <Select id={id} value={venue} onChange={(e) => setVenue(e.target.value)} required>
              <option value="">Select…</option>
              {venues.map((v) => (
                <option key={v} value={v}>{v}</option>
              ))}
            </Select>
          )}</Field>
          <Field label="Asset">{(id) => <TextInput id={id} value={asset} onChange={(e) => setAsset(e.target.value.toUpperCase())} required />}</Field>
          <Field label="Target" help="Desired balance the rebalancer aims for">{(id) => <TextInput id={id} type="number" step="any" min="0" value={target} onChange={(e) => setTarget(e.target.value)} />}</Field>
          <Field label="Minimum">{(id) => <TextInput id={id} type="number" step="any" min="0" value={minimum} onChange={(e) => setMinimum(e.target.value)} />}</Field>
          <Field label="Maximum" help="Leave blank for no cap">{(id) => <TextInput id={id} type="number" step="any" min="0" value={maximum} onChange={(e) => setMaximum(e.target.value)} />}</Field>
        </div>
        {err ? <div className="danger-text mt-8" role="alert">{err}</div> : null}
        <div className="form-actions">
          <button type="submit" className="btn btn-primary" disabled={busy || !venue || !asset}>{busy ? "Saving…" : "Save targets"}</button>
        </div>
      </form>
    </Card>
  );
}
