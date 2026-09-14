import { BarChart } from "../../components/BarChart";
import { Card } from "../../components/Card";
import { Money } from "../../components/MoneyText";
import { Skeleton } from "../../components/Spinner";
import { EmptyState, ErrorState } from "../../components/States";
import { api, qs } from "../../lib/api";
import { fmtMoney, num } from "../../lib/format";
import { usePoll } from "../../lib/hooks";
import type { PnlSummary } from "../../lib/types";

export function PnlTab({ mode }: { mode: string }) {
  const { data, error, loading, refresh } = usePoll<PnlSummary>(() => api.get<PnlSummary>(`/api/history/pnl${qs({ mode })}`), 15000, [mode]);
  if (loading && !data) return <Card><Skeleton /></Card>;
  if (!data) return <ErrorState message={error ?? "no data"} onRetry={refresh} />;
  const t = data.total;
  const lines: [string, string][] = [
    ["Gross", t.gross],
    ["Trading fees", t.trading_fees],
    ["Gas", t.gas],
    ["Funding", t.funding],
    ["Slippage", t.slippage],
    ["Rebalancing", t.rebalancing],
    ["Other", t.other],
  ];
  const winRate = data.trades > 0 ? Math.round((data.wins / data.trades) * 100) : null;
  return (
    <div className="grid grid-main-side">
      <Card title={`Daily net P&L — last ${data.days.length} day(s)`}>
        {data.days.length === 0 ? <EmptyState title="No realized P&L yet">Daily bars appear once trades complete in {data.mode.toUpperCase()} mode.</EmptyState> : <BarChart title={`Daily net P&L in ${data.mode} mode`} bars={data.days.map((d) => ({ label: d.day, value: num(d.net) ?? 0 }))} />}
        {data.days.length > 0 ? (
          <div className="table-wrap mt-12">
            <table className="table dense">
              <thead><tr><th scope="col">Day</th><th scope="col" className="num">Trades</th><th scope="col" className="num">Gross</th><th scope="col" className="num">Fees</th><th scope="col" className="num">Gas</th><th scope="col" className="num">Net</th></tr></thead>
              <tbody>
                {data.days.slice().reverse().map((d) => (
                  <tr key={d.day}><td>{d.day}</td><td className="num">{d.trades}</td><td className="num mono">{fmtMoney(d.gross)}</td><td className="num mono">{fmtMoney(d.fees, { sign: false })}</td><td className="num mono">{fmtMoney(d.gas, { sign: false })}</td><td className="num"><Money value={d.net} /></td></tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}
      </Card>
      <Card title="Realized P&L summary">
        <dl className="kv kv-right">
          {lines.map(([k, v]) => (
            <div key={k} style={{ display: "contents" }}>
              <dt>{k}</dt>
              <dd className="mono">{k === "Gross" ? fmtMoney(v) : fmtMoney(-(num(v) ?? 0) === 0 ? 0 : -Math.abs(num(v) ?? 0))}</dd>
            </div>
          ))}
          <dt className="strong">NET REALIZED P&amp;L</dt>
          <dd><Money value={t.net} className="strong" /></dd>
          <dt>Today</dt>
          <dd><Money value={data.today_net} /></dd>
          <dt>Trades / wins</dt>
          <dd className="mono">{data.trades} / {data.wins}{winRate !== null ? ` (${winRate}%)` : ""}</dd>
        </dl>
        <p className="tiny muted mt-12">Costs are shown as deductions. Net = gross − fees − gas − funding − slippage − rebalancing − other. P&amp;L is never mixed between modes.</p>
      </Card>
    </div>
  );
}
