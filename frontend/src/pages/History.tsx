import { useState } from "react";
import { Card } from "../components/Card";
import { Select } from "../components/Field";
import { Money } from "../components/MoneyText";
import { Skeleton } from "../components/Spinner";
import { EmptyState, ErrorState } from "../components/States";
import { DecisionPill, StatusPill } from "../components/StatusPill";
import { Table } from "../components/Table";
import { Tabs } from "../components/Tabs";
import { useToast } from "../components/Toast";
import { TradeDetailModal, tradeTone } from "../components/TradeDetailModal";
import { api, errorMessage, qs } from "../lib/api";
import { useAppState } from "../lib/app-state";
import { fmtDateTime, fmtMoney, fmtPct, strategyLabel, titleCase } from "../lib/format";
import { usePoll } from "../lib/hooks";
import type { AuditRow, OpportunityLogRow, RiskEventRow, TradeRow, TradeRowDetail } from "../lib/types";
import { BacktestsTab } from "./history/BacktestsTab";
import { PnlTab } from "./history/PnlTab";

const TABS = [
  { key: "trades", label: "Trades" },
  { key: "opportunities", label: "Opportunities" },
  { key: "audit", label: "Audit log" },
  { key: "risk", label: "Risk events" },
  { key: "pnl", label: "P&L" },
  { key: "backtests", label: "Backtests" },
];
const MODES = ["simulation", "paper", "testnet", "live"];

export function History() {
  const { status } = useAppState();
  const [tab, setTab] = useState("trades");
  const [mode, setMode] = useState<string>("");
  const effectiveMode = mode || status?.mode || "";
  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>History</h1>
          <div className="sub">Trades and P&amp;L are queried per mode; opportunity, audit and risk logs follow the current mode.</div>
        </div>
        <div className="field" style={{ minWidth: 180 }}>
          <label htmlFor="hist-mode">Mode</label>
          <Select id="hist-mode" value={mode} onChange={(e) => setMode(e.target.value)}>
            <option value="">Current ({(status?.mode ?? "…").toUpperCase()})</option>
            {MODES.map((m) => <option key={m} value={m}>{m.toUpperCase()}</option>)}
          </Select>
        </div>
      </div>
      <Tabs tabs={TABS} value={tab} onChange={setTab} ariaLabel="History sections" />
      <div role="tabpanel" aria-labelledby={`tab-${tab}`}>
        {tab === "trades" ? <TradesTab mode={effectiveMode} /> : null}
        {tab === "opportunities" ? <OpportunitiesTab /> : null}
        {tab === "audit" ? <AuditTab /> : null}
        {tab === "risk" ? <RiskTab /> : null}
        {tab === "pnl" ? <PnlTab mode={effectiveMode} /> : null}
        {tab === "backtests" ? <BacktestsTab /> : null}
      </div>
    </div>
  );
}

function TradesTab({ mode }: { mode: string }) {
  const toast = useToast();
  const { data, error, loading, refresh } = usePoll<{ mode: string; items: TradeRow[] }>(() => api.get(`/api/history/trades${qs({ limit: 200, mode })}`), 10000, [mode]);
  const [selected, setSelected] = useState<TradeRowDetail | null>(null);
  const open = async (t: TradeRow) => {
    try { setSelected(await api.get<TradeRowDetail>(`/api/history/trades/${t.id}`)); } catch (e) { toast.error(errorMessage(e)); }
  };
  if (loading && !data) return <Card><Skeleton lines={5} /></Card>;
  if (!data) return <ErrorState message={error ?? "no data"} onRetry={refresh} />;
  return (
    <Card title={`Trades (${data.mode.toUpperCase()})`}>
      <Table
        caption="Trade history"
        columns={[
          { key: "t", header: "Time", render: (t) => <span className="nowrap">{fmtDateTime(t.started_at)}</span> },
          { key: "pair", header: "Pair", render: (t) => <strong>{t.pair}</strong> },
          { key: "strat", header: "Strategy", render: (t) => strategyLabel(t.strategy) },
          { key: "route", header: "Route", render: (t) => <span className="mono small">{t.route}</span> },
          { key: "gross", header: "Gross", render: (t) => <Money value={t.actual_gross ?? t.estimated_gross} colored={false} />, align: "right" },
          { key: "fees", header: "Fees", render: (t) => <span className="mono">{fmtMoney(t.actual_fees, { sign: false })}</span>, align: "right" },
          { key: "gas", header: "Gas", render: (t) => <span className="mono">{fmtMoney(t.actual_gas, { sign: false })}</span>, align: "right" },
          { key: "net", header: "Net (realized)", render: (t) => (t.actual_net === null ? <span className="muted">—</span> : <Money value={t.actual_net} />), align: "right" },
          { key: "est", header: "Estimated net", render: (t) => <Money value={t.estimated_net} />, align: "right" },
          { key: "status", header: "Status", render: (t) => <StatusPill tone={tradeTone(t.status)}>{t.status.toUpperCase()}</StatusPill> },
        ]}
        rows={data.items}
        rowKey={(t) => t.id}
        onRowClick={open}
        empty={<EmptyState title={`No trades recorded in ${data.mode.toUpperCase()} mode`} />}
      />
      <TradeDetailModal trade={selected} onClose={() => setSelected(null)} />
    </Card>
  );
}

