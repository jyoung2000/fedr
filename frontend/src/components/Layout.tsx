import { useState, type ReactNode } from "react";
import { NavLink, Link } from "react-router-dom";
import { api } from "../lib/api";
import { useAppState } from "../lib/app-state";
import { fmtTime } from "../lib/format";
import { Banner } from "./Banner";
import { ConfirmDialog } from "./ConfirmDialog";
import { IconDashboard, IconExchange, IconHealth, IconHistory, IconOpportunities, IconSettings, IconStop, IconTrading, IconWallet } from "./Icons";
import { StateBar } from "./StateBar";
import { ModeBadge, StatusPill } from "./StatusPill";
import { useToast } from "./Toast";

const NAV = [
  { to: "/", label: "Dashboard", icon: IconDashboard, end: true },
  { to: "/opportunities", label: "Opportunities", icon: IconOpportunities },
  { to: "/trading", label: "Trading", icon: IconTrading },
  { to: "/wallets", label: "Wallets", icon: IconWallet },
  { to: "/exchanges", label: "Exchanges", icon: IconExchange },
  { to: "/history", label: "History", icon: IconHistory },
  { to: "/status", label: "Health", icon: IconHealth },
  { to: "/settings", label: "Settings", icon: IconSettings },
];

export function Layout({ children }: { children: ReactNode }) {
  const { status, statusError, refreshStatus, refreshMetrics, snapshot, sseConnected, alerts } = useAppState();
  const toast = useToast();
  const [stopOpen, setStopOpen] = useState(false);
  const [reason, setReason] = useState("manual");
  const [releasing, setReleasing] = useState(false);
  const [resetting, setResetting] = useState(false);

  const mode = snapshot?.mode ?? status?.mode ?? null;
  const isLive = mode === "live";
  const estop = snapshot?.emergency_stop ?? status?.emergency_stop ?? false;
  const breakers = status?.breakers ?? [];

  const release = async () => {
    setReleasing(true);
    try {
      await api.post("/api/system/emergency-stop/release");
      toast.success("Emergency stop released");
      await refreshStatus();
    } catch (err) {
      toast.error(`Release failed: ${(err as Error).message}`);
    } finally {
      setReleasing(false);
    }
  };

  const resetBreakers = async () => {
    setResetting(true);
    try {
      const r = await api.post<{ reset: number }>("/api/system/breakers/reset", {});
      toast.success(`${r.reset} circuit breaker(s) reset`);
      await Promise.all([refreshStatus(), refreshMetrics()]);
    } catch (err) {
      toast.error(`Reset failed: ${(err as Error).message}`);
    } finally {
      setResetting(false);
    }
  };

  return (
    <div className={`app ${isLive ? "is-live" : ""} ${estop ? "is-estop" : ""}`}>
      <header className="app-header">
        <Link to="/" className="brand" aria-label="FEDR dashboard">
          <span className="brand-mark" aria-hidden="true">F</span>
          <span className="brand-text">FEDR</span>
        </Link>
        <div className="header-badges">
          <ModeBadge mode={mode} />
          {estop ? (
            <StatusPill tone="danger" title="Emergency stop is active — release it from the red banner">
              E-STOP
            </StatusPill>
          ) : null}
          <span className={`health health-${sseConnected ? "success" : "warning"} tiny`} title="Event stream connection (not the trading mode)">
            <span className={`dot dot-${sseConnected ? "success" : "warning"}`} aria-hidden="true" />
            {sseConnected ? "Connected" : "Reconnecting…"}
          </span>
        </div>
        <div className="header-spacer" />
        <button type="button" className="btn btn-estop" onClick={() => setStopOpen(true)} aria-haspopup="dialog" aria-label="Emergency stop">
          <IconStop />
          <span>Emergency stop</span>
        </button>
      </header>

      <StateBar alerts={alerts} />

      <div className="banners">
        {estop ? (
          <Banner
            tone="danger"
            id="estop-banner"
            className="banner-estop"
            actions={
              <button type="button" className="btn" onClick={release} disabled={releasing}>
                {releasing ? "Releasing…" : "Release emergency stop"}
              </button>
            }
          >
            <span className="pulse-dot pulse" aria-hidden="true" />
            <strong>EMERGENCY STOP ACTIVE.</strong> Scanning and execution are halted{status?.emergency_stop_reason ? ` — reason: ${status.emergency_stop_reason}` : ""}. Open orders were cancelled; review{" "}
            <Link to="/trading" className="banner-link">
              positions
            </Link>{" "}
            before releasing. It is reset here with the Release button.
          </Banner>
        ) : null}
        {isLive ? (
          <Banner tone="danger" id="live-banner" className="banner-live">
            <span className="pulse-dot pulse" aria-hidden="true" />
            <strong>LIVE — REAL FUNDS.</strong> Orders submitted from now on use real funds.{status?.shadow_mode ? " Shadow mode is on: nothing is submitted until you turn it off in Trading." : ""}
          </Banner>
        ) : null}
        {breakers.length > 0 ? (
          <Banner
            tone="warning"
            id="breakers-banner"
            actions={
              <button type="button" className="btn" onClick={resetBreakers} disabled={resetting}>
                {resetting ? "Resetting…" : "Reset after investigation"}
              </button>
            }
          >
            <strong>Circuit breaker{breakers.length > 1 ? "s" : ""} tripped — trading paused.</strong>
            <ul className="small">
              {breakers.map((b, i) => (
                <li key={`${b.reason}-${i}`}>
                  <span className="strong">{b.reason.replace(/_/g, " ")}</span>
                  {b.scope ? ` (${b.scope})` : ""}: {b.detail} <span className="faint">at {fmtTime(b.tripped_at_ms)}</span>
                </li>
              ))}
            </ul>
          </Banner>
        ) : null}
        {statusError && !status ? (
          <Banner tone="muted">
            Waiting for the backend… ({statusError})
          </Banner>
        ) : null}
      </div>

      <div className="app-body">
        <aside className="sidebar">
          <nav aria-label="Main">
            {NAV.map((n) => (
              <NavLink key={n.to} to={n.to} end={n.end} className={({ isActive }) => `nav-link ${isActive ? "active" : ""}`}>
                <n.icon />
                <span>{n.label}</span>
              </NavLink>
            ))}
          </nav>
          <div className="sidebar-foot">
            {status ? (
              <>
                <div>{status.status_message}</div>
                <div>v{status.version} · scans {status.scan.count}</div>
              </>
            ) : null}
          </div>
        </aside>
        <main className="main" id="main">
          {children}
        </main>
      </div>

      <nav className="bottom-nav" aria-label="Main (mobile)">
        {NAV.map((n) => (
          <NavLink key={n.to} to={n.to} end={n.end} className={({ isActive }) => (isActive ? "active" : "")}>
            <n.icon />
            <span>{n.label}</span>
          </NavLink>
        ))}
      </nav>

      <ConfirmDialog
        open={stopOpen}
        onClose={() => setStopOpen(false)}
        title="Activate emergency stop?"
        tone="danger"
        confirmLabel="STOP EVERYTHING"
        onConfirm={async () => {
          await api.post("/api/system/emergency-stop", { reason: reason.trim() || "manual" });
          toast.success("Emergency stop activated");
          await refreshStatus();
        }}
        extra={
          <div className="field">
            <label htmlFor="estop-reason">Reason (recorded in the audit log)</label>
            <input id="estop-reason" className="input" value={reason} onChange={(e) => setReason(e.target.value)} data-autofocus />
          </div>
        }
      >
        <p>
          This cancels open orders on every venue, halts the scanner and blocks any new execution until you release it. Existing filled legs are <strong>not</strong> unwound automatically — reconcile them afterwards.
        </p>
      </ConfirmDialog>
    </div>
  );
}
