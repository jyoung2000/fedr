import { fmtAmount, fmtDateTime, fmtMoney, fmtTime, num, strategyLabel, titleCase } from "../lib/format";
import type { OrderLeg, TradeDetail, TradeRowDetail, TradeSummary } from "../lib/types";
import { Modal } from "./Modal";
import { Money } from "./MoneyText";
import { StatusPill, type Tone } from "./StatusPill";
import { Table } from "./Table";

export function tradeTone(status: string): Tone {
  switch (status) {
    case "filled":
    case "hedged":
      return "success";
    case "failed":
    case "aborted":
    case "cancelled":
      return "danger";
    case "recovering":
    case "partial":
      return "warning";
    default:
      return "info";
  }
}

type AnyTrade = (TradeDetail | TradeSummary | TradeRowDetail) & { legs?: Record<string, OrderLeg>; events?: Record<string, unknown>[]; estimated_costs?: Record<string, string>; detail?: Record<string, unknown> };

function pick(t: AnyTrade) {
  const d = (t.detail ?? {}) as Record<string, unknown>;
  const legs: Record<string, OrderLeg> = t.legs ?? {};
  for (const k of ["buy", "sell", "hedge"]) {
    if (!legs[k] && d[k]) legs[k] = d[k] as OrderLeg;
  }
  const events = (t.events ?? (d.events as Record<string, unknown>[] | undefined) ?? []) as Record<string, unknown>[];
  const costs = (t.estimated_costs ?? (d.estimated_costs as Record<string, string> | undefined) ?? {}) as Record<string, string>;
  return { legs, events, costs };
}

