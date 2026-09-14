import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useLocation, useNavigate, useParams } from "react-router-dom";
import { Banner } from "../components/Banner";
import { Card } from "../components/Card";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { Money, Pct } from "../components/MoneyText";
import { ProfitTriple } from "../components/ProfitTriple";
import { Skeleton } from "../components/Spinner";
import { ErrorState } from "../components/States";
import { DecisionPill, StatusPill } from "../components/StatusPill";
import { useToast } from "../components/Toast";
import { TradeDetailModal } from "../components/TradeDetailModal";
import { api, ApiError, errorMessage } from "../lib/api";
import { useAppState } from "../lib/app-state";
import { costLabel, fmtAge, fmtAmount, fmtMoney, fmtPct, fmtTime, num, strategyLabel, titleCase } from "../lib/format";
import type { OpportunityDetail as Detail, OpportunitySummary, QuoteLeg, TradeDetail } from "../lib/types";

/* The GET endpoint returns the live `opportunity_detail` shape, or — once expired — `{expired: true, ...to_jsonable(Opportunity)}`
   which is the raw dataclass shape. Normalise the latter into the former so the page can render either. */
function normalize(raw: Record<string, unknown>): Detail {
  if (!raw.expired || !raw.profit) return raw as unknown as Detail;
  const p = raw.profit as Record<string, unknown>;
  const leg = (q: Record<string, unknown> | undefined): QuoteLeg => ({
    venue: String(q?.venue ?? "?"),
    kind: String(q?.kind ?? ""),
    symbol: String(q?.symbol ?? ""),
    execution_price: (q?.avg_price as string) ?? null,
    reference_price: (q?.reference_price as string) ?? null,
    slippage_pct: (q?.slippage_pct as string) ?? null,
    price_impact_pct: (q?.price_impact_pct as string) ?? null,
    fee_pct: (q?.fee_pct as string) ?? null,
    fee_source: String(q?.fee_source ?? "unknown"),
    route: (q?.route as string) ?? null,
    fully_fillable: Boolean(q?.fully_fillable),
    levels: Number(q?.levels_consumed ?? 0),
    age_ms: 0,
    quote_amount: (q?.quote_amount as string) ?? null,
    min_received: (q?.min_received as string) ?? null,
    chain: (q?.chain as string) ?? null,
  });
  const buy = leg(raw.buy as Record<string, unknown>);
  const sell = leg(raw.sell as Record<string, unknown>);
  const risk = (raw.risk as Detail["risk"]) ?? { score: 0, passed: false, reasons: [], factors: {} };
  const extra = (raw.extra as Record<string, unknown>) ?? {};
  const decision = String(raw.decision ?? "blocked") as Detail["decision"];
  return {
    id: String(raw.id),
    mode: raw.mode as Detail["mode"],
    strategy: String(raw.strategy),
    pair: `${raw.base}/${raw.quote}`,
    route: `${buy.venue} → ${sell.venue}`,
    buy_venue: buy.venue,
    sell_venue: sell.venue,
    size_base: String(raw.size_base ?? ""),
    notional_usd: (extra.notional_usd as string) ?? null,
    gross_pct: (p.gross_spread_pct as string) ?? null,
    gross_usd: (p.gross_profit as string) ?? null,
    expected_pct: (p.expected_net_pct as string) ?? null,
    expected_usd: (p.expected_net_profit as string) ?? null,
    worst_pct: (p.worst_case_pct as string) ?? null,
    worst_usd: (p.worst_case_profit as string) ?? null,
    required_usd: (p.required_min_profit as string) ?? null,
    required_worst_usd: (p.required_min_worst_case as string) ?? null,
    roi_pct: (p.net_roi_pct as string) ?? null,
    risk_score: risk.score,
    decision,
    status: decision === "safe_to_execute" ? "SAFE TO EXECUTE" : "BLOCKED",
    block_reasons: (raw.block_reasons as string[]) ?? [],
    explanation: String(raw.explanation ?? ""),
    gas_regime: ((raw.gas as Record<string, unknown>)?.regime as string) ?? null,
    age_ms: 0,
    created_at_ms: Number(raw.created_at_ms ?? 0),
    expires_at_ms: Number(raw.expires_at_ms ?? 0),
    flash_loan: Boolean(extra.flash_loan),
    labels: { gross: fmtPct(p.gross_spread_pct as string), expected: fmtPct(p.expected_net_pct as string), worst: fmtPct(p.worst_case_pct as string), expected_usd: fmtMoney(p.expected_net_profit as string), worst_usd: fmtMoney(p.worst_case_profit as string) },
    expired: true,
    buy,
    sell,
    costs: (p.expected_costs as Record<string, string>) ?? {},
    worst_case_costs: (p.worst_case_costs as Record<string, string>) ?? {},
    unknown_costs: ((p.expected_costs as Record<string, unknown>)?.unknown as string[]) ?? [],
    capital_required: (p.capital_required as string) ?? null,
    required_roi_pct: (p.required_min_roi_pct as string) ?? null,
    gas: (raw.gas as Detail["gas"]) ?? null,
    risk,
    alternatives: (raw.alternatives as Record<string, unknown>[]) ?? [],
    carry: (extra.carry as Record<string, unknown>) ?? null,
    experience_extra_pct: (extra.experience_extra_pct as string) ?? null,
  };
}

