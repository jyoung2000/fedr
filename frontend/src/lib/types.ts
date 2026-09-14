/* Typed shapes for the FEDR API (mirrors backend/fedr/api/serializers.py and routes/*.py).
   Money/percent values arrive as decimal STRINGS to preserve precision. */

export type Mode = "simulation" | "paper" | "testnet" | "live";
export type Decision = "safe_to_execute" | "blocked";
export type Health = "healthy" | "degraded" | "unhealthy" | "blocked" | "unknown";
export type DecimalStr = string;

export interface Breaker {
  reason: string;
  detail: string;
  tripped_at_ms: number;
  scope: string | null;
  auto: boolean;
}

export interface CheckItem {
  key: string;
  label: string;
  ok: boolean;
  detail: string;
  required: boolean;
}
export interface Checklist {
  ready: boolean;
  items: CheckItem[];
}

export interface GasInfo {
  gas_price: DecimalStr;
  priority: DecimalStr;
  native_usd: DecimalStr;
  age_ms: number;
  source: string;
  baseline: DecimalStr;
}

export interface SystemStatus {
  mode: Mode;
  shadow_mode: boolean;
  bot_enabled: boolean;
  auto_execute: boolean;
  live_activated: boolean;
  live_allowed_by_env: boolean;
  emergency_stop: boolean;
  emergency_stop_reason: string | null;
  breakers: Breaker[];
  risk_profile: string;
  advanced_mode: boolean;
  uptime_s: number;
  status_message: string;
  startup: Checklist | null;
  gateway_ok: boolean;
  scan: { count: number; last_ms: number; duration_ms: number; interval_ms: number };
  open_trades: number;
  gas: Record<string, GasInfo>;
  auth_required: boolean;
  version: string;
  market_data_source: string;
  strategies: Record<string, boolean>;
}

export interface Capital {
  total: DecimalStr;
  usable: DecimalStr;
  reserved: DecimalStr;
  emergency_reserve: DecimalStr;
  inventory_reserve: DecimalStr;
  gas_reserve: DecimalStr;
  at_risk: DecimalStr;
}

export interface OpportunitySummary {
  id: string;
  mode: Mode;
  strategy: string;
  pair: string;
  route: string;
  buy_venue: string;
  sell_venue: string;
  size_base: DecimalStr;
  notional_usd: DecimalStr | null;
  gross_pct: DecimalStr | null;
  gross_usd: DecimalStr | null;
  expected_pct: DecimalStr | null;
  expected_usd: DecimalStr | null;
  worst_pct: DecimalStr | null;
  worst_usd: DecimalStr | null;
  required_usd: DecimalStr | null;
  required_worst_usd: DecimalStr | null;
  roi_pct: DecimalStr | null;
  risk_score: number;
  decision: Decision;
  status: "SAFE TO EXECUTE" | "BLOCKED";
  block_reasons: string[];
  explanation: string;
  gas_regime: string | null;
  age_ms: number;
  created_at_ms: number;
  expires_at_ms: number;
  flash_loan: boolean;
  labels: { gross: string; expected: string; worst: string; expected_usd: string; worst_usd: string };
}

export interface QuoteLeg {
  venue: string;
  kind: string;
  symbol: string;
  execution_price: DecimalStr | null;
  reference_price: DecimalStr | null;
  slippage_pct: DecimalStr | null;
  price_impact_pct: DecimalStr | null;
  fee_pct: DecimalStr | null;
  fee_source: string;
  route: string | null;
  fully_fillable: boolean;
  levels: number;
  age_ms: number;
  quote_amount: DecimalStr | null;
  min_received?: DecimalStr | null;
  chain: string | null;
}

export interface GasAssessment {
  chain: string | null;
  regime: string;
  current_gas_cost_usd: DecimalStr;
  expected_gas_cost_usd: DecimalStr;
  stress_gas_cost_usd: DecimalStr;
  gas_pct_of_gross: DecimalStr;
  passed: boolean;
  reasons: string[];
  gas_price_native: DecimalStr | null;
  priority_fee_usd: DecimalStr;
}

