import { useMemo, useState } from "react";
import { Card } from "../components/Card";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { Modal } from "../components/Modal";
import { Skeleton } from "../components/Spinner";
import { EmptyState, ErrorState } from "../components/States";
import { connectorLabel, connectorTone, HealthDot, StatusPill } from "../components/StatusPill";
import { useToast } from "../components/Toast";
import { api, errorMessage } from "../lib/api";
import { chainLabel, fmtInt, fmtLatency } from "../lib/format";
import { usePoll } from "../lib/hooks";
import type { CexRow, DexRow, ExchangesData } from "../lib/types";
import { AddKeysModal, PermissionsLine } from "./exchanges/AddKeysModal";

function verificationLabel(v: string): string {
  switch (v) {
    case "supported_not_verified_live":
      return "SUPPORTED — NOT VERIFIED LIVE";
    case "testnet_verified":
      return "TESTNET VERIFIED";
    case "live_verified":
      return "LIVE VERIFIED";
    default:
      return v.toUpperCase();
  }
}

export function Exchanges() {
  const toast = useToast();
  const { data, error, loading, refresh } = usePoll<ExchangesData>(() => api.get<ExchangesData>("/api/exchanges"), 8000);
  const [addFor, setAddFor] = useState<CexRow | null>(null);
  const [removeFor, setRemoveFor] = useState<CexRow | null>(null);
  const [perms, setPerms] = useState<{ venue: string; permissions: Record<string, boolean | null | undefined>; note: string } | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const cex = useMemo(() => (data?.cex ?? []).slice().sort((a, b) => a.tier - b.tier), [data]);
  const dexByChain = useMemo(() => {
    const m = new Map<string, DexRow[]>();
    for (const d of data?.dex ?? []) m.set(d.chain, [...(m.get(d.chain) ?? []), d]);
    return Array.from(m.entries());
  }, [data]);

  const run = async (key: string, fn: () => Promise<unknown>, ok: string) => {
    setBusy(key);
    try {
      await fn();
      toast.success(ok);
      await refresh();
    } catch (e) { toast.error(errorMessage(e)); } finally { setBusy(null); }
  };

  const test = async (row: CexRow) => {
    if (!row.account) return;
    setBusy(`test-${row.id}`);
    try {
      const r = await api.post<{ ok: boolean; permissions?: Record<string, boolean>; markets?: number; error?: string }>(`/api/exchanges/accounts/${row.account.id}/test`);
      if (r.ok) toast.success(`${row.display_name}: connected, ${r.markets ?? 0} markets`);
      else toast.error(`${row.display_name}: ${r.error ?? "connection failed"}`);
      await refresh();
    } catch (e) { toast.error(errorMessage(e)); } finally { setBusy(null); }
  };

  const viewPerms = async (row: CexRow) => {
    try {
      setPerms(await api.get(`/api/exchanges/${row.id}/permissions`));
    } catch (e) { toast.error(errorMessage(e)); }
  };

  if (loading && !data) return <div className="page"><Card title="Centralized exchanges"><Skeleton lines={6} /></Card></div>;
  if (!data) return <ErrorState message={error ?? "no data"} onRetry={refresh} />;

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>Exchanges</h1>
          <div className="sub">Status is reported exactly as the backend sees it. Mode {data.mode.toUpperCase()}.</div>
        </div>
        {error ? <StatusPill tone="warning">refresh failed: {error}</StatusPill> : null}
      </div>

      <Card title="Centralized exchanges" bodyClassName="stack">
        <p className="small muted">Lifecycle: SUPPORTED → CONNECTED → HEALTHY → TRADEABLE → ARBITRAGE-ELIGIBLE. Tier 1 exchanges are designed-for; tier 2 are supported through ccxt.</p>
        {cex.length === 0 ? <EmptyState title="No exchanges in the registry" /> : null}
        {cex.map((row) => (
          <div key={row.id} className="card" style={{ boxShadow: "none" }}>
            <div className="card-body stack-sm">
              <div className="row-between">
                <span className="row">
                  <strong style={{ fontSize: 15 }}>{row.display_name}</strong>
                  <span className="tag">tier {row.tier}</span>
                  {row.perps ? <span className="tag">perps</span> : null}
                  <StatusPill tone={connectorTone(row.status)}>{connectorLabel(row.status)}</StatusPill>
                  <StatusPill tone={row.verification === "live_verified" ? "success" : "muted"}>{verificationLabel(row.verification)}</StatusPill>
                </span>
                <span className="btn-group">
                  <button type="button" className="btn btn-sm btn-primary" onClick={() => setAddFor(row)}>Add API keys</button>
                  {row.account ? (
                    <>
                      <button type="button" className="btn btn-sm" onClick={() => test(row)} disabled={busy === `test-${row.id}`}>{busy === `test-${row.id}` ? "Testing…" : "Test connection"}</button>
                      <button type="button" className="btn btn-sm" onClick={() => run(`en-${row.id}`, () => api.post(`/api/exchanges/accounts/${row.account!.id}/enabled`, { enabled: !row.account!.enabled }), row.account.enabled ? "Account disabled" : "Account enabled")} disabled={busy === `en-${row.id}`}>{row.account.enabled ? "Disable" : "Enable"}</button>
                      <button type="button" className="btn btn-sm" onClick={() => setRemoveFor(row)}>Remove</button>
                    </>
                  ) : null}
                  {row.in_use ? (
                    <>
                      <button type="button" className="btn btn-sm" onClick={() => run(`rc-${row.id}`, () => api.post(`/api/exchanges/${row.id}/reconnect`), `${row.display_name} reconnected`)} disabled={busy === `rc-${row.id}`}>{busy === `rc-${row.id}` ? "Reconnecting…" : "Reconnect"}</button>
                      <button type="button" className="btn btn-sm" onClick={() => viewPerms(row)}>View permissions</button>
                    </>
                  ) : null}
                </span>
              </div>
              <div className="grid grid-4 small" style={{ gap: "6px 16px" }}>
                <div><span className="muted">Connection</span><br />{row.connected ? <StatusPill tone="success">CONNECTED</StatusPill> : <StatusPill tone="muted">NOT CONNECTED</StatusPill>}{row.in_use ? "" : <span className="tiny muted"> not active in this mode</span>}</div>
                <div><span className="muted">Trading</span><br />{row.trading_enabled ? <StatusPill tone="success">ENABLED</StatusPill> : <StatusPill tone="muted">DISABLED</StatusPill>}</div>
                <div><span className="muted">Market data</span><br /><span className="mono">{fmtInt(row.market_data_updates)} updates</span> · WS {row.ws === true ? "yes" : row.ws === false ? "no" : "unknown"} · {row.markets} markets</div>
                <div><span className="muted">Health</span><br /><HealthDot health={row.health} /> <span className="mono muted">{fmtLatency(row.latency_ms)}</span>{row.health_reasons.length ? <div className="tiny muted">{row.health_reasons.join("; ")}</div> : null}</div>
              </div>
              <div className="small muted">
                Sandbox: {row.sandbox ? `available — ${row.sandbox_note}` : `none (${row.sandbox_note || "no sandbox"})`}
                {row.needs_password ? " · requires API passphrase" : ""}
                {row.auth_style === "wallet_key" ? " · authenticates with a wallet private key" : ""}
                {row.notes ? ` · ${row.notes}` : ""}
              </div>
              {row.last_error ? <div className="small money neg">Last error: {row.last_error}</div> : null}
              {row.account ? (
                <div className="banner banner-muted banner-inline small">
                  <div className="banner-body stack-sm">
                    <span><strong>Account:</strong> {row.account.label} · scope {row.account.mode}{row.account.sandbox ? " (sandbox)" : ""} · status {row.account.status} · {row.account.enabled ? "enabled" : "disabled"}{row.account.last_verified_at ? ` · verified ${row.account.last_verified_at.slice(0, 19).replace("T", " ")}` : " · never verified"}</span>
                    <PermissionsLine perms={row.account.permissions ?? {}} />
                    {row.account.last_error ? <span className="money neg">{row.account.last_error}</span> : null}
                  </div>
                </div>
              ) : null}
            </div>
          </div>
        ))}
      </Card>

      <Card title="Decentralized exchanges" actions={<span className={`health health-${data.gateway_ok ? "success" : data.gateway_enabled ? "danger" : "muted"}`}><span className={`dot dot-${data.gateway_ok ? "success" : data.gateway_enabled ? "danger" : "muted"}`} aria-hidden="true" />Gateway {data.gateway_ok ? "reachable" : data.gateway_enabled ? "unreachable" : "disabled"}</span>} bodyClassName="stack">
        <p className="small muted">DEX venues are reached through the Gateway middleware. {data.gateway_ok ? "" : "While Gateway is unavailable, DEX quotes shown in simulation are synthetic and no on-chain swap can be sent."}</p>
        {dexByChain.map(([chain, rows]) => (
          <div key={chain}>
            <h3 className="mb-8">{chainLabel(chain)}</h3>
            <div className="table-wrap">
              <table className="table dense responsive">
                <thead><tr><th scope="col">Venue</th><th scope="col">Type</th><th scope="col">Network</th><th scope="col">Status</th><th scope="col">Health</th><th scope="col">Verification</th><th scope="col">Notes</th></tr></thead>
                <tbody>
                  {rows.map((d) => (
                    <tr key={d.id}>
                      <td data-label="Venue"><strong>{d.display_name}</strong><div className="tiny muted mono">{d.id}</div></td>
                      <td data-label="Type">{d.connector} · {d.trading_type}</td>
                      <td data-label="Network">{d.network}{d.testnet_network ? <span className="tiny muted"> (testnet: {d.testnet_network})</span> : null}</td>
                      <td data-label="Status"><StatusPill tone={connectorTone(d.status)}>{connectorLabel(d.status)}</StatusPill></td>
                      <td data-label="Health"><HealthDot health={d.health} />{d.health_reasons.length ? <div className="tiny muted">{d.health_reasons.join("; ")}</div> : null}{d.last_error ? <div className="tiny money neg">{d.last_error}</div> : null}</td>
                      <td data-label="Verification"><span className="tiny">{verificationLabel(d.verification)}</span></td>
                      <td data-label="Notes"><span className="tiny muted">{d.notes || "—"}</span></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        ))}
      </Card>

      {addFor ? <AddKeysModal spec={addFor} onClose={() => setAddFor(null)} onDone={refresh} /> : null}
      <ConfirmDialog open={removeFor !== null} onClose={() => setRemoveFor(null)} title={`Remove ${removeFor?.display_name} account?`} tone="danger" confirmLabel="Remove" onConfirm={async () => { if (removeFor?.account) { await api.del(`/api/exchanges/accounts/${removeFor.account.id}`); toast.success("Account removed"); await refresh(); } }}>
        <p>The stored API credentials are deleted and the connector is rebuilt without them.</p>
      </ConfirmDialog>
      <Modal open={perms !== null} onClose={() => setPerms(null)} title={`Permissions — ${perms?.venue}`} footer={<button type="button" className="btn" onClick={() => setPerms(null)}>Close</button>}>
        {perms ? (
          <div className="stack">
            {Object.keys(perms.permissions).length ? <PermissionsLine perms={perms.permissions} /> : <p className="small muted">No permission information available for this connector (no authenticated account in this mode).</p>}
            <p className="small muted">{perms.note}</p>
          </div>
        ) : null}
      </Modal>
    </div>
  );
}