function OpportunitiesTab() {
  const { data, error, loading, refresh } = usePoll<{ items: OpportunityLogRow[] }>(() => api.get("/api/history/opportunities?limit=200"), 10000);
  if (loading && !data) return <Card><Skeleton lines={5} /></Card>;
  if (!data) return <ErrorState message={error ?? "no data"} onRetry={refresh} />;
  return (
    <Card title="Decision log">
      <Table
        caption="Opportunity decisions"
        columns={[
          { key: "t", header: "Time", render: (o) => <span className="nowrap">{fmtDateTime(o.created_at)}</span> },
          { key: "pair", header: "Pair", render: (o) => <span><strong>{o.pair}</strong> <span className="tag">{strategyLabel(o.strategy)}</span></span> },
          { key: "route", header: "Route", render: (o) => <span className="mono small">{o.route}</span> },
          { key: "gross", header: "Gross", render: (o) => <span className="mono">{fmtPct(o.gross_pct)}</span>, align: "right" },
          { key: "exp", header: "Expected net", render: (o) => <Money value={o.expected_net_usd} />, align: "right" },
          { key: "worst", header: "Worst case", render: (o) => <Money value={o.worst_case_usd} />, align: "right" },
          { key: "risk", header: "Risk", render: (o) => o.risk_score, align: "right" },
          { key: "dec", header: "Decision", render: (o) => <div className="stack-sm"><DecisionPill status={o.decision} /><span className="tiny muted">{o.explanation}</span></div> },
        ]}
        rows={data.items}
        rowKey={(o) => o.id}
        empty={<EmptyState title="No decisions logged yet" />}
      />
    </Card>
  );
}

function AuditTab() {
  const [type, setType] = useState("");
  const { data, error, loading, refresh } = usePoll<{ items: AuditRow[] }>(() => api.get(`/api/history/audit${qs({ limit: 200, event_type: type })}`), 10000, [type]);
  const TYPES = ["decision", "execution", "settings_change", "mode_change", "live_activation", "emergency_stop", "circuit_breaker", "wallet", "exchange", "reconciliation", "security", "system"];
  return (
    <Card title="Audit log" actions={<Select value={type} onChange={(e) => setType(e.target.value)} aria-label="Event type" style={{ minWidth: 160 }}><option value="">All event types</option>{TYPES.map((t) => <option key={t} value={t}>{titleCase(t)}</option>)}</Select>}>
      {loading && !data ? <Skeleton lines={5} /> : !data ? <ErrorState message={error ?? "no data"} onRetry={refresh} /> : data.items.length === 0 ? <EmptyState title="No audit events" /> : (
        <ul className="list-plain">
          {data.items.map((r) => (
            <li key={r.id}>
              <div className="row small">
                <span className="mono muted nowrap">{fmtDateTime(r.ts)}</span>
                <StatusPill tone={r.event_type === "emergency_stop" || r.event_type === "live_activation" ? "danger" : r.event_type === "circuit_breaker" ? "warning" : "muted"}>{r.event_type.replace(/_/g, " ").toUpperCase()}</StatusPill>
                <span className="tag">{r.actor}</span>
                <span className="tag">{r.mode}</span>
              </div>
              <div className="mt-8">{r.summary}</div>
              {r.payload && Object.keys(r.payload).length ? (
                <details className="why"><summary>Payload</summary><pre className="payload">{JSON.stringify(r.payload, null, 2)}</pre></details>
              ) : null}
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

function RiskTab() {
  const { data, error, loading, refresh } = usePoll<{ items: RiskEventRow[] }>(() => api.get("/api/history/risk-events?limit=100"), 10000);
  if (loading && !data) return <Card><Skeleton lines={5} /></Card>;
  if (!data) return <ErrorState message={error ?? "no data"} onRetry={refresh} />;
  return (
    <Card title="Risk events">
      {data.items.length === 0 ? <EmptyState title="No risk events">Circuit breakers, limit breaches and reconciliation discrepancies are listed here.</EmptyState> : (
        <ul className="list-plain">
          {data.items.map((r) => (
            <li key={r.id}>
              <div className="row small">
                <span className="mono muted nowrap">{fmtDateTime(r.ts)}</span>
                <StatusPill tone={r.severity === "critical" || r.severity === "error" ? "danger" : r.severity === "warning" ? "warning" : "muted"}>{r.severity.toUpperCase()}</StatusPill>
                <span className="tag">{r.kind}</span>
              </div>
              <div className="mt-8">{r.detail}</div>
              {r.payload && Object.keys(r.payload).length ? <details className="why"><summary>Payload</summary><pre className="payload">{JSON.stringify(r.payload, null, 2)}</pre></details> : null}
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}