export interface OpportunityDetail extends OpportunitySummary {
  expired?: boolean;
  buy: QuoteLeg;
  sell: QuoteLeg;
  costs: Record<string, DecimalStr>;
  worst_case_costs: Record<string, DecimalStr>;
  unknown_costs: string[];
  capital_required: DecimalStr | null;
  required_roi_pct: DecimalStr | null;
  gas: GasAssessment | null;
  risk: { score: number; passed: boolean; reasons: string[]; factors: Record<string, unknown> };
  alternatives: Record<string, unknown>[];
  carry: Record<string, unknown> | null;
  experience_extra_pct: DecimalStr | null;
}

export interface TradeSummary {
  id: string;
  mode: Mode;
  strategy: string;
  pair: string;
  route: string;
  status: string;
  size_base: DecimalStr;
  estimated_gross: DecimalStr | null;
  estimated_net: DecimalStr | null;
  estimated_worst_case: DecimalStr | null;
  actual_gross: DecimalStr | null;
  actual_fees: DecimalStr | null;
  actual_gas: DecimalStr | null;
  actual_net: DecimalStr | null;
  prediction_error: DecimalStr | null;
  explanation: string;
  started_at_ms: number;
  completed_at_ms: number | null;
}

export interface Fill {
  order_id: string;
  venue: string;
  symbol: string;
  side: string;
  amount: DecimalStr;
  price: DecimalStr;
  fee_amount: DecimalStr;
  fee_asset: string;
  ts_ms: number;
  trade_id: string | null;
}

export interface OrderLeg {
  request: { venue: string; symbol: string; side: string; amount: DecimalStr; limit_price: DecimalStr | null; time_in_force: string; client_order_id: string; min_received: DecimalStr | null };
  order_id: string;
  status: string;
  filled: DecimalStr;
  avg_price: DecimalStr | null;
  fills: Fill[];
  fee_quote: DecimalStr;
  gas_cost_usd: DecimalStr;
  submitted_at_ms: number;
  completed_at_ms: number | null;
  tx_hash: string | null;
  error: string | null;
  raw: Record<string, unknown>;
}

export interface TradeDetail extends TradeSummary {
  legs: Record<string, OrderLeg>;
  estimated_costs: Record<string, string>;
  events: { ts_ms?: number; ts?: number; kind?: string; type?: string; detail?: string; message?: string; [k: string]: unknown }[];
}

/* rows from the DB (history) */
export interface TradeRow {
  id: string;
  mode: string;
  strategy: string;
  pair: string;
  route: string;
  status: string;
  size_base: string;
  estimated_gross: string | null;
  estimated_net: string | null;
  estimated_worst_case: string | null;
  actual_gross: string | null;
  actual_fees: string | null;
  actual_gas: string | null;
  actual_net: string | null;
  prediction_error: string | null;
  explanation: string;
  started_at: string | null;
  completed_at: string | null;
}
export interface TradeRowDetail extends TradeRow {
  detail: Record<string, unknown>;
}

export interface Snapshot {
  ts: number;
  mode: Mode;
  emergency_stop: boolean;
  breakers: number;
  opportunities: OpportunitySummary[];
  capital: Capital;
  open_trades: TradeSummary[];
  daily_pnl: DecimalStr;
}

export interface VenueRow {
  name: string;
  display_name: string;
  kind: string;
  connected: boolean;
  health: Health;
  reasons: string[];
  latency_ms: number | null;
}

export interface BalanceRow {
  venue: string;
  asset: string;
  available: DecimalStr;
  reserved: DecimalStr;
  usd: DecimalStr | null;
  kind: string;
}

export interface PnlSummary {
  mode: string;
  today_net: DecimalStr;
  total: { gross: DecimalStr; trading_fees: DecimalStr; gas: DecimalStr; funding: DecimalStr; slippage: DecimalStr; rebalancing: DecimalStr; other: DecimalStr; net: DecimalStr };
  trades: number;
  wins: number;
  days: { day: string; net: string; gross: string; fees: string; gas: string; trades: number }[];
}

