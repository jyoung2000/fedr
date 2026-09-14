# Architecture

```
                         WEB UI (React, /)
                               │  REST + SSE (/api)
                               ▼
                        API SERVICE (FastAPI)
                               │
       ┌───────────────────────┼───────────────────────┐
       ▼                       ▼                       ▼
 Opportunity Engine        Risk Engine            Wallet Manager
 (route graph, sizing,   (limits, score,         (EVM/Solana keys,
  executable quotes)      exposure, health)       encrypted, backups)
       └───────────────────────┼───────────────────────┘
                               ▼
                         PROFIT GUARD  ── Gas Guard ── Slippage Guard ── Latency Guard
                               │
                       EXECUTION ENGINE ── Emergency Hedge ── Circuit Breakers
                               │
              ┌────────────────┴────────────────┐
              ▼                                 ▼
          CEX LAYER (ccxt / ccxt.pro)       DEX LAYER (Gateway client)
              │                                 │
        Exchanges (REST + WS)          Gateway container → chains + DEXs
              └────────────────┬────────────────┘
                               ▼
                 ACCOUNTING → EXPERIENCE ENGINE → RECONCILIATION
                               ▼
                         DATABASE (SQLite, /data)

 Optional:  FLASH LOAN ENGINE → FlashLoanArbitrage.sol (Aave V3, atomic) → DEX A → DEX B → repay
```

## Package map (`backend/fedr`)

| Package | Responsibility |
|---|---|
| `core/` | enums, Decimal money helpers, domain dataclasses (`ExecutionQuote`, `CostBreakdown`, `ProfitAssessment`, `Opportunity`, `OrderResult`, `TradeRecord`) |
| `config/` | `EnvSettings` (infrastructure, env vars) and `AppSettings` (user settings document, risk profiles) |
| `security/` | AES-GCM `SecretBox`, master key, phrase hashing, log redaction |
| `db/` | SQLAlchemy models + `Repo` (all persistence) |
| `marketdata/` | `OrderBook` (VWAP walking), `MarketDataHub` (WS with REST fallback, price index), `GasOracle` |
| `connectors/` | `VenueConnector` ABC + health tracker; `cex/` (ccxt), `dex/` (Gateway client + connector, registries), `paper/` (ledger) |
| `engine/` | Profit/Gas/Slippage/Latency guards, Risk Engine, circuit breakers, opportunity engine, strategies, execution (paired executor, paper executor, emergency hedge), reconciliation, accounting, experience, inventory, rebalancer, shadow recorder, readiness, emergency stop, flash-loan engine |
| `wallets/` | bot wallets, keystores, deposits, withdrawals |
| `sim/` | synthetic market + venues (SIMULATION mode only) |
| `backtest/` | replay engine (same pipeline, recorded or synthetic data) |
| `api/` | routes, auth middleware, serializers |
| `app.py` | composition root: builds connectors per mode, background loops (scan, health, gas, balances, reconciliation, housekeeping) |

## Data flow per scan

1. `MarketDataHub` holds the latest order book per (venue, symbol) from ccxt.pro WebSockets (REST polling
   as fallback / when WS is disabled) and derives a USD price index.
2. `OpportunityEngine.candidates()` builds the route graph: for each enabled strategy and configured pair,
   every ordered (buy venue, sell venue) combination the venues support (same-chain only for flash loans).
3. `pick_size()` chooses the trade size from the risk profile, order-book depth within the slippage limit,
   and available inventory (quote on the buy venue, base on the sell venue). Inventory shortfalls are
   reported to the rebalancer.
4. Both legs are priced with **executable** quotes: CEX legs walk the live book (VWAP, levels consumed,
   depth slippage); DEX legs request a router quote for the exact size (Gateway `quote-swap`, `quoteId`).
5. `ProfitGuard.assess()` computes gross at reference prices, attributes execution deviations to
   slippage / price impact / swap fee (no double counting), adds trading fees, gas (from `GasGuard`),
   bridge/withdrawal/deposit fees, funding, rebalance / latency / partial-fill / failure allowances,
   safety buffer (+ learned extra buffer), flash-loan fee and MEV reserve — twice: expected and worst case
   (stressed multipliers). Unknown required costs → block (or documented fallback if configured).
6. Slippage Guard, Latency Guard, circuit breakers and the Risk Engine add their reasons. Decision is
   `SAFE_TO_EXECUTE` only when the reason list is empty. Every decision is explainable in one sentence.
7. Opportunities are ranked by worst-case net, then expected net, then risk — never by gross spread.
   When a prefunded and a flash-loan variant exist for the same route, the one with the stronger
   worst-case economics is chosen and the alternative is shown with the reasoning.

## Execution

`ExecutionEngine.execute()` re-quotes and re-runs the full guard stack immediately before submission
(abort if anything changed), reserves inventory, submits both legs concurrently as IOC limit orders
(DEX: swap with `minAmountOut` / cached `quoteId`), waits with a timeout, releases unused reservations,
and then:

* both filled → accounting (`compute_outcome`: realized gross, fees, gas, net, prediction error,
  slippage/fee/gas variance) → P&L → experience engine → audit;
* imbalance → **recovery**: the `EmergencyHedger` neutralises the exposure on the safest liquid venue
  (health, liquidity, certainty, slippage), trips `ONE_LEG_FILL` if it cannot; failure counters feed
  `REPEATED_ORDER_FAILURE`; large prediction error / slippage / fee variance trip their breakers;
  the daily loss limit is a hard breaker.

Paper and simulation use the same code with `PaperExecutor` replacing venue order placement: latency +
jitter, adverse drift, depth thinned by the stress multiplier, IOC walking with limit price, random partial
fills / rejections, DEX gas charged to the chain's native balance.

## Persistence & recovery

SQLite (WAL) in `/data/database/fedr.db`. On boot the app restores settings, wallets, exchange accounts,
paper balances, active circuit breakers, the emergency-stop flag, and daily P&L / failure counters. A LIVE
mode without a valid activation reverts to PAPER.
