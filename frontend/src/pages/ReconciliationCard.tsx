import { useState } from "react";
import { Card } from "../components/Card";
import { Field, TextArea, TextInput } from "../components/Field";
import { EmptyState } from "../components/States";
import { StatusPill } from "../components/StatusPill";
import { Table } from "../components/Table";
import { useToast } from "../components/Toast";
import { api, errorMessage } from "../lib/api";
import { fmtAmount, fmtTime } from "../lib/format";
import { usePoll } from "../lib/hooks";

interface Report {
  ok: boolean;
  status: "ok" | "incomplete" | "discrepancy";
  discrepancies: string[];
  incomplete: string[];
  deferred: string[];
  checkpoints: string[];
  unresolved_orders: string[];
  ts_ms: number;
}
interface ReconciliationResponse {
  mode: string;
  simulated: boolean;
  last: Report | null;
  expected: { account: string; asset: string; balance: string }[];
  entries: { ts: string; account: string; asset: string; delta: string; kind: string; actor: string; note: string }[];
}

/** Durable-ledger reconciliation: expected balances, last result, and explicit operator adjustments. */
export function ReconciliationCard() {
  const toast = useToast();
  const { data, refresh } = usePoll<ReconciliationResponse>(() => api.get<ReconciliationResponse>("/api/system/reconciliation?limit=20"), 15000);
  const [adj, setAdj] = useState({ account: "", asset: "", observed: "", note: "" });
  const [busy, setBusy] = useState(false);
  if (!data) return null;
  const rep = data.last;
  const tone = !rep ? "muted" : rep.status === "ok" ? "success" : rep.status === "incomplete" ? "warning" : "danger";

  const run = async () => {
    setBusy(true);
    try {
      await api.post("/api/system/reconciliation/run");
      await refresh();
    } catch (err) {
      toast.error(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };
  const adjust = async () => {
    setBusy(true);
    try {
      await api.post("/api/system/reconciliation/adjust", adj);
      toast.success("Adjustment recorded");
      setAdj({ account: "", asset: "", observed: "", note: "" });
      await refresh();
    } catch (err) {
      toast.error(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card
      title="Reconciliation"
      actions={<button type="button" className="btn btn-sm" disabled={busy} onClick={() => void run()}>Run now</button>}
    >
      <p className="small">
        <StatusPill tone={tone}>{rep ? rep.status.toUpperCase() : "NOT RUN YET"}</StatusPill>{" "}
        {rep ? <span className="tiny muted">{fmtTime(rep.ts_ms)}</span> : null}
      </p>
      <p className="small muted">
        {data.simulated
          ? "Paper/simulation: the paper ledger is the book of record and is checked for internal consistency."
          : "Expected balances come from a durable ledger (opening checkpoint, confirmed fills, fees, gas, transfers, adjustments). A venue snapshot never overwrites them; a missing or failed observation is never OK."}
      </p>
      {rep && [...rep.discrepancies, ...rep.incomplete].length ? (
        <ul className="small">
          {rep.discrepancies.map((d) => <li key={d} className="money neg">{d}</li>)}
          {rep.incomplete.map((d) => <li key={d}>incomplete: {d}</li>)}
        </ul>
      ) : null}
      {rep && rep.deferred.length ? <p className="tiny muted">Deferred (work in flight): {rep.deferred.join("; ")}</p> : null}
      {!data.simulated ? (
        <>
          <Table
            dense
            columns={[
              { key: "a", header: "Account", render: (r) => <span className="mono">{r.account}</span> },
              { key: "s", header: "Asset", render: (r) => r.asset },
              { key: "b", header: "Expected", render: (r) => <span className="mono">{fmtAmount(r.balance)}</span>, align: "right" },
            ]}
            rows={data.expected}
            rowKey={(r) => `${r.account}|${r.asset}`}
            empty={<EmptyState title="No ledger yet" />}
          />
          <details className="mt-8">
            <summary className="small">Record an adjustment after an investigation…</summary>
            <div className="stack mt-8">
              <div className="grid grid-2">
                <Field label="Account">{(id) => <TextInput id={id} value={adj.account} onChange={(e) => setAdj({ ...adj, account: e.target.value })} />}</Field>
                <Field label="Asset">{(id) => <TextInput id={id} value={adj.asset} onChange={(e) => setAdj({ ...adj, asset: e.target.value })} />}</Field>
              </div>
              <Field label="Observed balance" help="The investigated, correct balance">{(id) => <TextInput id={id} inputMode="decimal" value={adj.observed} onChange={(e) => setAdj({ ...adj, observed: e.target.value })} />}</Field>
              <Field label="Investigation note">{(id) => <TextArea id={id} rows={2} value={adj.note} onChange={(e) => setAdj({ ...adj, note: e.target.value })} />}</Field>
              <button type="button" className="btn btn-primary" disabled={busy || adj.note.trim().length < 8 || !adj.account || !adj.asset || !adj.observed} onClick={() => void adjust()}>
                Record adjustment
              </button>
            </div>
          </details>
        </>
      ) : null}
    </Card>
  );
}
