import { useState } from "react";
import { fmtAge, fmtMoney, strategyLabel } from "../lib/format";
import type { OpportunitySummary } from "../lib/types";
import { ProfitTriple } from "./ProfitTriple";
import { DecisionPill, StatusPill } from "./StatusPill";

/** Stable identity for a route across scans (opportunity ids change on every scan). */
export function routeKey(o: Pick<OpportunitySummary, "strategy" | "pair" | "buy_venue" | "sell_venue">): string {
  return `${o.strategy}|${o.pair}|${o.buy_venue}|${o.sell_venue}`;
}

/** One opportunity in a list: pair, route, three-level profit, required, risk and decision (+ Why? expander). */
export function OpportunityRow({ o, onOpen }: { o: OpportunitySummary; onOpen: (o: OpportunitySummary) => void }) {
  const [why, setWhy] = useState(false);
  const blocked = o.decision !== "safe_to_execute";
  return (
    <>
      <div
        className="opp-row"
        role="link"
        tabIndex={0}
        onClick={() => onOpen(o)}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            onOpen(o);
          }
        }}
        aria-label={`${o.pair} ${o.route}, ${o.status}`}
      >
        <div className="stack-sm">
          <span className="pair">{o.pair}</span>
          <span className="row small">
            <span className="tag">{strategyLabel(o.strategy)}</span>
            {o.flash_loan ? <span className="tag">flash loan</span> : null}
            {o.gas_regime && o.gas_regime !== "normal" ? <span className="tag">gas {o.gas_regime}</span> : null}
          </span>
        </div>
        <div className="stack-sm">
          <span className="route">
            {o.buy_venue} <span className="faint">→</span> {o.sell_venue}
          </span>
          <span className="tiny muted">
            buy on {o.buy_venue}, sell on {o.sell_venue} · size {o.size_base} {o.pair.split("/")[0]}
            {o.notional_usd ? ` (${fmtMoney(o.notional_usd, { sign: false })})` : ""}
          </span>
        </div>
        <ProfitTriple o={o} />
        <div className="stack-sm small">
          <span>
            <span className="muted">Risk score</span> <span className="strong">{o.risk_score}</span>
            <span className="faint">/100</span>
          </span>
          <span className="muted">quote age {fmtAge(o.age_ms)}</span>
        </div>
        <div className="status-cell">
          <DecisionPill status={o.status} />
          {blocked && o.block_reasons.length > 0 ? (
            <>
              <span className="block-reason">{o.block_reasons[0]}</span>
              <button
                type="button"
                className="link-btn small"
                style={{ minHeight: 28 }}
                aria-expanded={why}
                onClick={(e) => {
                  e.stopPropagation();
                  setWhy((v) => !v);
                }}
              >
                {why ? "Hide reasons" : `Why? (${o.block_reasons.length})`}
              </button>
            </>
          ) : null}
          {!blocked ? <StatusPill tone="success">passes Profit Guard</StatusPill> : null}
        </div>
      </div>
      {why ? (
        <div className="opp-reasons">
          <ul>
            {o.block_reasons.map((r, i) => (
              <li key={i}>{r}</li>
            ))}
          </ul>
          <p className="tiny muted mt-8">{o.explanation}</p>
        </div>
      ) : null}
    </>
  );
}
