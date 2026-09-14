import { Link, useNavigate } from "react-router-dom";
import { Card } from "../components/Card";
import { Gauge } from "../components/Gauge";
import { Money } from "../components/MoneyText";
import { OpportunityRow, routeKey } from "../components/OpportunityRow";
import { Skeleton } from "../components/Spinner";
import { ErrorState, EmptyState } from "../components/States";
import { HealthDot, StatusPill } from "../components/StatusPill";
import { Table } from "../components/Table";
import { tradeTone } from "../components/TradeDetailModal";
import { api } from "../lib/api";
import { useAppState } from "../lib/app-state";
import { fmtAge, fmtAmount, fmtDuration, fmtLatency, fmtMoney, fmtTime, num } from "../lib/format";
import { usePoll } from "../lib/hooks";
import type { DashboardData, TradeSummary } from "../lib/types";

export function Dashboard() {
  const { status, snapshot } = useAppState();
  const nav = useNavigate();
  const { data, error, loading, refresh } = usePoll<DashboardData>(() => api.get<DashboardData>("/api/dashboard"), 5000);

  if (loading && !data) {
    return (
      <div className="page">
        <div className="grid grid-3">
          <Card title="Bot status"><Skeleton /></Card>
          <Card title="Capital"><Skeleton /></Card>
          <Card title="Risk limits"><Skeleton /></Card>
        </div>
      </div>
    );
  }
  if (!data) return <ErrorState message={error ?? "no data"} onRetry={refresh} />;

  const capital = snapshot?.capital ?? data.capital;
  const best = (snapshot?.opportunities ?? data.best).slice(0, 6);
  const active = snapshot?.open_trades ?? data.active_trades;
  const dailyPnl = snapshot?.daily_pnl ?? data.risk.daily_pnl;
  const wins = data.pnl.trades > 0 ? Math.round((data.pnl.wins / data.pnl.trades) * 100) : null;
  const dailyLoss = Math.max(0, -(num(dailyPnl) ?? 0));
  const maxLoss = num(data.risk.max_daily_loss) ?? 0;

  const tradeCols = [
    { key: "t", header: "Time", render: (t: TradeSummary) => fmtTime(t.started_at_ms) },
    { key: "pair", header: "Pair", render: (t: TradeSummary) => <strong>{t.pair}</strong> },
    { key: "route", header: "Route", render: (t: TradeSummary) => <span className="mono small">{t.route}</span> },
    { key: "est", header: "Estimated", render: (t: TradeSummary) => <Money value={t.estimated_net} />, align: "right" as const },
    { key: "real", header: "Realized", render: (t: TradeSummary) => (t.actual_net === null ? <span className="muted">{["aborted", "failed", "cancelled"].includes(t.status) ? "—" : "pending"}</span> : <Money value={t.actual_net} />), align: "right" as const },
    { key: "status", header: "Status", render: (t: TradeSummary) => <StatusPill tone={tradeTone(t.status)}>{t.status.toUpperCase()}</StatusPill> },
  ];

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>Dashboard</h1>
          <div className="sub">
            {data.counts.executable} safe to execute · {data.counts.blocked} blocked · {data.counts.routes} routes scanned · market data: {data.market_data.source} ({data.market_data.books} books)
          </div>
        </div>
        {error ? <StatusPill tone="warning">refresh failed: {error}</StatusPill> : null}
      </div>

      <div className="grid grid-3">
        <Card title="Bot status">
          <dl className="kv">
            <dt>Mode</dt>
            <dd className="strong">{(snapshot?.mode ?? data.mode).toUpperCase()}</dd>
            <dt>Bot</dt>
            <dd>{data.bot_enabled ? <StatusPill tone="success">ENABLED</StatusPill> : <StatusPill tone="warning">PAUSED</StatusPill>}</dd>
            <dt>Shadow mode</dt>
            <dd>{data.shadow_mode ? <StatusPill tone="info">ON — nothing submitted</StatusPill> : <span>off</span>}</dd>
            <dt>Auto-execute</dt>
            <dd>{status ? (status.auto_execute ? "on" : "off — manual Execute only") : "…"}</dd>
            <dt>Status</dt>
            <dd>{status?.status_message ?? "…"}</dd>
            <dt>Scans</dt>
            <dd>
              {status ? `${status.scan.count} · every ${status.scan.interval_ms} ms · last ${status.scan.duration_ms} ms` : "…"}
            </dd>
            <dt>Market data</dt>
            <dd>{data.market_data.source}</dd>
            <dt>Uptime</dt>
            <dd>{fmtDuration(status?.uptime_s)}</dd>
          </dl>
          <div className="mt-12">
            <Link to="/trading" className="btn btn-sm">
              Trading controls
            </Link>
          </div>
        </Card>

        <Card title="Capital">
          <div className="stat-grid">
            <div className="stat"><span className="label">Total</span><span className="value mono">{fmtMoney(capital.total, { sign: false })}</span></div>
            <div className="stat"><span className="label">Usable</span><span className="value mono">{fmtMoney(capital.usable, { sign: false })}</span></div>
            <div className="stat"><span className="label">Reserved</span><span className="value mono">{fmtMoney(capital.reserved, { sign: false })}</span></div>
            <div className="stat"><span className="label">At risk</span><span className="value mono">{fmtMoney(capital.at_risk, { sign: false })}</span></div>
            <div className="stat"><span className="label">Today P&amp;L</span><Money value={dailyPnl} className="value" /></div>
            <div className="stat"><span className="label">Total realized net</span><Money value={data.pnl.total.net} className="value" /></div>
          </div>
          <p className="small muted mt-12">
            {data.pnl.trades} trades · {data.pnl.wins} wins{wins !== null ? ` (${wins}%)` : ""} · reserves: emergency {fmtMoney(capital.emergency_reserve, { sign: false })}, inventory {fmtMoney(capital.inventory_reserve, { sign: false })}, gas {fmtMoney(capital.gas_reserve, { sign: false })}
          </p>
        </Card>

        <Card title="Risk limits">
          <div className="stack">
            <Gauge label="Daily loss used" value={dailyLoss} max={maxLoss} valueText={fmtMoney(dailyPnl)} maxText={`-${fmtMoney(maxLoss, { sign: false })} limit`} />
            <Gauge label="Failed trades (last hour)" value={data.risk.failed_last_hour} max={data.risk.max_failed} valueText={String(data.risk.failed_last_hour)} maxText={`${data.risk.max_failed} max`} />
            <Gauge label="Open trades" value={active.length} max={data.risk.max_concurrent} valueText={String(active.length)} maxText={`${data.risk.max_concurrent} max`} />
            {data.breakers.length > 0 ? <StatusPill tone="warning">{data.breakers.length} circuit breaker(s) tripped</StatusPill> : <span className="small muted">Circuit breakers armed · none tripped</span>}
          </div>
        </Card>
      </div>

      <div className="grid grid-main-side">
        <Card
          title="Best opportunities"
          actions={
            <Link to="/opportunities" className="btn btn-sm">
              All opportunities
            </Link>
          }
          bodyClassName="opp-list"
        >
          {best.length === 0 ? (
            <EmptyState title="No opportunities evaluated yet">Waiting for the next scan{status ? ` (every ${status.scan.interval_ms} ms)` : ""}.</EmptyState>
          ) : (
            <>
              {data.counts.executable === 0 ? (
                <div className="banner banner-muted small" style={{ margin: "-14px -16px 0", borderRadius: 0 }}>
                  No executable opportunities right now — every route is blocked by the Profit Guard ({data.counts.blocked} blocked of {data.counts.routes} routes). Rows below are ranked by worst-case net.
                </div>
              ) : null}
              <div style={{ margin: "0 -16px -14px" }}>
                {best.map((o) => (
                  <OpportunityRow key={routeKey(o)} o={o} onOpen={(x) => nav(`/opportunities/${x.id}`, { state: { route: { strategy: x.strategy, pair: x.pair, buy_venue: x.buy_venue, sell_venue: x.sell_venue } } })} />
                ))}
              </div>
            </>
          )}
        </Card>

        <div className="stack">
          <Card title="Venue health">
            {data.venues.length === 0 ? (
              <EmptyState title="No venues active" />
            ) : (
              <ul className="list-plain">
                {data.venues.map((v) => (
                  <li key={v.name} className="row-between">
                    <span>
                      <strong>{v.display_name}</strong> <span className="tag">{v.kind.toUpperCase()}</span>
                      {v.reasons.length ? <div className="tiny muted">{v.reasons.join("; ")}</div> : null}
                    </span>
                    <span className="row small">
                      <HealthDot health={v.health} />
                      <span className="muted mono">{fmtLatency(v.latency_ms)}</span>
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </Card>

          <Card title="Gas">
            {Object.keys(data.gas).length === 0 ? (
              <p className="small muted">No gas oracle data.</p>
            ) : (
              <dl className="kv small">
                {Object.entries(data.gas).map(([chain, g]) => (
                  <div key={chain} style={{ display: "contents" }}>
                    <dt>{chain}</dt>
                    <dd className="mono">
                      {fmtAmount(g.gas_price, 4)} (baseline {fmtAmount(g.baseline, 4)}) · {g.source} · {fmtAge(g.age_ms)} old
                    </dd>
                  </div>
                ))}
              </dl>
            )}
          </Card>
        </div>
      </div>

      <div className="grid grid-2">
        <Card title="Balances">
          <Table
            dense
            caption="Wallet and exchange balances"
            columns={[
              { key: "venue", header: "Venue", render: (b) => <span><strong>{b.venue}</strong> <span className="tag">{b.kind}</span></span> },
              { key: "asset", header: "Asset", render: (b) => b.asset },
              { key: "avail", header: "Available", render: (b) => <span className="mono">{fmtAmount(b.available)}</span>, align: "right" },
              { key: "res", header: "Reserved", render: (b) => <span className="mono">{fmtAmount(b.reserved)}</span>, align: "right" },
              { key: "usd", header: "USD", render: (b) => <span className="mono">{b.usd === null ? "unknown" : fmtMoney(b.usd, { sign: false })}</span>, align: "right" },
            ]}
            rows={data.balances}
            rowKey={(b) => `${b.venue}-${b.asset}`}
            empty={<EmptyState title="No balances" />}
          />
        </Card>
        <Card title="Trades" actions={<Link to="/history" className="btn btn-sm">History</Link>}>
          {active.length > 0 ? (
            <>
              <h3 className="mb-8">Active ({active.length})</h3>
              <Table dense columns={tradeCols} rows={active} rowKey={(t) => t.id} />
              <hr className="divider" />
            </>
          ) : null}
          <h3 className="mb-8">Recent</h3>
          <Table dense columns={tradeCols} rows={data.recent_trades} rowKey={(t) => t.id} empty={<EmptyState title="No trades yet" />} />
        </Card>
      </div>
    </div>
  );
}
