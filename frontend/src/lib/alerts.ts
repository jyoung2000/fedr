/* Derives the persistent "danger state" chips from real backend fields only:
   - /api/system/status: mode, shadow_mode, bot_enabled, emergency_stop(+reason), breakers[], gas{}, strategies{}
   - SSE snapshot: mode, emergency_stop, opportunities[].gas_regime
   - /api/system/metrics: venues{name → health}, breakers[], gas{}
   Nothing here invents a field; thresholds mirror backend defaults where noted. */
import { num } from "./format";
import type { Breaker, Snapshot, SystemMetrics, SystemStatus } from "./types";

export type AlertTone = "danger" | "warning" | "info" | "muted";

export interface StateAlert {
  key: string;
  tone: AlertTone;
  label: string;
  detail?: string;
  title?: string;
  /** in-app route */
  to?: string;
  /** same-page anchor */
  href?: string;
  pulse?: boolean;
}

/** Mirrors GasSettings.high_multiple / extreme_multiple defaults (backend/fedr/config/schema.py). */
export const GAS_HIGH_MULTIPLE = 2.5;
export const GAS_EXTREME_MULTIPLE = 4;

export function deriveAlerts(status: SystemStatus | null, snapshot: Snapshot | null, metrics: SystemMetrics | null): StateAlert[] {
  const out: StateAlert[] = [];
  const mode = snapshot?.mode ?? status?.mode ?? null;
  const estop = snapshot?.emergency_stop ?? status?.emergency_stop ?? false;
  const breakers: Breaker[] = status?.breakers ?? metrics?.breakers ?? [];

  if (estop) {
    out.push({
      key: "estop",
      tone: "danger",
      pulse: true,
      label: "EMERGENCY STOP ACTIVE",
      detail: status?.emergency_stop_reason ? `reason: ${status.emergency_stop_reason}` : undefined,
      href: "#estop-banner",
      title: "Scanning and execution are halted — release it from the red banner",
    });
  }

  if (mode === "live") out.push({ key: "mode", tone: "danger", pulse: true, label: "LIVE — REAL FUNDS", to: "/settings#live", title: "Real funds. Deactivate in Settings › Live trading" });
  else if (mode === "testnet") out.push({ key: "mode", tone: "warning", label: "TESTNET", detail: "test assets, real testnet APIs", to: "/trading" });
  else if (mode === "simulation") out.push({ key: "mode", tone: "muted", label: "SIMULATION", detail: "synthetic data", to: "/trading" });
  else if (mode === "paper") out.push({ key: "mode", tone: "muted", label: "PAPER", detail: "simulated balances", to: "/trading" });

  if (status?.shadow_mode) out.push({ key: "shadow", tone: "info", label: "SHADOW", detail: "recording only, nothing is submitted", to: "/trading" });
  if (status && !status.bot_enabled) out.push({ key: "bot", tone: "muted", label: "BOT OFF", to: "/trading" });
  if (status?.strategies?.flash_loan) out.push({ key: "flash", tone: "warning", label: "FLASH LOANS ON", to: "/settings", title: "The flash-loan strategy switch is on" });

  // circuit breakers → named chips for the wallet / position / gas reasons, one counter chip for the rest
  const seen = new Set<string>();
  let other = 0;
  for (const b of breakers) {
    if (seen.has(b.reason)) continue;
    seen.add(b.reason);
    if (b.reason === "balance_discrepancy") out.push({ key: "wallet-mismatch", tone: "danger", label: "WALLET MISMATCH", detail: b.detail, to: "/wallets", title: "balance_discrepancy breaker" });
    else if (b.reason === "position_discrepancy") out.push({ key: "position-discrepancy", tone: "danger", label: "POSITION DISCREPANCY", detail: b.detail, to: "/trading", title: "position_discrepancy breaker" });
    else if (b.reason === "gas_spike") out.push({ key: "gas-spike", tone: "warning", label: "GAS SPIKE — on-chain trading paused", detail: b.detail, to: "/status", title: "gas_spike breaker" });
    else other += 1;
  }
  if (other > 0) out.push({ key: "breakers", tone: "warning", label: `${other} CIRCUIT BREAKER${other > 1 ? "S" : ""} TRIPPED`, href: "#breakers-banner" });

  // gas regime: oracle price vs rolling baseline, plus the regime the engine attached to evaluated routes
  const gas = status?.gas ?? metrics?.gas ?? {};
  const hot: string[] = [];
  let extreme = false;
  for (const [chain, g] of Object.entries(gas)) {
    const price = num(g.gas_price);
    const base = num(g.baseline);
    if (price === null || base === null || base <= 0) continue;
    const ratio = price / base;
    if (ratio >= GAS_HIGH_MULTIPLE) {
      hot.push(`${chain} ${ratio.toFixed(1)}×`);
      if (ratio >= GAS_EXTREME_MULTIPLE) extreme = true;
    }
  }
  const regimes = new Set((snapshot?.opportunities ?? []).map((o) => o.gas_regime).filter((r): r is string => Boolean(r)));
  if (regimes.has("extreme")) extreme = true;
  if (hot.length > 0 || regimes.has("high") || extreme) {
    out.push({
      key: "gas",
      tone: "warning",
      label: extreme ? "GAS EXTREME — on-chain routes blocked" : "HIGH GAS",
      detail: hot.length ? `${hot.join(", ")} baseline` : "gas regime high on evaluated routes",
      to: "/status",
    });
  }

  // connector health from /api/system/metrics venues{name → health}
  if (metrics?.venues) {
    const degraded: string[] = [];
    const unhealthy: string[] = [];
    for (const [name, h] of Object.entries(metrics.venues)) {
      if (h === "degraded") degraded.push(name);
      else if (h === "unhealthy" || h === "blocked") unhealthy.push(name);
    }
    if (unhealthy.length) out.push({ key: "conn-unhealthy", tone: "warning", label: "CONNECTOR UNHEALTHY", detail: unhealthy.join(", "), to: "/exchanges" });
    if (degraded.length) out.push({ key: "conn-degraded", tone: "warning", label: "CONNECTOR DEGRADED", detail: degraded.join(", "), to: "/exchanges" });
  }
  return out;
}