const COST_ORDER = ["buy_trading_fee", "sell_trading_fee", "maker_taker_adjustment", "dex_swap_fee", "lp_fee", "gas", "priority_fee", "slippage", "price_impact", "bridge_fee", "withdrawal_fee", "deposit_fee", "funding", "rebalance_allowance", "latency_allowance", "partial_fill_allowance", "failure_reserve", "safety_buffer", "flash_loan_fee", "mev_reserve", "total"];

interface RouteHint {
  strategy: string;
  pair: string;
  buy_venue: string;
  sell_venue: string;
}

async function latestForRoute(hint: RouteHint): Promise<OpportunitySummary | undefined> {
  const r = await api.get<{ items: OpportunitySummary[] }>("/api/opportunities?limit=200");
  return r.items.find((o) => o.pair === hint.pair && o.strategy === hint.strategy && o.buy_venue === hint.buy_venue && o.sell_venue === hint.sell_venue);
}

export function OpportunityDetail() {
  const { id = "" } = useParams();
  const nav = useNavigate();
  const location = useLocation();
  const hint = (location.state as { route?: RouteHint } | null)?.route;
  const resolved = useRef(false);
  const toast = useToast();
  const { status } = useAppState();
  const [detail, setDetail] = useState<Detail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [gone, setGone] = useState(false);
  const [confirm, setConfirm] = useState(false);
  const [result, setResult] = useState<TradeDetail | null>(null);
  const [finding, setFinding] = useState(false);
  const stopped = useRef(false);

  const load = useCallback(async () => {
    try {
      const raw = await api.get<Record<string, unknown>>(`/api/opportunities/${id}`);
      const d = normalize(raw);
      setDetail(d);
      setError(null);
      if (d.expired) stopped.current = true;
    } catch (err) {
      if (err instanceof ApiError && err.status === 404) {
        stopped.current = true;
        // Opportunities expire within a few seconds: if we know the route, jump to its latest evaluation once.
        if (hint && !resolved.current) {
          resolved.current = true;
          try {
            const m = await latestForRoute(hint);
            if (m && m.id !== id) {
              nav(`/opportunities/${m.id}`, { replace: true, state: { route: hint } });
              return;
            }
          } catch {
            /* fall through to the expired state */
          }
        }
        setGone(true);
      } else setError(errorMessage(err));
    }
  }, [id, hint, nav]);

  useEffect(() => {
    stopped.current = false;
    setGone(false);
    setDetail(null);
    void load();
    const t = window.setInterval(() => {
      if (!stopped.current) void load();
    }, 2000);
    return () => window.clearInterval(t);
  }, [load]);

  const findLatest = async () => {
    if (!detail) return;
    setFinding(true);
    try {
      const m = await latestForRoute(detail);
      if (m) nav(`/opportunities/${m.id}`, { replace: true, state: { route: { strategy: detail.strategy, pair: detail.pair, buy_venue: detail.buy_venue, sell_venue: detail.sell_venue } } });
      else toast.info("This route was not evaluated in the latest scan.");
    } catch (err) {
      toast.error(errorMessage(err));
    } finally {
      setFinding(false);
    }
  };

  if (!detail && gone) {
    return (
      <div className="page">
        <Link to="/opportunities" className="small">← Opportunities</Link>
        <ErrorState message="This opportunity has expired and is no longer stored. Opportunities are re-evaluated every scan; open the latest one from the list." />
      </div>
    );
  }
  if (!detail) {
    return (
      <div className="page">
        {error ? <ErrorState message={error} onRetry={load} /> : <Card><Skeleton lines={8} /></Card>}
      </div>
    );
  }

  const d = detail;
  const executable = d.decision === "safe_to_execute" && !d.expired && !gone;
  const base = d.pair.split("/")[0];
  const costKeys = COST_ORDER.filter((k) => k in d.costs || k in d.worst_case_costs).concat(Object.keys(d.costs).filter((k) => !COST_ORDER.includes(k)));
  const shadow = status?.shadow_mode;

  return (
    <div className="page">
      <Link to="/opportunities" className="small">← Opportunities</Link>
      {d.expired || gone ? (
        <Banner
          tone="warning"
          inline
          actions={
            <button type="button" className="btn btn-sm" onClick={findLatest} disabled={finding}>
              {finding ? "Searching…" : "Find the latest for this route"}
            </button>
          }
        >
          <strong>Expired snapshot.</strong> This evaluation was created at {fmtTime(d.created_at_ms)} and can no longer be executed; every scan produces a fresh evaluation.
        </Banner>
      ) : null}

      <Card>
        <div className="row-between">
          <div className="stack-sm">
            <div className="row">
              <h1>{d.pair}</h1>
              <span className="tag">{strategyLabel(d.strategy)}</span>
              {d.flash_loan ? <span className="tag">flash loan</span> : null}
            </div>
            <div className="mono">
              ROUTE: {d.buy_venue} <span className="faint">→</span> {d.sell_venue}
            </div>
            <div className="small muted">
              BUY VENUE {d.buy_venue} ({d.buy.kind}{d.buy.chain ? `, ${d.buy.chain}` : ""}) · SELL VENUE {d.sell_venue} ({d.sell.kind}{d.sell.chain ? `, ${d.sell.chain}` : ""})
            </div>
          </div>
          <div className="stack-sm" style={{ alignItems: "flex-end" }}>
            <DecisionPill status={d.status} size="lg" />
            <span className="small muted">
              Risk score <strong>{d.risk_score}</strong>/100 · {d.risk.passed ? "risk checks passed" : "risk checks failed"}
            </span>
          </div>
        </div>
        <p className="mt-12" style={{ fontSize: 15 }}>
          {d.explanation}
        </p>
        {d.unknown_costs.length > 0 ? (
          <div className="warn-text mt-8">
            <strong>Unknown costs:</strong> {d.unknown_costs.join(", ")}. A cost the engine cannot quantify blocks execution rather than being guessed.
          </div>
        ) : null}
        <div className="row mt-12">
          {executable ? (
            <button type="button" className="btn btn-success" onClick={() => setConfirm(true)} disabled={Boolean(shadow) || Boolean(status?.emergency_stop)}>
              EXECUTE
            </button>
          ) : (
            <StatusPill tone="muted" size="lg">
              BLOCKED — see why below
            </StatusPill>
          )}
          {executable && shadow ? <span className="small muted">Shadow mode is on: nothing is submitted. Turn it off in Trading to execute.</span> : null}
          {executable && status?.emergency_stop ? <span className="small muted">Emergency stop is active.</span> : null}
        </div>
      </Card>

      {!executable && d.block_reasons.length > 0 ? (
        <Card title="Blocked — why?" tone="warning">
          <ul>
            {d.block_reasons.map((r, i) => (
              <li key={i}>{r}</li>
            ))}
          </ul>
        </Card>
      ) : null}

      <div className="grid grid-2">
        <Card title="Profit assessment">
          <ProfitTriple o={d} size="lg" />
          <dl className="kv mt-12 small">
            <dt>Net ROI</dt>
            <dd><Pct value={d.roi_pct} /> <span className="muted">(required {fmtPct(d.required_roi_pct)})</span></dd>
            <dt>Capital required</dt>
            <dd className="mono">{fmtMoney(d.capital_required, { sign: false })}</dd>
            {d.experience_extra_pct && num(d.experience_extra_pct) ? (<><dt>Learned extra buffer</dt><dd className="mono">{fmtPct(d.experience_extra_pct)}</dd></>) : null}
          </dl>
          <p className="tiny muted mt-8">Gross is the raw spread before any cost. Expected net subtracts every modelled cost; worst case applies stress multipliers to slippage, fees and gas. Nothing here is guaranteed.</p>
        </Card>

        <Card title="Trade size & legs">
          <dl className="kv small">
            <dt>Size</dt>
            <dd className="mono">{fmtAmount(d.size_base)} {base}{d.notional_usd ? ` ≈ ${fmtMoney(d.notional_usd, { sign: false })}` : ""}</dd>
            <dt>Buy execution price</dt>
            <dd className="mono">{fmtAmount(d.buy.execution_price, 4)} <span className="muted">on {d.buy.venue} (ref {fmtAmount(d.buy.reference_price, 4)})</span></dd>
            <dt>Sell execution price</dt>
            <dd className="mono">{fmtAmount(d.sell.execution_price, 4)} <span className="muted">on {d.sell.venue} (ref {fmtAmount(d.sell.reference_price, 4)})</span></dd>
            <dt>Gross spread</dt>
            <dd><Pct value={d.gross_pct} /> <span className="muted">({fmtMoney(d.gross_usd)})</span></dd>
            <dt>Quote ages</dt>
            <dd className="mono">buy {fmtAge(d.buy.age_ms)} · sell {fmtAge(d.sell.age_ms)}{d.expired ? " (at evaluation)" : ""}</dd>
          </dl>
          <div className="grid grid-2 mt-12">
            {[d.buy, d.sell].map((q, i) => (
              <div key={i} className="stack-sm small">
                <strong>{i === 0 ? "Buy leg" : "Sell leg"} — {q.venue}</strong>
                <dl className="kv small">
                  <dt>Fee</dt><dd>{q.fee_pct === null ? <span className="money neg">unknown</span> : `${fmtPct(q.fee_pct, { sign: false, decimals: 3 })}`} <span className="muted">({q.fee_source})</span></dd>
                  <dt>Slippage</dt><dd className="mono">{fmtPct(q.slippage_pct, { sign: false, decimals: 4 })}</dd>
                  <dt>Price impact</dt><dd className="mono">{fmtPct(q.price_impact_pct, { sign: false, decimals: 4 })}</dd>
                  <dt>Fillable</dt><dd>{q.fully_fillable ? "fully" : <span className="money neg">partially</span>}{q.levels ? ` · ${q.levels} level(s)` : ""}</dd>
                  {q.min_received ? (<><dt>Min received</dt><dd className="mono">{fmtAmount(q.min_received, 4)}</dd></>) : null}
                  {q.route ? (<><dt>Route</dt><dd className="muted">{q.route}</dd></>) : null}
                </dl>
              </div>
            ))}
          </div>
        </Card>
      </div>

      <Card title="Cost breakdown">
        <div className="table-wrap">
          <table className="table dense">
            <thead>
              <tr>
                <th scope="col">Cost</th>
                <th scope="col" className="num">Expected</th>
                <th scope="col" className="num">Worst case</th>
              </tr>
            </thead>
            <tbody>
              {costKeys.map((k) => (
                <tr key={k} className={k === "total" ? "strong" : undefined}>
                  <td>{costLabel(k)}</td>
                  <td className="num mono">{fmtMoney(d.costs[k], { sign: false, decimals: 4 })}</td>
                  <td className="num mono">{fmtMoney(d.worst_case_costs[k], { sign: false, decimals: 4 })}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>

      <div className="grid grid-2">
        <Card title="Gas">
          {d.gas ? (
            <dl className="kv small">
              <dt>Chain</dt><dd>{d.gas.chain ?? "—"}</dd>
              <dt>Regime</dt><dd><StatusPill tone={d.gas.regime === "normal" ? "success" : d.gas.regime === "elevated" ? "warning" : "danger"}>{d.gas.regime.toUpperCase()}</StatusPill></dd>
              <dt>Current cost</dt><dd className="mono">{fmtMoney(d.gas.current_gas_cost_usd, { sign: false, decimals: 4 })}</dd>
              <dt>Expected / stress</dt><dd className="mono">{fmtMoney(d.gas.expected_gas_cost_usd, { sign: false, decimals: 4 })} / {fmtMoney(d.gas.stress_gas_cost_usd, { sign: false, decimals: 4 })}</dd>
              <dt>Priority fee</dt><dd className="mono">{fmtMoney(d.gas.priority_fee_usd, { sign: false, decimals: 4 })}</dd>
              <dt>Gas % of gross</dt><dd className="mono">{fmtPct(d.gas.gas_pct_of_gross, { sign: false })}</dd>
              <dt>Gas Guard</dt><dd>{d.gas.passed ? "passed" : <span className="money neg">failed: {d.gas.reasons.join("; ")}</span>}</dd>
            </dl>
          ) : (
            <p className="small muted">No on-chain leg — gas does not apply.</p>
          )}
        </Card>
        <Card title="Risk">
          <dl className="kv small">
            <dt>Score</dt><dd><strong>{d.risk.score}</strong>/100 · {d.risk.passed ? "passed" : <span className="money neg">failed</span>}</dd>
            {Object.entries(d.risk.factors).map(([k, v]) => (
              <div key={k} style={{ display: "contents" }}>
                <dt>{titleCase(k.replace("health:", "health "))}</dt>
                <dd className="mono">{typeof v === "string" && /^-?\d/.test(v) ? Number(v).toFixed(4).replace(/\.?0+$/, "") : String(v)}</dd>
              </div>
            ))}
          </dl>
          {d.risk.reasons.length ? (
            <ul className="small mt-8">
              {d.risk.reasons.map((r, i) => (
                <li key={i}>{r}</li>
              ))}
            </ul>
          ) : null}
        </Card>
      </div>

      {d.carry ? (
        <Card title="Carry position">
          <pre className="payload">{JSON.stringify(d.carry, null, 2)}</pre>
        </Card>
      ) : null}

      <Card title="Alternatives (prefunded vs flash loan)">
        {d.alternatives.length === 0 ? (
          <p className="small muted">No alternative funding path was evaluated for this route.</p>
        ) : (
          <div className="grid grid-2">
            {d.alternatives.map((a, i) => (
              <div key={i} className="card" style={{ boxShadow: "none" }}>
                <div className="card-body stack-sm small">
                  <strong>{String(a.kind ?? a.name ?? a.type ?? `Alternative ${i + 1}`)}</strong>
                  {Object.entries(a)
                    .filter(([k]) => !["kind", "name", "type"].includes(k))
                    .map(([k, v]) => (
                      <div key={k}>
                        <span className="muted">{titleCase(k)}:</span> {typeof v === "object" ? JSON.stringify(v) : String(v)}
                      </div>
                    ))}
                </div>
              </div>
            ))}
          </div>
        )}
      </Card>

      <ConfirmDialog
        open={confirm}
        onClose={() => setConfirm(false)}
        title={`Execute ${d.pair} ${d.buy_venue} → ${d.sell_venue}?`}
        tone="success"
        confirmLabel="Execute now"
        onConfirm={async () => {
          const tr = await api.post<TradeDetail>(`/api/opportunities/${d.id}/execute`);
          setResult(tr);
          toast.success(`Trade ${tr.status}: ${tr.explanation || tr.id}`);
        }}
      >
        <p>
          Size {fmtAmount(d.size_base)} {base}{d.notional_usd ? ` (≈ ${fmtMoney(d.notional_usd, { sign: false })})` : ""}. Expected net <strong>{fmtMoney(d.expected_usd)}</strong>, worst case <strong>{fmtMoney(d.worst_usd)}</strong>, required {fmtMoney(d.required_usd)}.
        </p>
        <p className="small muted">The engine re-checks the quotes and the Profit Guard immediately before submitting. Mode: {(status?.mode ?? d.mode).toUpperCase()}.</p>
      </ConfirmDialog>
      <TradeDetailModal trade={result} onClose={() => setResult(null)} />
    </div>
  );
}
