import { useEffect, useState } from "react";
import { Link, useLocation } from "react-router-dom";
import { Card } from "../components/Card";
import { Skeleton } from "../components/Spinner";
import { ErrorState } from "../components/States";
import { StatusPill } from "../components/StatusPill";
import { useToast } from "../components/Toast";
import { Toggle } from "../components/Toggle";
import { api, errorMessage } from "../lib/api";
import { useAppState } from "../lib/app-state";
import { usePoll } from "../lib/hooks";
import type { AppSettings, SettingsData } from "../lib/types";
import { FlashLoanCard } from "./settings/FlashLoanCard";
import { LiveCard } from "./settings/LiveCard";
import { PairsEditor } from "./settings/PairsEditor";
import { ADVANCED, FLASH_LOAN_EXTRA, GAS, GENERAL, MEV, PAPER, REBALANCE, RISK, SECURITY, SLIPPAGE, TRADING } from "./settings/schema";
import { SectionForm } from "./settings/SectionForm";
import { StrategyToggles } from "./settings/StrategiesCard";

const MODES = [
  { key: "simulation", label: "Simulation", help: "Synthetic market data and simulated balances — safe for exploring the UI." },
  { key: "paper", label: "Paper", help: "Real public market data with simulated balances and execution." },
  { key: "testnet", label: "Testnet", help: "Real testnet exchange APIs, devnet/testnet chains and test assets." },
];

const PROFILES = [
  { key: "conservative", label: "Conservative", help: "Max trade $250 · min expected net $2 · daily loss limit $50 · 1 concurrent trade · max risk score 60." },
  { key: "balanced", label: "Balanced", help: "Max trade $1,000 · min expected net $3 · daily loss limit $150 · 2 concurrent trades · max risk score 70." },
  { key: "custom", label: "Custom", help: "Keeps the numeric limits exactly as you edit them in the Trading, Risk and Gas sections." },
];

