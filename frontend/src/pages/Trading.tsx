import { useState } from "react";
import { Link } from "react-router-dom";
import { Card } from "../components/Card";
import { Money } from "../components/MoneyText";
import { Skeleton } from "../components/Spinner";
import { EmptyState, ErrorState } from "../components/States";
import { StatusPill } from "../components/StatusPill";
import { Table } from "../components/Table";
import { useToast } from "../components/Toast";
import { Toggle } from "../components/Toggle";
import { TradeDetailModal, tradeTone } from "../components/TradeDetailModal";
import { api, errorMessage } from "../lib/api";
import { useAppState } from "../lib/app-state";
import { fmtAmount, fmtMoney, fmtTime, num, strategyLabel } from "../lib/format";
import { usePoll } from "../lib/hooks";
import { useSseEvent } from "../lib/sse";
import type { TradeDetail, TradeRowDetail, TradeSummary, TradingState } from "../lib/types";
import { InventorySection } from "./trading/InventorySection";
import { PaperSection } from "./trading/PaperSection";
import { ShadowSection } from "./trading/ShadowSection";

const MODES: { key: string; label: string; help: string }[] = [
  { key: "simulation", label: "Simulation", help: "synthetic market data, simulated balances" },
  { key: "paper", label: "Paper", help: "real public market data, simulated balances" },
  { key: "testnet", label: "Testnet", help: "real testnet APIs and test assets" },
];

