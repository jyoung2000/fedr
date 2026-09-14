import { useState } from "react";
import { Card } from "../../components/Card";
import { ConfirmDialog } from "../../components/ConfirmDialog";
import { Checkbox } from "../../components/Field";
import { Skeleton } from "../../components/Spinner";
import { StatusPill } from "../../components/StatusPill";
import { useToast } from "../../components/Toast";
import { api, errorMessage } from "../../lib/api";
import { useAppState } from "../../lib/app-state";
import { usePoll } from "../../lib/hooks";
import type { AppSettings, Checklist, EnvInfo } from "../../lib/types";

const PHRASE = "ACTIVATE LIVE TRADING";

export function LiveCard({ live, env, mode, onSaved }: { live: AppSettings["live"]; env: EnvInfo; mode: string; onSaved: (s: AppSettings) => void }) {
  const toast = useToast();
  const { refreshStatus } = useAppState();
  const { data, error, refresh } = usePoll<Checklist>(() => api.get<Checklist>("/api/system/readiness"), 10000);
  const [activate, setActivate] = useState(false);
  const [deactivate, setDeactivate] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const isLive = mode === "live";

  const mark = async (key: "paper_completed" | "shadow_reviewed", v: boolean) => {
    setBusy(key);
    try {
      const r = await api.put<{ settings: AppSettings }>("/api/settings", { patch: { live: { [key]: v } } });
      onSaved(r.settings);
      await refresh();
    } catch (e) {
      toast.error(errorMessage(e));
    } finally {
      setBusy(null);
    }
  };

  const ready = Boolean(data?.ready) && env.live_trading_allowed;
  return (
    <Card id="live" title="Live trading" tone={isLive ? "danger" : "default"} actions={isLive ? <StatusPill tone="danger" size="lg">LIVE — REAL MONEY</StatusPill> : <StatusPill tone={ready ? "success" : "muted"}>{ready ? "READY TO ACTIVATE" : "NOT READY"}</StatusPill>}>
      <div className="stack">
        <p className="small">
          LIVE mode submits real orders with real funds. Activation requires every readiness item below, the environment gate <code>FEDR_LIVE_TRADING_ALLOWED=true</code>, and typing the confirmation phrase. Live starts in shadow mode with auto-execute off.
        </p>
        <div className="row">
          <span className="small muted">Environment gate:</span>
          {env.live_trading_allowed ? <StatusPill tone="success">FEDR_LIVE_TRADING_ALLOWED = true</StatusPill> : <StatusPill tone="danger">FEDR_LIVE_TRADING_ALLOWED not set</StatusPill>}
          {live.activated ? <StatusPill tone="danger">ACTIVATED {live.activated_at ? `at ${live.activated_at.slice(0, 19).replace("T", " ")}` : ""}{live.activated_by ? ` by ${live.activated_by}` : ""}</StatusPill> : null}
        </div>
        <div className="grid grid-2">
          <div>
            <h3 className="mb-8">Readiness checklist</h3>
            {!data ? (error ? <div className="danger-text">{error}</div> : <Skeleton lines={6} />) : (
              <ul className="readiness">
                {data.items.map((i) => (
                  <li key={i.key} className={i.ok ? "ok" : "bad"}>
                    <span className="mark" aria-hidden="true">{i.ok ? "✓" : "✗"}</span>
                    <span>
                      <span className="visually-hidden">{i.ok ? "passed: " : "failed: "}</span>
                      <strong>{i.label}</strong>{i.required ? "" : <span className="tag" style={{ marginLeft: 6 }}>optional</span>}
                      {i.detail ? <div className="small muted">{i.detail}</div> : null}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </div>
          <div className="stack">
            <h3>Sign-off</h3>
            <Checkbox checked={live.paper_completed} disabled={busy === "paper_completed"} onChange={(v) => mark("paper_completed", v)} label="Paper mode completed" help="I have run paper mode long enough and reviewed the results." />
            <Checkbox checked={live.shadow_reviewed} disabled={busy === "shadow_reviewed"} onChange={(v) => mark("shadow_reviewed", v)} label="Shadow mode reviewed" help="I have compared shadow predictions with what the market actually did." />
            <h3>Environment</h3>
            <dl className="kv small">
              <dt>UI authentication</dt><dd>{env.auth_enabled ? "enabled" : <span className="money neg">disabled (set FEDR_AUTH_TOKEN)</span>}</dd>
              <dt>Gateway</dt><dd>{env.gateway_enabled ? env.gateway_url : "disabled"}</dd>
              <dt>Private RPC (MEV)</dt><dd>{env.private_rpc ? "configured" : "not configured"}</dd>
              <dt>RPC configured</dt><dd>{Object.entries(env.rpc).map(([c, ok]) => <span key={c} className={`tag ${ok ? "" : "faint"}`} style={{ marginRight: 4 }}>{c} {ok ? "✓" : "✗"}</span>)}</dd>
              <dt>Data dir</dt><dd className="mono break">{env.data_dir}</dd>
            </dl>
            <div className="row">
              {isLive ? (
                <button type="button" className="btn btn-danger" onClick={() => setDeactivate(true)}>Deactivate live trading</button>
              ) : (
                <button type="button" className="btn btn-danger" disabled={!ready} onClick={() => setActivate(true)} title={ready ? undefined : "Complete the checklist first"}>Activate LIVE trading</button>
              )}
              {!ready && !isLive ? <span className="small muted">Activation is disabled until every required item passes.</span> : null}
            </div>
          </div>
        </div>
      </div>
      <ConfirmDialog
        open={activate}
        onClose={() => setActivate(false)}
        title="Activate LIVE trading"
        tone="danger"
        confirmLabel="Activate"
        typedPhrase={PHRASE}
        onConfirm={async () => {
          await api.post("/api/system/live/activate", { confirmation: PHRASE });
          toast.success("LIVE trading activated — shadow mode on, auto-execute off");
          await Promise.all([refresh(), refreshStatus()]);
          const s = await api.get<{ settings: AppSettings }>("/api/settings");
          onSaved(s.settings);
        }}
      >
        <div className="danger-text">You are about to trade with real money. FEDR starts LIVE in shadow mode with auto-execute off; you must explicitly turn those on afterwards. Nothing about arbitrage is risk-free.</div>
      </ConfirmDialog>
      <ConfirmDialog
        open={deactivate}
        onClose={() => setDeactivate(false)}
        title="Deactivate live trading?"
        tone="danger"
        confirmLabel="Deactivate — back to paper"
        onConfirm={async () => {
          await api.post("/api/system/live/deactivate");
          toast.success("Live trading deactivated — mode is now PAPER");
          await Promise.all([refresh(), refreshStatus()]);
          const s = await api.get<{ settings: AppSettings }>("/api/settings");
          onSaved(s.settings);
        }}
      >
        <p>The bot returns to PAPER mode. Open live positions are not closed automatically — use the emergency stop first if anything is in flight.</p>
      </ConfirmDialog>
    </Card>
  );
}