export function Settings() {
  const toast = useToast();
  const { refreshStatus } = useAppState();
  const location = useLocation();
  const { data, error, loading, refresh, setData } = usePoll<SettingsData>(() => api.get<SettingsData>("/api/settings"), 20000);
  const [busy, setBusy] = useState<string | null>(null);

  useEffect(() => {
    if (location.hash === "#live" && data) document.getElementById("live")?.scrollIntoView({ behavior: "smooth", block: "start" });
  }, [location.hash, data]);

  const onSaved = (s: AppSettings) => {
    setData((prev) => (prev ? { ...prev, settings: s } : prev));
    void refreshStatus();
  };

  const act = async (key: string, fn: () => Promise<AppSettings | void>, ok: string) => {
    setBusy(key);
    try {
      const s = await fn();
      if (s) onSaved(s);
      else await refresh();
      toast.success(ok);
      await refreshStatus();
    } catch (e) {
      toast.error(errorMessage(e));
    } finally {
      setBusy(null);
    }
  };

  if (loading && !data) return <div className="page"><Card title="Settings"><Skeleton lines={6} /></Card></div>;
  if (!data) return <ErrorState message={error ?? "no data"} onRetry={refresh} />;

  const s = data.settings;
  const advanced = s.general.advanced_mode;
  const showFlash = advanced || s.flash_loan.enabled || s.strategies.flash_loan;

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>Settings</h1>
          <div className="sub">Mode {s.general.mode.toUpperCase()} · risk profile {s.general.risk_profile} · {advanced ? "advanced mode on" : "simple mode"}</div>
        </div>
        {error ? <StatusPill tone="warning">refresh failed: {error}</StatusPill> : null}
      </div>

      <div className="grid grid-2">
        <Card title="Trading mode">
          <div className="segmented" role="group" aria-label="Trading mode">
            {MODES.map((m) => (
              <button key={m.key} type="button" aria-pressed={s.general.mode === m.key} disabled={busy === "mode" || s.general.mode === "live"} onClick={() => s.general.mode !== m.key && act("mode", async () => { await api.post("/api/trading/mode", { mode: m.key }); }, `Mode set to ${m.label}`)}>
                {m.label}
              </button>
            ))}
          </div>
          <ul className="small muted mt-8">
            {MODES.map((m) => <li key={m.key}><strong>{m.label}</strong> — {m.help}</li>)}
          </ul>
          <p className="small mt-8">{s.general.mode === "live" ? <span className="money neg">LIVE is active — deactivate it in the Live trading card to change mode. </span> : null}Live is activated only through the <a href="#live">Live trading</a> card below.</p>
        </Card>

        <Card title="Risk profile">
          <div className="stack-sm" role="radiogroup" aria-label="Risk profile">
            {PROFILES.map((p) => (
              <button key={p.key} type="button" role="radio" aria-checked={s.general.risk_profile === p.key} className="toggle" disabled={busy === "profile"} onClick={() => s.general.risk_profile !== p.key && act("profile", async () => (await api.post<{ settings: AppSettings }>("/api/settings/risk-profile", { profile: p.key })).settings, `Risk profile: ${p.label}`)}>
                <span className={`dot ${s.general.risk_profile === p.key ? "dot-info" : ""}`} aria-hidden="true" style={{ width: 12, height: 12 }} />
                <span className="stack-sm" style={{ gap: 0 }}>
                  <span className="toggle-label">{p.label}</span>
                  <span className="toggle-desc">{p.help}</span>
                </span>
                <span className="toggle-state">{s.general.risk_profile === p.key ? "ACTIVE" : ""}</span>
              </button>
            ))}
          </div>
          <p className="tiny muted mt-8">Selecting Conservative or Balanced overwrites the numeric limits; editing any Trading, Risk or Gas value afterwards switches the profile to Custom.</p>
        </Card>
      </div>

      <Card title="Strategies">
        <StrategyToggles catalog={data.strategies_catalog} strategies={s.strategies} onSaved={onSaved} />
      </Card>

      <div className="grid grid-2">
        <Card title="Pairs">
          <PairsEditor pairs={s.general.pairs} onSaved={onSaved} />
        </Card>
        <Card title="Advanced mode">
          <Toggle checked={advanced} busy={busy === "adv"} label="Advanced mode" description="Show every engine parameter as editable forms (General, Trading, Risk, Gas & Fees, Flash Loans, Paper/Testnet, Security, Advanced)." onChange={(v) => act("adv", async () => (await api.put<{ settings: AppSettings }>("/api/settings", { patch: { general: { advanced_mode: v } } })).settings, v ? "Advanced mode on" : "Advanced mode off")} />
        </Card>
      </div>

      {showFlash && !advanced ? <FlashLoanCard fl={s.flash_loan} strategyOn={s.strategies.flash_loan} contracts={data.env.flashloan_contracts} onSaved={onSaved} /> : null}

      <LiveCard live={s.live} env={data.env} mode={s.general.mode} onSaved={onSaved} />

      {advanced ? (
        <section aria-label="Advanced settings">
          <h2 className="mb-8">Advanced settings</h2>
          <details className="accordion"><summary>General</summary><div className="accordion-body"><SectionForm section={GENERAL} values={s.general as unknown as Record<string, unknown>} onSaved={onSaved} /></div></details>
          <details className="accordion"><summary>Trading</summary><div className="accordion-body"><SectionForm section={TRADING} values={s.trading} onSaved={onSaved} /></div></details>
          <details className="accordion"><summary>Risk</summary><div className="accordion-body"><SectionForm section={RISK} values={s.risk} onSaved={onSaved} /></div></details>
          <details className="accordion"><summary>Strategies</summary><div className="accordion-body"><StrategyToggles catalog={data.strategies_catalog} strategies={s.strategies} onSaved={onSaved} /></div></details>
          <details className="accordion"><summary>Exchanges</summary><div className="accordion-body"><p className="small">API keys, connection tests and permissions are managed on the <Link to="/exchanges">Exchanges page</Link>.</p></div></details>
          <details className="accordion"><summary>Wallets</summary><div className="accordion-body"><p className="small">Bot wallets, backups, deposits, withdrawals and the allowlist are managed on the <Link to="/wallets">Wallets page</Link>.</p></div></details>
          <details className="accordion"><summary>Gas &amp; Fees</summary><div className="accordion-body stack"><SectionForm section={GAS} values={s.gas} onSaved={onSaved} /><hr className="divider" /><SectionForm section={SLIPPAGE} values={s.slippage} onSaved={onSaved} /></div></details>
          <details className="accordion"><summary>Flash Loans</summary><div className="accordion-body stack"><FlashLoanCard fl={s.flash_loan} strategyOn={s.strategies.flash_loan} contracts={data.env.flashloan_contracts} onSaved={onSaved} /><SectionForm section={FLASH_LOAN_EXTRA} values={s.flash_loan} onSaved={onSaved} /></div></details>
          <details className="accordion"><summary>Paper / Testnet</summary><div className="accordion-body"><SectionForm section={PAPER} values={s.paper} onSaved={onSaved} /></div></details>
          <details className="accordion"><summary>Security</summary><div className="accordion-body"><SectionForm section={SECURITY} values={s.security} onSaved={onSaved} /></div></details>
          <details className="accordion"><summary>Advanced</summary><div className="accordion-body stack"><SectionForm section={ADVANCED} values={s.advanced} onSaved={onSaved} /><hr className="divider" /><SectionForm section={REBALANCE} values={s.rebalance} onSaved={onSaved} /><hr className="divider" /><SectionForm section={MEV} values={s.mev} onSaved={onSaved} /></div></details>
        </section>
      ) : null}
    </div>
  );
}