export interface DashboardData {
  mode: Mode;
  shadow_mode: boolean;
  bot_enabled: boolean;
  emergency_stop: boolean;
  breakers: Breaker[];
  capital: Capital;
  pnl: PnlSummary;
  best: OpportunitySummary[];
  counts: { executable: number; blocked: number; routes: number };
  venues: VenueRow[];
  balances: BalanceRow[];
  active_trades: TradeSummary[];
  recent_trades: TradeSummary[];
  risk: { daily_pnl: DecimalStr; max_daily_loss: DecimalStr; failed_last_hour: number; max_failed: number; open_trades: number; max_concurrent: number };
  gas: Record<string, GasInfo>;
  market_data: { books: number; source: string; prices: Record<string, unknown> };
  dex_available: boolean;
}

export interface InventoryLine {
  venue: string;
  asset: string;
  available: DecimalStr;
  reserved: DecimalStr;
  pending: DecimalStr;
  target: DecimalStr;
  minimum: DecimalStr;
  maximum: DecimalStr | null;
  usd: DecimalStr | null;
  kind: string;
}

export interface RebalanceRec {
  from: string;
  to: string;
  asset: string;
  amount: DecimalStr;
  cost_usd: DecimalStr;
  benefit_usd: DecimalStr;
  net_usd: DecimalStr;
  reason: string;
}

export interface ExperienceRow {
  key: string;
  samples: number;
  error_pct: number | string;
  slippage_bias_pct: number | string;
  fee_variance_pct: number | string;
  gas_variance_pct: number | string;
  fill_reliability: number | string;
  latency_ms: number | string;
  extra_buffer_pct: number | string;
}

export interface ShadowSummary {
  evaluated: number;
  would_trade: number;
  predicted_pnl: DecimalStr;
  hypothetical_pnl: DecimalStr;
}

export interface ShadowRecord {
  id: string | number;
  ts: string;
  would_trade: boolean;
  strategy: string;
  pair: string;
  route: string;
  size_base: string;
  predicted_profit: string;
  worst_case_profit: string;
  estimated_costs: string;
  hypothetical_profit: string | null;
  reason: string;
}

export interface TradingState {
  mode: Mode;
  bot_enabled: boolean;
  auto_execute: boolean;
  shadow_mode: boolean;
  emergency_stop: boolean;
  active: TradeDetail[];
  recent: TradeSummary[];
  shadow: ShadowSummary;
  rebalance: RebalanceRec[];
  inventory: InventoryLine[];
  paper: { editable: boolean; starting_balances: Record<string, Record<string, string>>; stress_multiplier: string; latency_ms: number };
  experience: ExperienceRow[];
}

export interface Wallet {
  id: string;
  family: "evm" | "solana" | string;
  address: string;
  label: string;
  kind: "bot" | "external";
  provider: string | null;
  backed_up: boolean;
  mode: string;
}

export interface ChainCard {
  chain: string;
  family: string;
  native: string;
  native_balance: DecimalStr;
  gas_reserve: DecimalStr | null;
  gas_reserve_ok: boolean;
  balances: { asset: string; available: DecimalStr; reserved: DecimalStr; usd: DecimalStr | null }[];
  usd_total: DecimalStr;
}

export interface AllowlistEntry {
  chain: string;
  address: string;
  label: string;
}

export interface WalletsData {
  mode: Mode;
  bot_wallets: Wallet[];
  external_wallets: Wallet[];
  chains: ChainCard[];
  capital: Capital;
  emergency_reserve_usd: DecimalStr;
  deposits: { chain: string; asset: string; amount: DecimalStr; ts: number }[];
  allowlist: AllowlistEntry[];
  require_allowlist: boolean;
  simulated: boolean;
}

export interface DepositInfo {
  chain: string;
  asset: string;
  address: string;
  qr_svg: string;
  warning: string;
  min_recommended_usd: DecimalStr;
  simulated: boolean;
  note: string;
}

export interface WithdrawQuote {
  chain: string;
  asset: string;
  amount: DecimalStr;
  destination: string;
  network_fee_native: DecimalStr;
  network_fee_usd: DecimalStr | null;
  estimated_received: DecimalStr;
  available: boolean;
  reason: string | null;
  allowlisted: boolean;
}

export interface ExchangeAccount {
  id: string;
  label: string;
  mode: string;
  enabled: boolean;
  sandbox: boolean;
  status: string;
  permissions: Record<string, boolean | null | undefined>;
  last_error: string | null;
  last_verified_at?: string | null;
}

