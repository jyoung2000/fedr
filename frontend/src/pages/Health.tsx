import { Card } from "../components/Card";
import { Money } from "../components/MoneyText";
import { Skeleton } from "../components/Spinner";
import { EmptyState } from "../components/States";
import { HealthDot, StatusPill } from "../components/StatusPill";
import { fetchJson } from "../lib/api";
import { useAppState } from "../lib/app-state";
import { fmtAge, fmtAmount, fmtDuration, fmtInt, fmtMoney, fmtTime, num, titleCase } from "../lib/format";
import { usePoll } from "../lib/hooks";
import type { HealthLive, HealthReady } from "../lib/types";

type Probe<T> = { ok: boolean; status: number; data: T | null };

interface CounterSpec {
  key: string;
  label: string;
  money?: boolean;
}
const PROCESS_GROUPS: { title: string; items: CounterSpec[] }[] = [
  {
    title: "Opportunities",
    items: [
      { key: "opportunities_evaluated", label: "Evaluated" },
      { key: "opportunities_executable", label: "Executable" },
      { key: "opportunities_blocked", label: "Blocked" },
    ],
  },
  {
    title: "Trades",
    items: [
      { key: "trades_started", label: "Started" },
      { key: "trades_filled", label: "Filled" },
      { key: "trades_failed", label: "Failed" },
      { key: "trades_aborted", label: "Aborted" },
      { key: "trades_hedged", label: "Hedged" },
    ],
  },
  {
    title: "Realized this process",
    items: [
      { key: "gross_usd", label: "Gross", money: true },
      { key: "fees_usd", label: "Fees", money: true },
      { key: "gas_usd", label: "Gas", money: true },
      { key: "net_usd", label: "Net", money: true },
      { key: "prediction_error_abs_usd", label: "Prediction error (abs)", money: true },
    ],
  },
  {
    title: "Safety and data feeds",
    items: [
      { key: "breaker_trips", label: "Breaker trips" },
      { key: "market_data_updates", label: "Market data updates" },
      { key: "market_data_rejections", label: "Market data rejections" },
      { key: "connector_errors", label: "Connector errors" },
    ],
  },
];

function probeText<T extends { status: string }>(p: Probe<T> | null): string {
  if (!p) return "checking…";
  if (p.status === 0) return "unreachable (network error)";
  return `${p.data?.status ?? (p.ok ? "ok" : "no body")} — HTTP ${p.status}`;
}

