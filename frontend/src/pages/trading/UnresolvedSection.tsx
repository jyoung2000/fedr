import { useState } from "react";
import { Card } from "../../components/Card";
import { Field, TextArea, TextInput } from "../../components/Field";
import { Modal } from "../../components/Modal";
import { EmptyState } from "../../components/States";
import { StatusPill } from "../../components/StatusPill";
import { useToast } from "../../components/Toast";
import { api, errorMessage } from "../../lib/api";
import { fmtAmount, fmtTime } from "../../lib/format";
import { usePoll } from "../../lib/hooks";
import { useSseEvent } from "../../lib/sse";

interface UnresolvedLeg {
  role: string;
  client_order_id: string;
  venue: string;
  status: string;
  filled: string | null;
  tx_hash: string | null;
  unknown_reason: string | null;
}
interface UnresolvedItem {
  id: string;
  pair: string;
  route: string;
  status: string;
  explanation: string;
  started_at_ms: number | null;
  legs: UnresolvedLeg[];
}
interface UnresolvedResponse {
  recovery_complete: boolean;
  recovery_note: string;
  foreign_mode_unresolved: string[];
  items: UnresolvedItem[];
}

const OPEN_STATES = ["unknown", "submitting", "cancel_pending", "open"];

/** Trades whose order outcome is unknown or whose fills lack authoritative values. They keep capital
 * reserved and pause their venues until FEDR resolves them by lookup or an operator resolves them here. */
export function UnresolvedSection() {
  const toast = useToast();
  const { data, refresh } = usePoll<UnresolvedResponse>(() => api.get<UnresolvedResponse>("/api/trading/unresolved"), 5000);
  useSseEvent("trade", () => void refresh());
  const [target, setTarget] = useState<{ trade: UnresolvedItem; leg: UnresolvedLeg } | null>(null);
  const [form, setForm] = useState({ filled: "", cost: "", fee_amount: "", fee_asset: "", note: "" });
  const [busy, setBusy] = useState(false);

  if (!data) return null;
  const blocked = !data.recovery_complete;
  if (!blocked && data.items.length === 0 && data.foreign_mode_unresolved.length === 0) return null;

  const submit = async () => {
    if (!target) return;
    setBusy(true);
    try {
      await api.post(`/api/trading/trades/${target.trade.id}/legs/${target.leg.client_order_id}/resolve`, {
        filled: form.filled || "0",
        cost: form.cost || null,
        fee_amount: form.fee_amount || null,
        fee_asset: form.fee_asset || null,
        note: form.note,
      });
      toast.success("Leg resolved and recorded in the audit log");
      setTarget(null);
      await refresh();
    } catch (err) {
      toast.error(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card title={`Unresolved orders (${data.items.length})`} tone="danger" id="unresolved">
      {blocked ? (
        <div className="banner banner-danger banner-inline" role="alert">
          <div className="banner-body">
            <strong>NEW TRADES BLOCKED</strong> — startup recovery has not completed: {data.recovery_note}
          </div>
        </div>
      ) : null}
      {data.foreign_mode_unresolved.length ? (
        <p className="small">
          <StatusPill tone="warning">Other mode</StatusPill> Unresolved trades from another mode: {data.foreign_mode_unresolved.join(", ")}. Switch to that mode to resolve them.
        </p>
      ) : null}
      <p className="small muted">
        An unknown outcome is never treated as “not filled”: the capital stays reserved and the venue stays paused until the
        order is found by its client order id / transaction hash, or until you record what the venue's own history shows.
      </p>
      {data.items.length === 0 ? (
        <EmptyState title="Nothing unresolved" />
      ) : (
        <div className="stack">
          {data.items.map((t) => (
            <div key={t.id} className="stack-sm" data-testid="unresolved-trade">
              <div>
                <strong>{t.pair}</strong> <span className="mono small">{t.route}</span>{" "}
                <StatusPill tone={t.status === "unknown" ? "danger" : "warning"}>{t.status.toUpperCase()}</StatusPill>{" "}
                <span className="tiny muted">{fmtTime(t.started_at_ms)}</span>
              </div>
              <div className="small">{t.explanation}</div>
              <ul className="small">
                {t.legs.map((l) => (
                  <li key={l.client_order_id}>
                    {l.role} on <span className="mono">{l.venue}</span> · <span className="mono">{l.client_order_id}</span>
                    {l.tx_hash ? <span className="mono tiny"> · tx {l.tx_hash}</span> : null} · {l.status}
                    {l.filled ? ` · filled ${fmtAmount(l.filled)}` : ""}
                    {l.unknown_reason ? <span className="muted"> ({l.unknown_reason})</span> : null}{" "}
                    {OPEN_STATES.includes(l.status) || t.status === "unreconciled" ? (
                      <button type="button" className="btn btn-sm" onClick={() => { setForm({ filled: "", cost: "", fee_amount: "", fee_asset: "", note: "" }); setTarget({ trade: t, leg: l }); }}>
                        Resolve from venue records…
                      </button>
                    ) : null}
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>
      )}
      <Modal open={target !== null} onClose={() => setTarget(null)} title="Resolve leg from the venue's records">
        {target ? (
          <div className="stack">
            <p className="small">
              Leg <span className="mono">{target.leg.client_order_id}</span> on <span className="mono">{target.leg.venue}</span>. Enter what the
              venue's order history (or a block explorer) shows. Filled 0 records that the order did not execute. The entry is audited.
            </p>
            <Field label="Filled (base units)">{(id) => <TextInput id={id} inputMode="decimal" value={form.filled} onChange={(e) => setForm({ ...form, filled: e.target.value })} />}</Field>
            <Field label="Total quote cost" help="Required when filled > 0">{(id) => <TextInput id={id} inputMode="decimal" value={form.cost} onChange={(e) => setForm({ ...form, cost: e.target.value })} />}</Field>
            <div className="grid grid-2">
              <Field label="Fee amount">{(id) => <TextInput id={id} inputMode="decimal" value={form.fee_amount} onChange={(e) => setForm({ ...form, fee_amount: e.target.value })} />}</Field>
              <Field label="Fee asset">{(id) => <TextInput id={id} value={form.fee_asset} onChange={(e) => setForm({ ...form, fee_asset: e.target.value })} />}</Field>
            </div>
            <Field label="Evidence" help="Where did you verify this? (order history export, explorer link, support ticket)">{(id) => <TextArea id={id} rows={3} value={form.note} onChange={(e) => setForm({ ...form, note: e.target.value })} />}</Field>
            <div className="row">
              <button type="button" className="btn btn-primary" disabled={busy || form.note.trim().length < 8} onClick={() => void submit()}>
                Record resolution
              </button>
              <button type="button" className="btn" onClick={() => setTarget(null)}>Cancel</button>
            </div>
          </div>
        ) : null}
      </Modal>
    </Card>
  );
}
