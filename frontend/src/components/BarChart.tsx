import { fmtMoney } from "../lib/format";

interface Bar {
  label: string;
  value: number;
}

/** Inline-SVG bar chart of daily net P&L (positive green, negative red) with a zero baseline. */
export function BarChart({ bars, title }: { bars: Bar[]; title: string }) {
  const W = 720;
  const H = 200;
  const padL = 56;
  const padR = 8;
  const padT = 12;
  const padB = 28;
  const innerW = W - padL - padR;
  const innerH = H - padT - padB;
  const maxAbs = Math.max(1, ...bars.map((b) => Math.abs(b.value)));
  const zeroY = padT + innerH / 2;
  const scale = innerH / 2 / maxAbs;
  const n = Math.max(1, bars.length);
  const slot = innerW / n;
  const bw = Math.max(3, Math.min(28, slot * 0.7));
  const ticks = [maxAbs, maxAbs / 2, 0, -maxAbs / 2, -maxAbs];
  return (
    <div className="chart-wrap">
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={title} style={{ minWidth: 360 }}>
        <title>{title}</title>
        {ticks.map((t) => {
          const y = zeroY - t * scale;
          return (
            <g key={t}>
              <line x1={padL} x2={W - padR} y1={y} y2={y} stroke="var(--border)" strokeWidth={t === 0 ? 1.5 : 1} strokeDasharray={t === 0 ? undefined : "3 3"} />
              <text x={padL - 6} y={y + 4} textAnchor="end" fontSize="10" fill="var(--text-muted)">
                {fmtMoney(t, { sign: false, decimals: 0 })}
              </text>
            </g>
          );
        })}
        {bars.map((b, i) => {
          const x = padL + i * slot + (slot - bw) / 2;
          const h = Math.abs(b.value) * scale;
          const y = b.value >= 0 ? zeroY - h : zeroY;
          const showLabel = n <= 10 || i % Math.ceil(n / 10) === 0;
          return (
            <g key={b.label}>
              <rect x={x} y={y} width={bw} height={Math.max(h, b.value === 0 ? 1 : 0)} rx={2} fill={b.value >= 0 ? "var(--success)" : "var(--danger)"}>
                <title>{`${b.label}: ${fmtMoney(b.value)}`}</title>
              </rect>
              {showLabel ? (
                <text x={x + bw / 2} y={H - 8} textAnchor="middle" fontSize="10" fill="var(--text-muted)">
                  {b.label.slice(5)}
                </text>
              ) : null}
            </g>
          );
        })}
      </svg>
    </div>
  );
}
