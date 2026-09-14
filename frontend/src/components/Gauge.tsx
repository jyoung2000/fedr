/** Simple horizontal bar gauge with text values (status never by color alone). */
export function Gauge({ label, value, max, valueText, maxText, invert }: { label: string; value: number; max: number; valueText: string; maxText: string; invert?: boolean }) {
  const ratio = max > 0 ? Math.min(1, Math.max(0, value / max)) : 0;
  const level = ratio >= 0.9 ? "danger" : ratio >= 0.6 ? "warn" : "";
  const pct = Math.round(ratio * 100);
  return (
    <div className={`gauge ${level}`} role="meter" aria-valuemin={0} aria-valuemax={max} aria-valuenow={Math.min(max, Math.max(0, value))} aria-label={`${label}: ${valueText} of ${maxText}`}>
      <div className="gauge-head">
        <span>{label}</span>
        <span className="mono">
          {valueText} <span className="muted">/ {maxText}</span>
        </span>
      </div>
      <div className="bar">
        <span style={{ width: `${pct}%` }} />
      </div>
      {invert ? <span className="tiny muted">{pct}% of the limit consumed</span> : null}
    </div>
  );
}