export function Trading() {
  const toast = useToast();
  const { refreshStatus, snapshot } = useAppState();
  const { data, error, loading, refresh } = usePoll<TradingState>(() => api.get<TradingState>("/api/trading/state"), 3000);
  const [busyKey, setBusyKey] = useState<string | null>(null);
  const [selected, setSelected] = useState<TradeDetail | TradeRowDetail | null>(null);
  useSseEvent("trade", () => void refresh());

  const act = async (key: string, fn: () => Promise<unknown>, okMsg: string) => {
    setBusyKey(key);
    try {
      await fn();
      toast.success(okMsg);
      await Promise.all([refresh(), refreshStatus()]);
    } catch (err) {
      toast.error(errorMessage(err));
    } finally {
      setBusyKey(null);
    }
  };

  const openTrade = async (t: TradeSummary) => {
    const live = data?.active.find((a) => a.id === t.id);
    if (live) {
      setSelected(live);
      return;
    }
    try {
      setSelected(await api.get<TradeRowDetail>(`/api/history/trades/${t.id}`));
    } catch (err) {
      toast.error(errorMessage(err));
    }
  };

  if (loading && !data) {
    return (
      <div className="page">
        <Card title="Controls"><Skeleton /></Card>
      </div>
    );
  }
  if (!data) return <ErrorState message={error ?? "no data"} onRetry={refresh} />;

  const active = data.active.length ? data.active : (snapshot?.open_trades ?? []);
  const tradeCols = [
    { key: "t", header: "Time", render: (t: TradeSummary) => fmtTime(t.started_at_ms) },
    { key: "pair", header: "Pair", render: (t: TradeSummary) => <span><strong>{t.pair}</strong> <span className="tag">{strategyLabel(t.strategy)}</span></span> },
    { key: "route", header: "Route", render: (t: TradeSummary) => <span className="mono small">{t.route}</span> },
    { key: "size", header: "Size", render: (t: TradeSummary) => <span className="mono">{fmtAmount(t.size_base)}</span>, align: "right" as const },
    { key: "est", header: "Estimated net", render: (t: TradeSummary) => <Money value={t.estimated_net} />, align: "right" as const },
    { key: "real", header: "Realized net", render: (t: TradeSummary) => (t.actual_net === null ? <span className="muted">pending</span> : <Money value={t.actual_net} />), align: "right" as const },
    { key: "diff", header: "Difference", render: (t: TradeSummary) => (t.actual_net === null || t.estimated_net === null ? <span className="muted">—</span> : <Money value={(num(t.actual_net) ?? 0) - (num(t.estimated_net) ?? 0)} />), align: "right" as const },
    { key: "status", header: "Status", render: (t: TradeSummary) => <StatusPill tone={tradeTone(t.status)}>{t.status.toUpperCase()}</StatusPill> },
  ];
  const venues = Array.from(new Set(data.inventory.map((l) => l.venue))).sort();

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>Trading</h1>
          <div className="sub">Mode {data.mode.toUpperCase()} · {data.emergency_stop ? "EMERGENCY STOP ACTIVE" : "bot " + (data.bot_enabled ? "enabled" : "paused")}</div>
        </div>
        {error ? <StatusPill tone="warning">refresh failed: {error}</StatusPill> : null}
      </div>

      <div className="grid grid-2">
        <Card title="Controls">
          <div className="stack">
            <Toggle checked={data.bot_enabled} busy={busyKey === "bot"} label="Bot" description="Master switch for the scanner and automatic execution" onChange={(v) => act("bot", () => api.post("/api/trading/bot", { enabled: v }), v ? "Bot enabled" : "Bot paused")} />
            <Toggle checked={data.auto_execute} busy={busyKey === "auto"} label="Auto-execute" description="Execute SAFE TO EXECUTE opportunities automatically; off means the Execute button only" onChange={(v) => act("auto", () => api.post("/api/trading/auto-execute", { enabled: v }), v ? "Auto-execute on" : "Auto-execute off")} />
            <Toggle checked={data.shadow_mode} busy={busyKey === "shadow"} label="Shadow mode" description="Evaluate and record decisions without submitting anything" onChange={(v) => act("shadow", () => api.post("/api/trading/shadow", { enabled: v }), v ? "Shadow mode on" : "Shadow mode off")} />
          </div>
        </Card>
        <Card title="Trading mode">
          <div className="segmented" role="group" aria-label="Trading mode">
            {MODES.map((m) => (
              <button key={m.key} type="button" aria-pressed={data.mode === m.key} disabled={busyKey === "mode" || data.mode === "live"} onClick={() => data.mode !== m.key && act("mode", () => api.post("/api/trading/mode", { mode: m.key }), `Mode set to ${m.label}`)} title={m.help}>
                {m.label}
              </button>
            ))}
          </div>
          <ul className="small muted mt-8">
            {MODES.map((m) => (
              <li key={m.key}><strong>{m.label}</strong> — {m.help}</li>
            ))}
          </ul>
          <p className="small mt-8">
            {data.mode === "live" ? <span className="money neg">LIVE mode is active. </span> : null}
            LIVE is not selectable here — it requires the readiness checklist and typed confirmation in <Link to="/settings#live">Settings › Live trading</Link>.
          </p>
        </Card>
      </div>

      <Card title={`Active trades (${active.length})`}>
        <Table columns={tradeCols} rows={active} rowKey={(t) => t.id} onRowClick={openTrade} empty={<EmptyState title="No trade in flight" />} />
      </Card>

      <Card title="Recent trades">
        <Table columns={tradeCols} rows={data.recent} rowKey={(t) => t.id} onRowClick={openTrade} empty={<EmptyState title="No trades yet in this mode">Executed trades appear here with estimated vs realized net.</EmptyState>} />
      </Card>

      <ShadowSection stats={data.shadow} shadowOn={data.shadow_mode} />

      {data.paper.editable ? <PaperSection paper={data.paper} mode={data.mode} venues={venues} onChanged={refresh} /> : null}

      <InventorySection lines={data.inventory} onChanged={refresh} />

      <div className="grid grid-2">
        <Card title="Rebalancing">
          {data.rebalance.length === 0 ? (
            <p className="small">
              <StatusPill tone="success">No rebalance needed</StatusPill>
            </p>
          ) : (
            <div className="stack">
              {data.rebalance.map((r, i) => (
                <div key={i} className="banner banner-warning banner-inline" role="status">
                  <div className="banner-body stack-sm">
                    <strong>REBALANCE RECOMMENDED</strong>
                    <span className="mono">{fmtAmount(r.amount)} {r.asset}: {r.from} → {r.to}</span>
                    <span className="small">cost {fmtMoney(r.cost_usd, { sign: false })} · benefit {fmtMoney(r.benefit_usd, { sign: false })} · net <Money value={r.net_usd} /></span>
                    <span className="small">{r.reason}</span>
                  </div>
                </div>
              ))}
              <p className="tiny muted">Recommendations only — FEDR never moves funds between venues automatically.</p>
            </div>
          )}
        </Card>
        <Card title="Experience (learning)">
          <p className="small muted">Per-route prediction error learned from realized trades; the extra buffer is added to the required profit.</p>
          <Table
            dense
            columns={[
              { key: "key", header: "Route", render: (e) => <span className="mono small break">{e.key}</span> },
              { key: "n", header: "Samples", render: (e) => e.samples, align: "right" },
              { key: "err", header: "Error %", render: (e) => <span className="mono">{Number(e.error_pct).toFixed(3)}%</span>, align: "right" },
              { key: "fill", header: "Fill reliability", render: (e) => <span className="mono">{(Number(e.fill_reliability) * 100).toFixed(0)}%</span>, align: "right" },
              { key: "buf", header: "Extra buffer", render: (e) => <span className="mono">{Number(e.extra_buffer_pct).toFixed(3)}%</span>, align: "right" },
            ]}
            rows={data.experience}
            rowKey={(e) => e.key}
            empty={<EmptyState title="No learning samples yet" />}
          />
        </Card>
      </div>

      <TradeDetailModal trade={selected} onClose={() => setSelected(null)} />
    </div>
  );
}