/** /health/ready + /health/live probes and GET /api/system/metrics, rendered as plain text and checklists. */
export function Health() {
  const { metrics, metricsError, refreshMetrics } = useAppState();
  const ready = usePoll<Probe<HealthReady>>(() => fetchJson<HealthReady>("/health/ready"), 10000);
  const live = usePoll<Probe<HealthLive>>(() => fetchJson<HealthLive>("/health/live"), 10000);
  const refreshAll = () => {
    void ready.refresh();
    void live.refresh();
    void refreshMetrics();
  };
  const checks = Object.entries(ready.data?.data?.checks ?? {});
  const isReady = Boolean(ready.data?.ok);
  const proc = metrics?.process ?? {};
  const persisted = metrics?.persisted ?? {};

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>Health</h1>
          <div className="sub">Readiness and liveness probes (unauthenticated, used by Docker) and process counters since start-up. Refreshes every 10 s.</div>
        </div>
        <button type="button" className="btn btn-sm" onClick={refreshAll}>
          Refresh now
        </button>
      </div>

      <div className="grid grid-2">
        <Card title="Readiness — /health/ready" actions={ready.data ? <StatusPill tone={isReady ? "success" : "danger"}>{isReady ? "READY" : "NOT READY"}</StatusPill> : null} footer={probeText(ready.data)}>
          {!ready.data ? (
            <Skeleton />
          ) : checks.length === 0 ? (
            <EmptyState title={ready.data.status === 0 ? "Probe unreachable" : "No checks in the response"}>{ready.data.status === 0 ? "The browser could not reach /health/ready." : `HTTP ${ready.data.status} without a checks object.`}</EmptyState>
          ) : (
            <ul className="readiness" aria-label="Readiness checks" data-testid="readiness-checks">
              {checks.map(([key, ok]) => (
                <li key={key} className={ok ? "ok" : "bad"}>
                  <span className="mark" aria-hidden="true">{ok ? "✓" : "✗"}</span>
                  <span className="status-text">{ok ? "pass" : "fail"}</span>
                  <span>{titleCase(key)}</span>
                </li>
              ))}
            </ul>
          )}
          {ready.data?.data?.mode ? <p className="small muted mt-8">Mode reported by the probe: {ready.data.data.mode.toUpperCase()}</p> : null}
        </Card>

        <Card title="Liveness — /health/live" actions={live.data ? <StatusPill tone={live.data.ok ? "success" : "danger"}>{live.data.ok ? "ALIVE" : "DOWN"}</StatusPill> : null} footer={probeText(live.data)}>
          {!live.data ? <Skeleton /> : <p className="small">{live.data.ok ? "The process answers HTTP requests." : "The process is not answering the liveness probe."}</p>}
          {metrics ? (
            <dl className="kv mt-8">
              <dt>Mode</dt>
              <dd className="strong">{metrics.mode.toUpperCase()}</dd>
              <dt>Uptime</dt>
              <dd>{fmtDuration(metrics.uptime_s)}</dd>
              <dt>Open trades</dt>
              <dd>{metrics.open_trades}</dd>
              <dt>Order books</dt>
              <dd>{metrics.market_data.books}</dd>
            </dl>
          ) : null}
        </Card>
      </div>

      <Card title="Process counters — /api/system/metrics" footer={metricsError ? `last refresh failed: ${metricsError}` : "Counters reset when the process restarts; the persisted block survives restarts."}>
        {!metrics && !metricsError ? (
          <Skeleton />
        ) : !metrics ? (
          <EmptyState title="Metrics unavailable">{metricsError}</EmptyState>
        ) : (
          <div className="stack" data-testid="process-counters">
            {PROCESS_GROUPS.map((g) => (
              <div key={g.title}>
                <h3 className="mb-8">{g.title}</h3>
                <div className="counter-grid">
                  {g.items.map((it) => (
                    <div key={it.key} className="counter">
                      <span className="k">{it.label}</span>
                      {it.money ? <Money value={proc[it.key] as string | number | undefined} className="v" colored={it.key === "net_usd"} /> : <span className="v">{fmtInt(proc[it.key] as number | string | undefined)}</span>}
                    </div>
                  ))}
                </div>
              </div>
            ))}
            <div>
              <h3 className="mb-8">Persisted (this mode, all time)</h3>
              <div className="counter-grid">
                <div className="counter"><span className="k">Trades</span><span className="v">{fmtInt(persisted.trades as number | undefined)}</span></div>
                <div className="counter"><span className="k">Wins</span><span className="v">{fmtInt(persisted.wins as number | undefined)}</span></div>
                {(["gross", "trading_fees", "gas", "funding", "slippage", "net"] as const).map((k) => (
                  <div key={k} className="counter">
                    <span className="k">{titleCase(k)}</span>
                    <Money value={persisted[k] as string | undefined} className="v" colored={k === "net"} />
                  </div>
                ))}
              </div>
            </div>
          </div>
        )}
      </Card>

      <div className="grid grid-2">
        <Card title="Venue health">
          {!metrics ? (
            <Skeleton />
          ) : Object.keys(metrics.venues).length === 0 ? (
            <EmptyState title="No connectors active" />
          ) : (
            <ul className="list-plain" data-testid="venue-health">
              {Object.entries(metrics.venues).map(([name, health]) => {
                const feed = metrics.market_data.venues[name];
                const rejected = metrics.market_data.rejections[name] ?? 0;
                return (
                  <li key={name} className="row-between">
                    <span>
                      <strong>{name}</strong>
                      {feed ? (
                        <div className="tiny muted">
                          {fmtInt(feed.updates)} updates · {fmtInt(feed.invalid)} invalid · {fmtInt(feed.errors)} errors · {rejected} rejected · {feed.source ?? "no source"}
                          {feed.ws === null ? "" : feed.ws ? " · websocket" : " · REST"} · last {feed.last_ms ? fmtTime(feed.last_ms) : "never"}
                        </div>
                      ) : (
                        <div className="tiny muted">no market-data feed stats</div>
                      )}
                    </span>
                    <HealthDot health={health} />
                  </li>
                );
              })}
            </ul>
          )}
        </Card>

        <div className="stack">
          <Card title="Circuit breakers">
            {!metrics ? (
              <Skeleton />
            ) : metrics.breakers.length === 0 ? (
              <p className="small">
                <StatusPill tone="success">NONE TRIPPED</StatusPill> <span className="muted">breakers armed</span>
              </p>
            ) : (
              <ul className="small">
                {metrics.breakers.map((b, i) => (
                  <li key={`${b.reason}-${i}`}>
                    <StatusPill tone="danger">TRIPPED</StatusPill> <span className="strong">{titleCase(b.reason)}</span>
                    {b.scope ? ` (${b.scope})` : ""}: {b.detail} <span className="faint">at {fmtTime(b.tripped_at_ms)}</span>
                  </li>
                ))}
              </ul>
            )}
          </Card>
          <Card title="Gas">
            {!metrics ? (
              <Skeleton />
            ) : Object.keys(metrics.gas).length === 0 ? (
              <p className="small muted">No gas oracle data.</p>
            ) : (
              <dl className="kv small">
                {Object.entries(metrics.gas).map(([chain, g]) => {
                  const price = num(g.gas_price);
                  const base = num(g.baseline);
                  const ratio = price !== null && base !== null && base > 0 ? price / base : null;
                  return (
                    <div key={chain} style={{ display: "contents" }}>
                      <dt>{chain}</dt>
                      <dd className="mono">
                        {fmtAmount(g.gas_price, 4)} (baseline {fmtAmount(g.baseline, 4)}{ratio !== null ? `, ${ratio.toFixed(2)}×` : ""}) · native {fmtMoney(g.native_usd, { sign: false })} · {g.source} · {fmtAge(g.age_ms)} old
                      </dd>
                    </div>
                  );
                })}
              </dl>
            )}
          </Card>
        </div>
      </div>
    </div>
  );
}
