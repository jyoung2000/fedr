/* Formatting helpers. Money always carries a sign and two decimals; percentages carry a sign. */

export function num(v: string | number | null | undefined): number | null {
  if (v === null || v === undefined || v === "") return null;
  const n = typeof v === "number" ? v : Number(v);
  return Number.isFinite(n) ? n : null;
}

export type Sign = "pos" | "neg" | "zero";
export function signOf(v: string | number | null | undefined, eps = 0.000001): Sign {
  const n = num(v);
  if (n === null || Math.abs(n) < eps) return "zero";
  return n > 0 ? "pos" : "neg";
}

export function fmtMoney(v: string | number | null | undefined, opts: { sign?: boolean; decimals?: number; dash?: string } = {}): string {
  const { sign = true, decimals = 2, dash = "—" } = opts;
  const n = num(v);
  if (n === null) return dash;
  const abs = Math.abs(n).toLocaleString("en-US", { minimumFractionDigits: decimals, maximumFractionDigits: decimals });
  if (n < 0 && Number(abs) !== 0) return `-$${abs}`;
  if (sign && n > 0 && Number(abs.replace(/,/g, "")) !== 0) return `+$${abs}`;
  return `$${abs}`;
}

export function fmtPct(v: string | number | null | undefined, opts: { sign?: boolean; decimals?: number; dash?: string } = {}): string {
  const { sign = true, decimals = 2, dash = "—" } = opts;
  const n = num(v);
  if (n === null) return dash;
  const s = n.toFixed(decimals);
  if (n < 0) return `${s}%`;
  return `${sign && Number(s) !== 0 ? "+" : ""}${s}%`;
}

/* base-asset amounts: trim trailing zeros, keep up to `max` decimals */
export function fmtAmount(v: string | number | null | undefined, max = 6): string {
  const n = num(v);
  if (n === null) return "—";
  if (Math.abs(n) >= 1000) return n.toLocaleString("en-US", { maximumFractionDigits: 2 });
  const s = n.toFixed(max);
  return s.replace(/\.?0+$/, "") || "0";
}

export function fmtInt(v: number | string | null | undefined): string {
  const n = num(v);
  return n === null ? "—" : Math.round(n).toLocaleString("en-US");
}

export function fmtTime(ts: number | string | null | undefined): string {
  const d = toDate(ts);
  if (!d) return "—";
  return d.toLocaleTimeString("en-GB", { hour12: false });
}

export function fmtDateTime(ts: number | string | null | undefined): string {
  const d = toDate(ts);
  if (!d) return "—";
  return `${d.toLocaleDateString("en-CA")} ${d.toLocaleTimeString("en-GB", { hour12: false })}`;
}

export function toDate(ts: number | string | null | undefined): Date | null {
  if (ts === null || ts === undefined || ts === "") return null;
  if (typeof ts === "number") return new Date(ts);
  // backend isoformat() has no timezone suffix but is UTC
  const iso = /Z$|[+-]\d\d:\d\d$/.test(ts) ? ts : `${ts}Z`;
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? null : d;
}

export function fmtAge(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) return "—";
  if (ms < 1000) return `${Math.round(ms)}ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`;
  if (ms < 3_600_000) return `${Math.round(ms / 60_000)}m`;
  return `${(ms / 3_600_000).toFixed(1)}h`;
}

export function fmtDuration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return "—";
  const s = Math.max(0, Math.floor(seconds));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  if (h > 24) return `${Math.floor(h / 24)}d ${h % 24}h`;
  if (h > 0) return `${h}h ${m}m`;
  if (m > 0) return `${m}m ${s % 60}s`;
  return `${s}s`;
}

export function fmtLatency(ms: number | null | undefined): string {
  return ms === null || ms === undefined ? "—" : `${Math.round(ms)} ms`;
}

export const STRATEGY_LABELS: Record<string, string> = {
  cex_cex: "CEX ↔ CEX",
  cex_dex: "CEX ↔ DEX",
  dex_dex: "DEX ↔ DEX",
  spot_perp: "Spot ↔ Perp",
  funding: "Funding",
  basis: "Basis",
  flash_loan: "Flash loan",
};
export function strategyLabel(key: string): string {
  return STRATEGY_LABELS[key] ?? titleCase(key);
}

export function modeLabel(mode: string | null | undefined): string {
  return (mode ?? "unknown").toUpperCase();
}

export function titleCase(s: string): string {
  return s.replace(/[_-]+/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

export function shortAddr(addr: string, head = 6, tail = 4): string {
  if (!addr || addr.length <= head + tail + 1) return addr;
  return `${addr.slice(0, head)}…${addr.slice(-tail)}`;
}

export function costLabel(key: string): string {
  const map: Record<string, string> = {
    buy_trading_fee: "Buy trading fee",
    sell_trading_fee: "Sell trading fee",
    maker_taker_adjustment: "Maker/taker adjustment",
    dex_swap_fee: "DEX swap fee",
    lp_fee: "LP fee",
    gas: "Gas",
    priority_fee: "Priority fee",
    slippage: "Slippage",
    price_impact: "Price impact",
    bridge_fee: "Bridge fee",
    withdrawal_fee: "Withdrawal fee",
    deposit_fee: "Deposit fee",
    funding: "Funding",
    rebalance_allowance: "Rebalancing allowance",
    latency_allowance: "Latency allowance",
    partial_fill_allowance: "Partial-fill allowance",
    failure_reserve: "Failure reserve",
    safety_buffer: "Safety reserve",
    flash_loan_fee: "Flash-loan fee",
    mev_reserve: "MEV reserve",
    total: "Total costs",
  };
  return map[key] ?? titleCase(key);
}

export function chainLabel(chain: string): string {
  const map: Record<string, string> = { bsc: "BNB Smart Chain", solana: "Solana", ethereum: "Ethereum", base: "Base", arbitrum: "Arbitrum", optimism: "Optimism", polygon: "Polygon", avalanche: "Avalanche" };
  return map[chain] ?? titleCase(chain);
}