export interface CexRow {
  id: string;
  display_name: string;
  tier: number;
  sandbox: boolean;
  sandbox_note: string;
  perps: boolean;
  needs_password: boolean;
  auth_style: "api_key" | "wallet_key" | string;
  notes: string;
  verification: string;
  status: string;
  connected: boolean;
  trading_enabled: boolean;
  health: Health;
  health_reasons: string[];
  latency_ms: number | null;
  ws: boolean | null;
  market_data_updates: number;
  markets: number;
  last_error: string | null;
  account: ExchangeAccount | null;
  permissions: Record<string, boolean | null | undefined> | null;
  in_use: boolean;
  simulated?: boolean;
}

export interface DexRow {
  id: string;
  display_name: string;
  chain: string;
  connector: string;
  trading_type: string;
  network: string;
  testnet_network: string | null;
  notes: string;
  verification: string;
  status: string;
  connected: boolean;
  health: Health;
  health_reasons: string[];
  in_use: boolean;
  simulated?: boolean;
  last_error: string | null;
}

export interface ExchangesData {
  cex: CexRow[];
  dex: DexRow[];
  gateway_ok: boolean;
  gateway_enabled: boolean;
  mode: Mode;
  accounts: (ExchangeAccount & { exchange_id: string })[];
}

/* settings document (config/schema.py) — sections are loosely typed records; keys are validated server-side */
export type SettingsSection = Record<string, unknown>;
export interface AppSettings {
  version: number;
  general: { mode: Mode; shadow_mode: boolean; risk_profile: string; advanced_mode: boolean; pairs: string[]; quote_assets: string[]; scan_interval_ms: number; bot_enabled: boolean };
  trading: SettingsSection & { auto_execute: boolean };
  risk: SettingsSection;
  strategies: Record<string, boolean>;
  gas: SettingsSection;
  slippage: SettingsSection;
  flash_loan: SettingsSection & { enabled: boolean; provider: string; max_loan_usd: string; min_net_profit_usd: string; max_gas_usd: string; max_gas_pct_of_gross: string; require_simulation: boolean; require_atomic: boolean; mev_aware: boolean };
  paper: SettingsSection;
  rebalance: SettingsSection;
  mev: SettingsSection;
  security: SettingsSection & { withdrawal_allowlist: AllowlistEntry[]; require_allowlist_for_withdrawals: boolean; wallet_backup_confirmed: boolean; session_timeout_minutes: number };
  advanced: SettingsSection;
  live: { activated: boolean; activated_at: string | null; activated_by: string | null; paper_completed: boolean; shadow_reviewed: boolean };
}

export interface StrategyInfo {
  key: string;
  name: string;
  description: string;
  default_on: boolean;
  risks: string[];
  carry: boolean;
  atomic: boolean;
}

export interface EnvInfo {
  live_trading_allowed: boolean;
  auth_enabled: boolean;
  gateway_enabled: boolean;
  gateway_url: string;
  private_rpc: boolean;
  rpc: Record<string, boolean>;
  flashloan_contracts: Record<string, boolean>;
  telemetry: boolean;
  data_dir: string;
}

export interface SettingsData {
  settings: AppSettings;
  strategies_catalog: StrategyInfo[];
  env: EnvInfo;
}

export interface OpportunityLogRow {
  id: string;
  strategy: string;
  pair: string;
  route: string;
  decision: string;
  gross_pct: string;
  expected_net_usd: string;
  worst_case_usd: string;
  risk_score: number;
  created_at: string;
  explanation: string | null;
  block_reasons: string[];
}

export interface AuditRow {
  id: number;
  ts: string;
  mode: string;
  event_type: string;
  actor: string;
  summary: string;
  payload: Record<string, unknown>;
}

export interface RiskEventRow {
  id: number | string;
  ts: string;
  severity: string;
  kind: string;
  detail: string;
  payload: Record<string, unknown>;
}

export interface BacktestRow {
  id: string;
  ts: string;
  label: string;
  params: Record<string, unknown>;
  results: Record<string, unknown>;
}

export interface BacktestResult {
  id: string;
  label: string;
  params: Record<string, unknown>;
  results: Record<string, unknown>;
  disclaimer?: string;
  [k: string]: unknown;
}
