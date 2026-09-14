import { useState, type FormEvent } from "react";
import { Card } from "../../components/Card";
import { ConfirmDialog } from "../../components/ConfirmDialog";
import { Field, Select, TextInput } from "../../components/Field";
import { useToast } from "../../components/Toast";
import { api, errorMessage } from "../../lib/api";
import { fmtAmount } from "../../lib/format";
import type { TradingState } from "../../lib/types";

export function PaperSection({ paper, mode, venues, onChanged }: { paper: TradingState["paper"]; mode: string; venues: string[]; onChanged: () => Promise<void> }) {
  const toast = useToast();
  const [resetOpen, setResetOpen] = useState(false);
  const [venue, setVenue] = useState(venues[0] ?? "");
  const [asset, setAsset] = useState("USDC");
  const [amount, setAmount] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const assets = Array.from(new Set(Object.values(paper.starting_balances).flatMap((b) => Object.keys(b)))).sort();
  const balanceVenues = Object.keys(paper.starting_balances);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setErr(null);
    try {
      await api.post("/api/trading/paper/funds", { venue, asset: asset.toUpperCase(), amount });
      toast.success(`${Number(amount) < 0 ? "Removed" : "Added"} ${amount} ${asset.toUpperCase()} on ${venue}`);
      setAmount("");
      await onChanged();
    } catch (e2) {
      setErr(errorMessage(e2));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card title={`${mode === "simulation" ? "Simulation" : "Paper"} account`} actions={<button type="button" className="btn btn-sm btn-danger" onClick={() => setResetOpen(true)}>Reset paper account</button>}>
      <p className="small muted">Balances are simulated. Starting balances are what a reset seeds on every venue; stress multiplier {paper.stress_multiplier}× on slippage and gas, simulated latency {paper.latency_ms} ms.</p>
      <div className="table-wrap mt-12">
        <table className="table dense">
          <caption className="visually-hidden">Starting balances per venue</caption>
          <thead>
            <tr>
              <th scope="col">Venue</th>
              {assets.map((a) => (
                <th key={a} scope="col" className="num">{a}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {balanceVenues.map((v) => (
              <tr key={v}>
                <td className="strong">{v}</td>
                {assets.map((a) => (
                  <td key={a} className="num mono">{paper.starting_balances[v][a] !== undefined ? fmtAmount(paper.starting_balances[v][a]) : <span className="faint">—</span>}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <form onSubmit={submit} className="mt-16">
        <h3 className="mb-8">Add / remove simulated funds</h3>
        <div className="form-grid">
          <Field label="Venue">{(id) => (
            <Select id={id} value={venue} onChange={(e) => setVenue(e.target.value)} required>
              {Array.from(new Set([...venues, ...balanceVenues])).map((v) => (
                <option key={v} value={v}>{v}</option>
              ))}
            </Select>
          )}</Field>
          <Field label="Asset">{(id) => <TextInput id={id} value={asset} onChange={(e) => setAsset(e.target.value.toUpperCase())} required placeholder="USDC" />}</Field>
          <Field label="Amount" help="Negative removes funds">{(id) => <TextInput id={id} type="number" step="any" value={amount} onChange={(e) => setAmount(e.target.value)} required placeholder="100 or -100" />}</Field>
        </div>
        {err ? <div className="danger-text mt-8" role="alert">{err}</div> : null}
        <div className="form-actions">
          <button type="submit" className="btn btn-primary" disabled={busy || !amount || !venue}>{busy ? "Applying…" : "Apply"}</button>
        </div>
      </form>

      <ConfirmDialog
        open={resetOpen}
        onClose={() => setResetOpen(false)}
        title="Reset paper account?"
        tone="danger"
        confirmLabel="Reset"
        onConfirm={async () => {
          await api.post("/api/trading/paper/reset");
          toast.success("Paper account reset to starting balances");
          await onChanged();
        }}
      >
        <p>Balances return to the starting balances above, and all {mode} trades, P&amp;L and opportunity history for this mode are purged. Circuit breakers are reset.</p>
      </ConfirmDialog>
    </Card>
  );
}
