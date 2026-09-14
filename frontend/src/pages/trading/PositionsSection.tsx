import { useState } from "react";
import { Card } from "../../components/Card";
import { ConfirmDialog } from "../../components/ConfirmDialog";
import { Money, Pct } from "../../components/MoneyText";
import { Skeleton } from "../../components/Spinner";
import { EmptyState, ErrorState } from "../../components/States";
import { StatusPill, type Tone } from "../../components/StatusPill";
import { Table, type Column } from "../../components/Table";
import { useToast } from "../../components/Toast";
import { api } from "../../lib/api";
import { fmtAmount, fmtDateTime, fmtPct, num, strategyLabel } from "../../lib/format";
import { usePoll } from "../../lib/hooks";
import { useSseEvent } from "../../lib/sse";
import type { PositionRow, PositionsResponse } from "../../lib/types";

const CLOSE_REASON = "manual close";

function positionTone(status: string): Tone {
  switch (status) {
    case "open":
      return "success";
    case "closing":
      return "warning";
    case "failed":
      return "danger";
    default:
      return "muted";
  }
}

function fmtHours(v: string | number | null | undefined): string {
  const n = num(v);
  return n === null ? "—" : `${n.toFixed(1)} h`;
}

function Venues({ p }: { p: PositionRow }) {
  return (
    <span className="stack-sm" style={{ gap: 0 }}>
      <span className="small">
        <span className="mono">{p.spot_venue}</span> spot / <span className="mono">{p.perp_venue}</span> perp
      </span>
      <span className="tiny muted mono">{p.perp_symbol}</span>
    </span>
  );
}

