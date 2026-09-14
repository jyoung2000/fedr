/* Field definitions for the advanced settings forms (mirrors backend/fedr/config/schema.py). */
export type FieldType = "int" | "decimal" | "bool" | "text" | "select" | "list" | "dict_decimal" | "json";

export interface FieldDef {
  key: string;
  label: string;
  type: FieldType;
  unit?: string;
  help?: string;
  options?: { value: string; label: string }[];
  min?: number;
  max?: number;
  step?: number;
  /** null allowed (empty input → null) */
  nullable?: boolean;
  readOnly?: boolean;
}

export interface SectionDef {
  key: string;
  title: string;
  description: string;
  fields: FieldDef[];
}

const pct = (key: string, label: string, help?: string, extra: Partial<FieldDef> = {}): FieldDef => ({ key, label, type: "decimal", unit: "%", help, step: 0.01, ...extra });
const usd = (key: string, label: string, help?: string, extra: Partial<FieldDef> = {}): FieldDef => ({ key, label, type: "decimal", unit: "USD", help, step: 0.01, ...extra });
const ms = (key: string, label: string, help?: string): FieldDef => ({ key, label, type: "int", unit: "ms", help, min: 0 });
const int = (key: string, label: string, help?: string, extra: Partial<FieldDef> = {}): FieldDef => ({ key, label, type: "int", help, min: 0, ...extra });
const bool = (key: string, label: string, help?: string): FieldDef => ({ key, label, type: "bool", help });

export const GENERAL: SectionDef = {
  key: "general",
  title: "General",
  description: "Scanner cadence and quote assets. Mode, risk profile, pairs and the bot switch are managed on the main Settings and Trading pages.",
  fields: [
    ms("scan_interval_ms", "Scan interval", "How often every route is re-evaluated"),
    { key: "quote_assets", label: "Quote assets", type: "list", help: "Comma-separated, e.g. USDC, USDT, USD" },
  ],
};

export const TRADING: SectionDef = {
  key: "trading",
  title: "Trading",
  description: "Profit Guard thresholds and execution allowances. Editing any value switches the risk profile to Custom.",
  fields: [
    usd("max_trade_size_usd", "Max trade size"),
    usd("min_trade_size_usd", "Min trade size"),
    usd("min_profit_usd", "Min expected net profit", "Required expected net per trade"),
    usd("min_worst_case_profit_usd", "Min worst-case profit", "Worst case must still clear this"),
    pct("min_roi_pct", "Min net ROI"),
    ms("max_quote_age_ms", "Max quote age"),
    int("max_risk_score", "Max risk score", "0 (safest) – 100", { max: 100 }),
    pct("safety_buffer_pct", "Safety reserve", "% of notional always reserved"),
    pct("latency_allowance_pct", "Latency allowance", "Adverse move allowance between quote and fill"),
    pct("partial_fill_allowance_pct", "Partial-fill allowance"),
    pct("failure_reserve_pct", "Failure reserve", "Expected cost of a failed leg (gas / unwind)"),
    pct("rebalance_allowance_pct", "Rebalancing allowance", "Amortised cost of restoring inventory"),
    bool("stablecoin_equivalence", "Stablecoin equivalence", "Treat USDC/USDT/USD as one quote (with haircut)"),
    pct("stablecoin_haircut_pct", "Stablecoin haircut"),
    { key: "worst_case_slippage_multiplier", label: "Worst-case slippage multiplier", type: "decimal", unit: "×", step: 0.05 },
    { key: "worst_case_fee_multiplier", label: "Worst-case fee multiplier", type: "decimal", unit: "×", step: 0.05 },
    ms("execution_timeout_ms", "Execution timeout"),
    pct("leg_price_tolerance_pct", "Leg price tolerance", "Limit price slack for IOC legs"),
    pct("unknown_fee_fallback_pct", "Unknown fee fallback", "Blank = BLOCK when a fee is unknown (recommended)", { nullable: true }),
  ],
};

