import { useState, type FormEvent } from "react";
import { Card } from "../../components/Card";
import { Field, TextInput } from "../../components/Field";
import { Skeleton } from "../../components/Spinner";
import { EmptyState, ErrorState } from "../../components/States";
import { api, errorMessage } from "../../lib/api";
import { fmtDateTime } from "../../lib/format";
import { usePoll } from "../../lib/hooks";
import type { BacktestResult, BacktestRow } from "../../lib/types";

function ResultsView({ results }: { results: Record<string, unknown> }) {
  const entries = Object.entries(results);
  return (
    <dl className="kv small">
      {entries.map(([k, v]) => (
        <div key={k} style={{ display: "contents" }}>
          <dt>{k.replace(/_/g, " ")}</dt>
          <dd className="mono break">{typeof v === "object" && v !== null ? JSON.stringify(v) : String(v)}</dd>
        </div>
      ))}
    </dl>
  );
}

export function BacktestsTab() {
  const { data, error, loading, refresh } = usePoll<{ items: BacktestRow[] }>(() => api.get("/api/history/backtests"), 0);
  const [ticks, setTicks] = useState("300");
  const [seed, setSeed] = useState("7");
  const [label, setLabel] = useState("backtest");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [result, setResult] = useState<BacktestResult | null>(null);

  const run = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true); setErr(null); setResult(null);
    try {
      const r = await api.post<BacktestResult>("/api/backtest/run", { ticks: Number(ticks), seed: Number(seed), label: label.trim() || "backtest" });
      setResult(r);
      await refresh();
    } catch (e2) { setErr(errorMessage(e2)); } finally { setBusy(false); }
  };

  return (
    <div className="grid grid-main-side">
      <Card title="Backtest runs">
        {loading && !data ? <Skeleton /> : !data ? <ErrorState message={error ?? "no data"} onRetry={refresh} /> : data.items.length === 0 ? <EmptyState title="No backtests yet" /> : (
          <div className="stack">
            {data.items.map((b) => (
              <details key={b.id} className="accordion">
                <summary><span>{b.label} <span className="muted small">· {fmtDateTime(b.ts)} · {JSON.stringify(b.params)}</span></span></summary>
                <div className="accordion-body"><ResultsView results={b.results} /></div>
              </details>
            ))}
          </div>
        )}
      </Card>
      <Card title="Run a backtest">
        <form onSubmit={run} className="stack">
          <p className="small muted">Replays synthetic (seeded) market data through the same strategy, Profit Guard and execution simulation. Results are indicative only.</p>
          <Field label="Ticks" help="1 – 5000">{(id) => <TextInput id={id} type="number" min={1} max={5000} value={ticks} onChange={(e) => setTicks(e.target.value)} required />}</Field>
          <Field label="Seed">{(id) => <TextInput id={id} type="number" value={seed} onChange={(e) => setSeed(e.target.value)} required />}</Field>
          <Field label="Label">{(id) => <TextInput id={id} value={label} onChange={(e) => setLabel(e.target.value)} />}</Field>
          {err ? <div className="danger-text" role="alert">{err}</div> : null}
          <button type="submit" className="btn btn-primary" disabled={busy}>{busy ? "Running…" : "Run backtest"}</button>
        </form>
        {result ? (
          <div className="mt-16 stack">
            <h3>Result: {result.label}</h3>
            {result.disclaimer ? <div className="warn-text">{String(result.disclaimer)}</div> : null}
            <ResultsView results={result.results ?? {}} />
            {Object.entries(result).filter(([k]) => !["id", "label", "params", "results", "disclaimer"].includes(k)).length ? <pre className="payload">{JSON.stringify(Object.fromEntries(Object.entries(result).filter(([k]) => !["id", "label", "params", "results", "disclaimer"].includes(k))), null, 2)}</pre> : null}
          </div>
        ) : null}
      </Card>
    </div>
  );
}