/** Open spot↔perp carry positions with live marks, manual close, and the closed-position history. Polls every 10 s. */
export function PositionsSection({ emergencyStop }: { emergencyStop?: boolean }) {
  const toast = useToast();
  const { data, error, loading, refresh } = usePoll<PositionsResponse>(() => api.get<PositionsResponse>("/api/trading/positions"), 10000);
  useSseEvent("position", () => void refresh());
  useSseEvent("trade", () => void refresh());
  const [closing, setClosing] = useState<PositionRow | null>(null);
  const [closeAllOpen, setCloseAllOpen] = useState(false);

  const open = data?.open ?? [];
  const history = data?.history ?? [];
  const policy = data?.policy ?? null;
  const minLiq = num(policy?.min_liquidation_distance_pct);

  const openCols: Column<PositionRow>[] = [
    {
      key: "pair",
      header: "Pair",
      render: (p) => (
        <span className="stack-sm" style={{ gap: 2 }}>
          <span><strong>{p.pair}</strong> <span className="tag">{strategyLabel(p.strategy)}</span></span>
          {p.suspended ? (
            <span className="tiny" style={{ maxWidth: 180, display: "block" }}>
              <StatusPill tone="warning">SUSPENDED</StatusPill> {p.suspended}
            </span>
          ) : null}
        </span>
      ),
    },
    { key: "venues", header: "Venues (spot / perp)", render: (p) => <Venues p={p} /> },
    { key: "size", header: "Size", render: (p) => <span className="mono">{fmtAmount(p.size_base)}</span>, align: "right" },
    {
      key: "basis",
      header: "Basis (entry → now)",
      align: "right",
      render: (p) => (
        <span className="nowrap">
          <span className="mono" title="entry basis">{fmtPct(p.entry_basis_pct, { decimals: 3 })}</span> → {p.basis_pct === undefined ? <span className="muted">—</span> : <Pct value={p.basis_pct} decimals={3} />}
        </span>
      ),
    },
    { key: "unreal", header: "Unrealized", render: (p) => (p.unrealized_usd === undefined ? <span className="muted">—</span> : <Money value={p.unrealized_usd} />), align: "right" },
    { key: "funding", header: "Funding", render: (p) => <Money value={p.funding_usd ?? p.funding_collected_usd} title="funding collected" />, align: "right" },
    {
      key: "liq",
      header: "Liq. distance",
      align: "right",
      render: (p) => {
        const v = num(p.liquidation_distance_pct);
        if (v === null) return <span className="muted">—</span>;
        const low = minLiq !== null && v < minLiq;
        return (
          <span className={`mono ${low ? "money neg" : ""}`}>
            {fmtPct(v, { sign: false, decimals: 1 })}
            {low ? <span className="tiny"> (below {minLiq}% minimum)</span> : null}
          </span>
        );
      },
    },
    { key: "hours", header: "Open", render: (p) => <span className="mono">{fmtHours(p.hours_open)}</span>, align: "right" },
    {
      key: "status",
      header: "Status",
      render: (p) => <StatusPill tone={positionTone(p.status)}>{p.status.toUpperCase()}</StatusPill>,
    },
    {
      key: "act",
      header: "",
      noLabel: true,
      className: "sticky-right",
      render: (p) => (
        <button type="button" className="btn btn-sm btn-danger" onClick={() => setClosing(p)} disabled={p.status !== "open"} data-testid="position-close" aria-label={`Close position ${p.pair} (${p.spot_venue} / ${p.perp_venue})`}>
          Close
        </button>
      ),
    },
  ];

  const historyCols: Column<PositionRow>[] = [
    { key: "closed", header: "Closed at", render: (p) => <span className="nowrap">{fmtDateTime(p.closed_at)}</span> },
    {
      key: "pair",
      header: "Pair",
      render: (p) => (
        <span className="stack-sm" style={{ gap: 2 }}>
          <span><strong>{p.pair}</strong> <span className="tag">{strategyLabel(p.strategy)}</span></span>
          {p.suspended ? (
            <span className="tiny" style={{ maxWidth: 180, display: "block" }}>
              <StatusPill tone="warning">SUSPENDED</StatusPill> {p.suspended}
            </span>
          ) : null}
        </span>
      ),
    },
    { key: "venues", header: "Venues (spot / perp)", render: (p) => <Venues p={p} /> },
    { key: "size", header: "Size", render: (p) => <span className="mono">{fmtAmount(p.size_base)}</span>, align: "right" },
    { key: "entry", header: "Entry basis", render: (p) => <span className="mono">{fmtPct(p.entry_basis_pct, { decimals: 3 })}</span>, align: "right" },
    { key: "reason", header: "Exit reason", render: (p) => <span className="small">{p.exit_reason ?? <span className="muted">—</span>}</span> },
    { key: "funding", header: "Funding", render: (p) => <Money value={p.funding_collected_usd} />, align: "right" },
    { key: "fees", header: "Fees", render: (p) => <span className="mono">{fmtAmount(p.fees_usd, 2) === "—" ? "—" : `$${fmtAmount(p.fees_usd, 2)}`}</span>, align: "right" },
    { key: "net", header: "Realized net", render: (p) => (p.realized_net_usd === null ? <span className="muted">—</span> : <Money value={p.realized_net_usd} />), align: "right" },
    { key: "status", header: "Status", render: (p) => <StatusPill tone={positionTone(p.status)}>{p.status.toUpperCase()}</StatusPill> },
  ];

  const closeOne = async (p: PositionRow) => {
    await api.post(`/api/trading/positions/${encodeURIComponent(p.id)}/close`, { reason: CLOSE_REASON });
    toast.success(`Close submitted for ${p.pair} (${p.spot_venue} / ${p.perp_venue})`);
    await refresh();
  };

  const closeAll = async () => {
    const r = await api.post<{ closed: unknown[] }>("/api/trading/positions/close-all", { reason: CLOSE_REASON });
    toast.success(`${r.closed.length} close order(s) submitted`);
    await refresh();
  };

  return (
    <Card
      id="positions"
      title={`Carry positions (${open.length} open)`}
      actions={
        <>
          {error && data ? <StatusPill tone="warning">refresh failed: {error}</StatusPill> : null}
          <button type="button" className="btn btn-sm btn-danger" onClick={() => setCloseAllOpen(true)} disabled={open.length === 0} data-testid="positions-close-all">
            Close all
          </button>
        </>
      }
      footer={data ? <span data-testid="positions-verification">{data.verification}</span> : undefined}
    >
      {loading && !data ? (
        <Skeleton />
      ) : !data ? (
        <ErrorState message={error ?? "no data"} onRetry={refresh} />
      ) : (
        <div className="stack">
          {policy ? (
            <p className="small muted">
              Exit rules: basis ≤ {policy.exit_basis_pct}% · funding negative for {policy.funding_flip_periods} periods · max hold {policy.max_hold_hours} h · liquidation distance ≥ {policy.min_liquidation_distance_pct}% · basis widening &gt; {policy.max_basis_widening_pct}% · flatten on emergency stop: {policy.flatten_on_emergency_stop ? "yes" : "no (exits suspended)"}
            </p>
          ) : null}
          {emergencyStop && open.length > 0 ? (
            <div className="danger-text" role="status">
              Emergency stop is active: automatic exits are {policy?.flatten_on_emergency_stop ? "flattening positions" : "suspended"}; manual Close still submits a close.
            </div>
          ) : null}
          <div data-testid="positions-open">
            <Table
              dense
              caption="Open carry positions"
              columns={openCols}
              rows={open}
              rowKey={(p) => p.id}
              empty={<EmptyState title="No open carry positions">Spot ↔ perp positions opened by the Basis and Funding strategies appear here with live basis, funding and liquidation distance.</EmptyState>}
            />
          </div>
          <details className="accordion" data-testid="positions-history">
            <summary>Closed positions ({history.length})</summary>
            <div className="accordion-body">
              <Table dense caption="Closed carry positions" columns={historyCols} rows={history} rowKey={(p) => p.id} empty={<EmptyState title="No closed positions yet in this mode" />} />
            </div>
          </details>
        </div>
      )}

      <ConfirmDialog
        open={closing !== null}
        onClose={() => setClosing(null)}
        title={closing ? `Close position ${closing.pair}?` : "Close position?"}
        tone="danger"
        confirmLabel="Close position"
        onConfirm={async () => {
          if (closing) await closeOne(closing);
        }}
      >
        {closing ? (
          <p>
            Both legs are closed at market: the spot leg ({fmtAmount(closing.size_base)} {closing.pair}) is sold on <span className="mono">{closing.spot_venue}</span> and the perp short is bought back on <span className="mono">{closing.perp_venue}</span>. Reason recorded: <code>{CLOSE_REASON}</code>.
          </p>
        ) : null}
      </ConfirmDialog>

      <ConfirmDialog
        open={closeAllOpen}
        onClose={() => setCloseAllOpen(false)}
        title={`Close all ${open.length} open position${open.length === 1 ? "" : "s"}?`}
        tone="danger"
        confirmLabel="Close all"
        checkboxLabel="I understand every open carry position is flattened at market"
        onConfirm={closeAll}
      >
        <p>
          Every open position is closed at market on both venues. Reason recorded: <code>{CLOSE_REASON}</code>.
        </p>
      </ConfirmDialog>
    </Card>
  );
}
