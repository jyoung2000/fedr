import { fmtMoney, fmtPct, signOf } from "../lib/format";
import type { OpportunitySummary } from "../lib/types";

/** The three-level profit display: Gross (spread, NOT profit) → Expected net → Worst case, plus Required. */
export function ProfitTriple({ o, size = "sm" }: { o: Pick<OpportunitySummary, "gross_pct" | "gross_usd" | "expected_pct" | "expected_usd" | "worst_pct" | "worst_usd" | "required_usd" | "required_worst_usd">; size?: "sm" | "lg" }) {
  const line = (pct: string | null, usd: string | null) => (
    <>
      <span className={`money ${signOf(pct)}`}>{fmtPct(pct)}</span>
      {usd !== null && usd !== undefined ? <span className="usd">({fmtMoney(usd)})</span> : null}
    </>
  );
  return (
    <dl className={`profit-triple ${size === "lg" ? "lg" : ""}`} aria-label="Profit assessment">
      <dt className="k">Gross spread</dt>
      <dd className="v">{line(o.gross_pct, size === "lg" ? o.gross_usd : null)}</dd>
      <dt className="k emph">Expected net</dt>
      <dd className="v emph">{line(o.expected_pct, o.expected_usd)}</dd>
      <dt className="k">Worst case</dt>
      <dd className="v">{line(o.worst_pct, o.worst_usd)}</dd>
      <dt className="k">Required</dt>
      <dd className="v">
        <span className="money plain">{fmtMoney(o.required_usd)}</span>
        {o.required_worst_usd ? <span className="usd">(worst ≥ {fmtMoney(o.required_worst_usd)})</span> : null}
      </dd>
    </dl>
  );
}
