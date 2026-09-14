import { useState, type FormEvent } from "react";
import { Card } from "../../components/Card";
import { Field, Select, TextInput } from "../../components/Field";
import { EmptyState } from "../../components/States";
import { Table } from "../../components/Table";
import { useToast } from "../../components/Toast";
import { api, errorMessage } from "../../lib/api";
import { CHAINS } from "../../lib/chains";
import { chainLabel } from "../../lib/format";
import type { AllowlistEntry } from "../../lib/types";

export function AllowlistManager({ entries, required, onChanged }: { entries: AllowlistEntry[]; required: boolean; onChanged: () => Promise<void> }) {
  const toast = useToast();
  const [chain, setChain] = useState("base");
  const [address, setAddress] = useState("");
  const [label, setLabel] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const add = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true); setErr(null);
    try {
      await api.post("/api/wallets/allowlist", { chain, address: address.trim(), label: label.trim() });
      toast.success("Address added to the withdrawal allowlist");
      setAddress(""); setLabel("");
      await onChanged();
    } catch (e2) { setErr(errorMessage(e2)); } finally { setBusy(false); }
  };

  const remove = async (a: AllowlistEntry) => {
    try {
      await api.del(`/api/wallets/allowlist?address=${encodeURIComponent(a.address)}`);
      toast.success("Address removed");
      await onChanged();
    } catch (e2) { toast.error(errorMessage(e2)); }
  };

  return (
    <Card title="Withdrawal allowlist" actions={required ? <span className="pill pill-success">REQUIRED FOR WITHDRAWALS</span> : <span className="pill pill-warning">NOT ENFORCED</span>}>
      <p className="small muted">Withdrawals can only go to these addresses{required ? "" : " once enforcement is enabled in Settings › Security"}.</p>
      <Table
        dense
        columns={[
          { key: "chain", header: "Network", render: (a) => chainLabel(a.chain) },
          { key: "addr", header: "Address", render: (a) => <span className="address">{a.address}</span> },
          { key: "label", header: "Label", render: (a) => a.label || <span className="faint">—</span> },
          { key: "x", header: "", noLabel: true, render: (a) => <button type="button" className="btn btn-sm" onClick={() => remove(a)}>Remove</button> },
        ]}
        rows={entries}
        rowKey={(a) => `${a.chain}-${a.address}`}
        empty={<EmptyState title="Allowlist is empty" />}
      />
      <form onSubmit={add} className="mt-12">
        <div className="form-grid">
          <Field label="Network">{(id) => <Select id={id} value={chain} onChange={(e) => setChain(e.target.value)}>{CHAINS.map((c) => <option key={c} value={c}>{chainLabel(c)}</option>)}</Select>}</Field>
          <Field label="Address">{(id) => <TextInput id={id} className="mono" value={address} onChange={(e) => setAddress(e.target.value)} required spellCheck={false} />}</Field>
          <Field label="Label">{(id) => <TextInput id={id} value={label} onChange={(e) => setLabel(e.target.value)} placeholder="e.g. Ledger cold wallet" />}</Field>
        </div>
        {err ? <div className="danger-text mt-8" role="alert">{err}</div> : null}
        <div className="form-actions"><button type="submit" className="btn btn-primary" disabled={busy || !address}>{busy ? "Adding…" : "Add address"}</button></div>
      </form>
    </Card>
  );
}