/** Trade detail: legs, fills, timeline and the estimated-vs-realized breakdown. */
export function TradeDetailModal({ trade, onClose }: { trade: AnyTrade | null; onClose: () => void }) {
  if (!trade) return null;
  const { legs, events, costs } = pick(trade);
  const est = num(trade.estimated_net);
  const real = num(trade.actual_net);
  const diff = est !== null && real !== null ? real - est : null;
  const started = "started_at_ms" in trade && trade.started_at_ms ? trade.started_at_ms : (trade as TradeRowDetail).started_at ?? null;
  const completed = "completed_at_ms" in trade && trade.completed_at_ms ? trade.completed_at_ms : (trade as TradeRowDetail).completed_at ?? null;
  return (
    <Modal open onClose={onClose} title={`Trade ${trade.pair} · ${trade.route}`} size="lg">
      <div className="stack">
        <div className="row">
          <StatusPill tone={tradeTone(trade.status)}>{trade.status.toUpperCase()}</StatusPill>
          <span className="tag">{strategyLabel(trade.strategy)}</span>
          <span className="tag">{trade.mode}</span>
          <span className="small muted mono">{trade.id}</span>
        </div>
        <p className="small">{trade.explanation || "No explanation recorded."}</p>

        <div className="grid grid-3">
          <div className="stat">
            <span className="label">Estimated net</span>
            <Money value={trade.estimated_net} className="value" />
          </div>
          <div className="stat">
            <span className="label">Realized net</span>
            {real === null ? <span className="value muted">{["aborted", "failed", "cancelled"].includes(trade.status) ? "— (nothing executed)" : "pending"}</span> : <Money value={trade.actual_net} className="value" />}
          </div>
          <div className="stat">
            <span className="label">Difference</span>
            {diff === null ? <span className="value muted">—</span> : <Money value={diff} className="value" />}
          </div>
        </div>
        <p className="small muted">
          Estimated {fmtMoney(trade.estimated_net)} / Realized {real === null ? (["aborted", "failed", "cancelled"].includes(trade.status) ? "—" : "pending") : fmtMoney(trade.actual_net)} / Difference {diff === null ? "—" : fmtMoney(diff)} · worst case modelled {fmtMoney(trade.estimated_worst_case)}
        </p>

        <div className="table-wrap">
          <table className="table dense">
            <thead>
              <tr>
                <th scope="col">Line</th>
                <th scope="col" className="num">Estimated</th>
                <th scope="col" className="num">Realized</th>
              </tr>
            </thead>
            <tbody>
              <tr><td>Gross</td><td className="num"><Money value={trade.estimated_gross} /></td><td className="num"><Money value={trade.actual_gross} /></td></tr>
              <tr><td>Trading fees</td><td className="num mono">{fmtMoney(sumKeys(costs, ["buy_trading_fee", "sell_trading_fee", "dex_swap_fee", "lp_fee", "maker_taker_adjustment"]), { sign: false })}</td><td className="num mono">{fmtMoney(trade.actual_fees, { sign: false })}</td></tr>
              <tr><td>Gas</td><td className="num mono">{fmtMoney(sumKeys(costs, ["gas", "priority_fee"]), { sign: false })}</td><td className="num mono">{fmtMoney(trade.actual_gas, { sign: false })}</td></tr>
              <tr><td>Total modelled costs</td><td className="num mono">{fmtMoney(costs.total ?? null, { sign: false })}</td><td className="num muted">—</td></tr>
              <tr><td className="strong">Net</td><td className="num"><Money value={trade.estimated_net} /></td><td className="num"><Money value={trade.actual_net} /></td></tr>
            </tbody>
          </table>
        </div>

        <h3>Legs</h3>
        {Object.keys(legs).length === 0 ? (
          <p className="small muted">No leg submitted{trade.status === "aborted" ? " (aborted before submission)" : ""}.</p>
        ) : (
          <div className="grid grid-2">
            {Object.entries(legs).map(([name, leg]) => (
              <div key={name} className="card" style={{ boxShadow: "none" }}>
                <div className="card-body stack-sm">
                  <div className="row-between">
                    <strong>{titleCase(name)} · {leg.request?.venue}</strong>
                    <StatusPill tone={leg.status === "filled" ? "success" : leg.status === "rejected" || leg.status === "failed" ? "danger" : "muted"}>{leg.status}</StatusPill>
                  </div>
                  <dl className="kv small">
                    <dt>Order</dt><dd className="mono">{leg.request?.side} {fmtAmount(leg.request?.amount)} {leg.request?.symbol} {leg.request?.limit_price ? `@ ${fmtAmount(leg.request.limit_price, 4)}` : "(market)"} {leg.request?.time_in_force}</dd>
                    <dt>Filled</dt><dd className="mono">{fmtAmount(leg.filled)} @ {leg.avg_price ? fmtAmount(leg.avg_price, 4) : "—"}</dd>
                    <dt>Fee</dt><dd className="mono">{fmtMoney(leg.fee_quote, { sign: false, decimals: 4 })}{num(leg.gas_cost_usd) ? ` · gas ${fmtMoney(leg.gas_cost_usd, { sign: false, decimals: 4 })}` : ""}</dd>
                    {leg.tx_hash ? (<><dt>Tx</dt><dd className="mono break">{leg.tx_hash}</dd></>) : null}
                    {leg.error ? (<><dt>Error</dt><dd className="money neg">{leg.error}</dd></>) : null}
                    <dt>Submitted</dt><dd>{fmtTime(leg.submitted_at_ms)}{leg.completed_at_ms ? ` → ${fmtTime(leg.completed_at_ms)}` : ""}</dd>
                  </dl>
                  {leg.fills && leg.fills.length > 0 ? (
                    <Table
                      dense
                      columns={[
                        { key: "t", header: "Time", render: (f) => fmtTime(f.ts_ms) },
                        { key: "amt", header: "Amount", render: (f) => fmtAmount(f.amount), align: "right" },
                        { key: "px", header: "Price", render: (f) => fmtAmount(f.price, 4), align: "right" },
                        { key: "fee", header: "Fee", render: (f) => `${fmtAmount(f.fee_amount, 6)} ${f.fee_asset}`, align: "right" },
                      ]}
                      rows={leg.fills}
                      rowKey={(f, ) => `${f.order_id}-${f.ts_ms}-${f.amount}`}
                    />
                  ) : null}
                </div>
              </div>
            ))}
          </div>
        )}

        <h3>Timeline</h3>
        <ul className="timeline">
          <li><span className="t">{started ? fmtDateTime(started) : "—"}</span><span>Started</span></li>
          {events.map((ev, i) => {
            const ts = (ev.ts ?? ev.ts_ms) as number | undefined;
            const name = String(ev.event ?? ev.kind ?? ev.type ?? "event");
            const rest = Object.entries(ev).filter(([k]) => !["ts", "ts_ms", "event", "kind", "type"].includes(k));
            return (
              <li key={i}>
                <span className="t">{ts ? fmtDateTime(ts) : ""}</span>
                <span>
                  <strong>{titleCase(name)}</strong>
                  {rest.length ? <span className="muted"> — {rest.map(([k, v]) => `${k}: ${String(v)}`).join(", ")}</span> : null}
                </span>
              </li>
            );
          })}
          {completed ? <li><span className="t">{fmtDateTime(completed)}</span><span>Completed ({trade.status})</span></li> : null}
        </ul>
      </div>
    </Modal>
  );
}

function sumKeys(costs: Record<string, string>, keys: string[]): number | null {
  let total = 0;
  let any = false;
  for (const k of keys) {
    const n = num(costs[k]);
    if (n !== null) {
      total += n;
      any = true;
    }
  }
  return any ? total : null;
}
