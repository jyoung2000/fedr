import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Card } from "../components/Card";
import { Checkbox, Select } from "../components/Field";
import { OpportunityRow } from "../components/OpportunityRow";
import { Skeleton } from "../components/Spinner";
import { EmptyState, ErrorState } from "../components/States";
import { StatusPill } from "../components/StatusPill";
import { api, qs } from "../lib/api";
import { fmtTime, strategyLabel } from "../lib/format";
import { usePoll, useLocalStorage } from "../lib/hooks";
import type { OpportunitySummary } from "../lib/types";

interface Resp {
  items: OpportunitySummary[];
  scan: { count: number; last_ms: number };
}

export function Opportunities() {
  const nav = useNavigate();
  const [showBlocked, setShowBlocked] = useLocalStorage("fedr.opps.showBlocked", true);
  const [strategy, setStrategy] = useState("");
  const [pair, setPair] = useState("");
  const { data, error, loading, refresh } = usePoll<Resp>(() => api.get<Resp>(`/api/opportunities${qs({ include_blocked: showBlocked, limit: 100 })}`), 2000, [showBlocked]);

  const items = data?.items ?? [];
  const strategies = useMemo(() => Array.from(new Set(items.map((o) => o.strategy))).sort(), [items]);
  const pairs = useMemo(() => Array.from(new Set(items.map((o) => o.pair))).sort(), [items]);
  const filtered = items.filter((o) => (!strategy || o.strategy === strategy) && (!pair || o.pair === pair));
  const executable = items.filter((o) => o.decision === "safe_to_execute").length;

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>Opportunities</h1>
          <div className="sub">
            Ranked by worst-case net (most conservative first). {data ? `Scan #${data.scan.count} at ${fmtTime(data.scan.last_ms)} · ${executable} safe to execute · ${items.length - executable} blocked` : ""}
          </div>
        </div>
        {error ? <StatusPill tone="warning">refresh failed: {error}</StatusPill> : null}
      </div>

      <Card>
        <div className="row" style={{ gap: 16 }}>
          <Checkbox label="Show blocked" checked={showBlocked} onChange={setShowBlocked} />
          <div className="field" style={{ minWidth: 160 }}>
            <label htmlFor="f-strategy">Strategy</label>
            <Select id="f-strategy" value={strategy} onChange={(e) => setStrategy(e.target.value)}>
              <option value="">All strategies</option>
              {strategies.map((s) => (
                <option key={s} value={s}>
                  {strategyLabel(s)}
                </option>
              ))}
            </Select>
          </div>
          <div className="field" style={{ minWidth: 140 }}>
            <label htmlFor="f-pair">Pair</label>
            <Select id="f-pair" value={pair} onChange={(e) => setPair(e.target.value)}>
              <option value="">All pairs</option>
              {pairs.map((p) => (
                <option key={p} value={p}>
                  {p}
                </option>
              ))}
            </Select>
          </div>
          <span className="small muted" style={{ marginLeft: "auto" }}>
            {filtered.length} of {items.length} shown
          </span>
        </div>
      </Card>

      <Card bodyClassName="opp-list">
        {loading && !data ? (
          <Skeleton lines={6} />
        ) : !data ? (
          <ErrorState message={error ?? "no data"} onRetry={refresh} />
        ) : filtered.length === 0 ? (
          <EmptyState title={items.length === 0 ? (showBlocked ? "No opportunities evaluated in the last scan" : "No executable opportunities right now") : "Nothing matches the current filters"}>
            {!showBlocked && items.length === 0 ? "Every route is blocked by the Profit Guard. Enable “Show blocked” to see each route and why it is blocked." : null}
          </EmptyState>
        ) : (
          <div style={{ margin: "-14px -16px" }}>
            {filtered.map((o) => (
              <OpportunityRow key={o.id} o={o} onOpen={(x) => nav(`/opportunities/${x.id}`)} />
            ))}
          </div>
        )}
      </Card>
    </div>
  );
}