export const RISK: SectionDef = {
  key: "risk",
  title: "Risk",
  description: "Hard limits enforced by the Risk Engine and circuit breakers. Editing any value switches the risk profile to Custom.",
  fields: [
    pct("max_capital_usage_pct", "Max capital usage"),
    pct("max_exchange_exposure_pct", "Max exposure per exchange"),
    pct("max_chain_exposure_pct", "Max exposure per chain"),
    pct("max_asset_exposure_pct", "Max exposure per asset"),
    usd("max_daily_loss_usd", "Max daily loss", "HARD limit — trips a breaker"),
    int("max_concurrent_trades", "Max concurrent trades", undefined, { min: 1 }),
    int("max_unhedged_seconds", "Max unhedged seconds"),
    pct("max_slippage_pct", "Max slippage"),
    usd("max_gas_usd", "Max gas per trade"),
    ms("max_quote_age_ms", "Max quote age"),
    int("max_failed_trades_per_hour", "Max failed trades / hour"),
    usd("max_flash_loan_usd", "Max flash loan"),
    usd("max_flash_loan_gas_usd", "Max flash-loan gas"),
    bool("allow_degraded_venues", "Allow degraded venues"),
    pct("max_prediction_error_pct", "Max prediction error", "% of notional; larger trips a breaker"),
    pct("rapid_move_pct", "Rapid move threshold", "% move within a scan window that trips a breaker"),
    usd("emergency_reserve_usd", "Emergency reserve"),
    pct("inventory_reserve_pct", "Inventory reserve"),
    bool("leverage_allowed", "Leverage allowed"),
    bool("auto_bridging_allowed", "Auto-bridging allowed"),
    { key: "asset_allowlist", label: "Asset allowlist", type: "list", help: "Comma-separated symbols" },
  ],
};

export const GAS: SectionDef = {
  key: "gas",
  title: "Gas",
  description: "Gas Guard limits, regime thresholds and per-chain native reserves the engine may never consume.",
  fields: [
    usd("max_gas_per_trade_usd", "Max gas per trade"),
    pct("max_gas_pct_of_gross", "Max gas % of gross"),
    usd("min_profit_after_gas_usd", "Min profit after gas"),
    { key: "stress_multiplier", label: "Stress multiplier", type: "decimal", unit: "×", step: 0.1 },
    { key: "max_priority_fee_gwei", label: "Max priority fee", type: "decimal", unit: "gwei", step: 0.1 },
    { key: "max_gas_price_gwei", label: "Max gas price", type: "decimal", unit: "gwei", step: 1 },
    int("max_priority_fee_lamports", "Max priority fee (Solana)", "lamports"),
    { key: "elevated_multiple", label: "Elevated regime", type: "decimal", unit: "× baseline", step: 0.1 },
    { key: "high_multiple", label: "High regime", type: "decimal", unit: "× baseline", step: 0.1 },
    { key: "extreme_multiple", label: "Extreme regime", type: "decimal", unit: "× baseline", step: 0.1 },
    { key: "elevated_profit_multiplier", label: "Elevated profit multiplier", type: "decimal", unit: "×", step: 0.1, help: "Required profit × this in the ELEVATED regime" },
    { key: "unknown_gas_policy", label: "Unknown gas policy", type: "select", options: [{ value: "block", label: "Block" }, { value: "conservative_fallback", label: "Conservative fallback" }] },
    usd("conservative_gas_fallback_usd", "Conservative gas fallback"),
    { key: "gas_reserve", label: "Gas reserve per chain", type: "dict_decimal", help: "Native units kept untouched on each chain" },
  ],
};

export const SLIPPAGE: SectionDef = {
  key: "slippage",
  title: "Slippage",
  description: "Maximum acceptable slippage overall, per asset, venue, route and trade size.",
  fields: [
    pct("default_max_pct", "Default max slippage"),
    pct("dex_slippage_tolerance_pct", "DEX slippage tolerance", "minAmountOut tolerance submitted with swaps"),
    { key: "per_asset_max_pct", label: "Per-asset max %", type: "dict_decimal" },
    { key: "per_venue_max_pct", label: "Per-venue max %", type: "json", help: 'JSON object, e.g. {"binance": "0.2"}' },
    { key: "per_route_max_pct", label: "Per-route max %", type: "json", help: "JSON object keyed by route" },
    { key: "size_tiers", label: "Size tiers", type: "json", help: 'JSON list of [notional USD, max %] pairs, e.g. [["100","0.5"],["1000","0.3"]]' },
  ],
};

