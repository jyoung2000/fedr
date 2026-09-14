import { useState } from "react";
import { Card } from "../../components/Card";
import { ConfirmDialog } from "../../components/ConfirmDialog";
import { Money } from "../../components/MoneyText";
import { EmptyState } from "../../components/States";
import { StatusPill } from "../../components/StatusPill";
import { Table } from "../../components/Table";
import { useToast } from "../../components/Toast";
import { api } from "../../lib/api";
import { fmtAmount, fmtMoney, fmtTime, strategyLabel } from "../../lib/format";
import { usePoll } from "../../lib/hooks";
import type { ShadowRecord, ShadowSummary } from "../../lib/types";

interface Resp {
  summary: ShadowSummary;
  items: ShadowRecord[];
}

export function ShadowSection({ stats, shadowOn }: { stats: ShadowSummary & { evaluated: number; would_trade: number }; shadowOn: boolean }) {
  const toast = useToast();
  const [clearOpen, setClearOpen] = useState(false);
  const { data, refresh } = usePoll<Resp>(() => api.get<Resp>("/api/trading/shadow/records?limit=100"), 5000);
  const summary = data?.summary ?? stats;
  return (
    <Card
      title="Shadow mode"
      actions={
        <>
          {shadowOn ? <StatusPill tone="info">RECORDING</StatusPill> : <StatusPill tone="muted">OFF</StatusPill>}
          <button type="button" className="btn btn-sm" onClick={() => setClearOpen(true)} disabled={!data || data.items.length === 0}>
            Clear records
          </button>
        </>
      }
    >
      <p className="small muted">Shadow mode evaluates every opportunity exactly as live would, records what it <em>would</em> have done and later compares the prediction with what the market actually did — nothing is submitted.</p>
      <div className="stat-grid mt-12">
        <div className="stat"><span className="label">Evaluated</span><span className="value">{summary.evaluated}</span></div>
        <div className="stat"><span className="label">Would trade</span><span className="value">{summary.would_trade}</span></div>
        <div className="stat"><span className="label">Predicted P&amp;L</span><Money value={summary.predicted_pnl} className="value" /></div>
        <div className="stat"><span className="label">Hypothetical P&amp;L</span><Money value={summary.hypothetical_pnl} className="value" /></div>
      </div>
      <div className="mt-12">
        <Table
          dense
          caption="Shadow records"
          columns={[
            { key: "ts", header: "Time", render: (r) => fmtTime(r.ts) },
            { key: "pair", header: "Pair", render: (r) => <span><strong>{r.pair}</strong> <span className="tag">{strategyLabel(r.strategy)}</span></span> },
            { key: "route", header: "Route", render: (r) => <span className="mono small">{r.route}</span> },
            { key: "size", header: "Size", render: (r) => fmtAmount(r.size_base), align: "right" },
            { key: "would", header: "Decision", render: (r) => (r.would_trade ? <StatusPill tone="success">WOULD TRADE</StatusPill> : <StatusPill tone="muted">SKIP</StatusPill>) },
            { key: "pred", header: "Predicted", render: (r) => <Money value={r.predicted_profit} />, align: "right" },
            { key: "worst", header: "Worst", render: (r) => <Money value={r.worst_case_profit} />, align: "right" },
            { key: "hypo", header: "Hypothetical", render: (r) => (r.hypothetical_profit === null ? <span className="muted">pending</span> : <Money value={r.hypothetical_profit} />), align: "right" },
            { key: "reason", header: "Reason", render: (r) => <span className="small muted">{r.reason}</span> },
          ]}
          rows={data?.items ?? []}
          rowKey={(r) => String(r.id)}
          empty={<EmptyState title="No shadow records yet">{shadowOn ? "Records appear as opportunities are evaluated." : "Turn shadow mode on to start recording."}</EmptyState>}
        />
      </div>
      <ConfirmDialog
        open={clearOpen}
        onClose={() => setClearOpen(false)}
        title="Clear shadow records?"
        tone="danger"
        confirmLabel="Clear"
        onConfirm={async () => {
          await api.post("/api/trading/shadow/clear");
          toast.success("Shadow records cleared");
          await refresh();
        }}
      >
        <p>All {data?.items.length ?? 0} shadow records and the running summary ({fmtMoney(summary.predicted_pnl)} predicted) will be deleted.</p>
      </ConfirmDialog>
    </Card>
  );
}