export const FLASH_LOAN_EXTRA: SectionDef = {
  key: "flash_loan",
  title: "Flash loan — additional limits",
  description: "Slippage and MEV parameters for atomic flash-loan routes.",
  fields: [
    int("max_slippage_bps", "Max slippage", undefined, { unit: "bps" }),
    { key: "min_profit_gas_ratio", label: "Min profit / gas ratio", type: "decimal", unit: "×", step: 0.5 },
    pct("mev_reserve_pct", "MEV reserve", "Reserve for tips / sandwich risk"),
    { key: "chains", label: "Chains", type: "list", help: "Comma-separated chain ids with a deployed flash-loan contract" },
  ],
};

export const PAPER: SectionDef = {
  key: "paper",
  title: "Paper / Testnet",
  description: "Simulation realism: latency, partial fills, failures and adverse drift applied to paper execution.",
  fields: [
    { key: "stress_multiplier", label: "Stress multiplier", type: "decimal", unit: "×", step: 0.1, help: "Pessimistic multiplier on slippage & gas" },
    ms("latency_ms", "Simulated latency"),
    ms("latency_jitter_ms", "Latency jitter"),
    { key: "partial_fill_probability", label: "Partial-fill probability", type: "decimal", step: 0.01, min: 0, max: 1 },
    { key: "failure_probability", label: "Failure probability", type: "decimal", step: 0.01, min: 0, max: 1 },
    pct("quote_drift_pct", "Quote drift", "Adverse price drift during simulated latency"),
    { key: "fill_ratio_on_partial", label: "Fill ratio on partial", type: "decimal", step: 0.05, min: 0, max: 1 },
    { key: "starting_balances", label: "Starting balances", type: "json", help: "JSON object: venue → { asset: amount }. Applied on the next paper reset." },
  ],
};

export const REBALANCE: SectionDef = {
  key: "rebalance",
  title: "Rebalancing",
  description: "Inventory rebalance recommendations (never executed automatically).",
  fields: [
    bool("recommendations_enabled", "Recommendations enabled"),
    usd("min_net_benefit_usd", "Min net benefit"),
    pct("imbalance_threshold_pct", "Imbalance threshold"),
    int("lookback_hours", "Lookback", undefined, { unit: "h" }),
  ],
};

export const MEV: SectionDef = {
  key: "mev",
  title: "MEV protection",
  description: "Private transaction submission and tip limits.",
  fields: [
    bool("enabled", "MEV protection"),
    bool("evm_private_submission", "EVM private submission", "Only effective when FEDR_EVM_PRIVATE_RPC is configured"),
    bool("solana_jito", "Solana Jito bundles"),
    usd("max_tip_usd", "Max tip"),
  ],
};

export const SECURITY: SectionDef = {
  key: "security",
  title: "Security",
  description: "Withdrawal controls and session policy. The allowlist itself is managed on the Wallets page.",
  fields: [
    bool("require_allowlist_for_withdrawals", "Require allowlist for withdrawals"),
    int("session_timeout_minutes", "Session timeout", undefined, { unit: "min", min: 1 }),
    { key: "wallet_backup_confirmed", label: "Wallet backup confirmed", type: "bool", readOnly: true, help: "Set through the Wallets › Backup flow" },
  ],
};

export const ADVANCED: SectionDef = {
  key: "advanced",
  title: "Advanced",
  description: "Market data plumbing, opportunity retention and learning.",
  fields: [
    bool("websocket_market_data", "WebSocket market data"),
    ms("rest_poll_interval_ms", "REST poll interval"),
    int("orderbook_depth", "Order book depth", undefined, { unit: "levels", min: 1 }),
    ms("opportunity_ttl_ms", "Opportunity TTL"),
    int("max_opportunities_kept", "Max opportunities kept"),
    int("reconciliation_interval_s", "Reconciliation interval", undefined, { unit: "s" }),
    int("health_check_interval_s", "Health check interval", undefined, { unit: "s" }),
    bool("experience_learning_enabled", "Experience learning"),
    pct("experience_max_extra_buffer_pct", "Max learned extra buffer"),
    { key: "log_level", label: "Log level", type: "select", options: ["DEBUG", "INFO", "WARNING", "ERROR"].map((v) => ({ value: v, label: v })) },
    { key: "connector_timeout_s", label: "Connector timeout", type: "decimal", unit: "s", step: 0.5 },
  ],
};
