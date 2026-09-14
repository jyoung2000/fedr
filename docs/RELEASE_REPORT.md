# Release report — FEDR 0.1.0

This report states what was built, what was verified, and — explicitly — what was **not** verified.
Nothing below claims to work unless it was exercised in this build environment.

## Build environment constraints (read first)

* No Docker daemon was available where this release was assembled, so `docker compose up` could not be
  executed here. `docker compose config` validated all compose files (base, dev, paper, testnet, live) and
  the Dockerfile was reviewed by hand. **Build and run the images on your machine before relying on them.**
* Outbound access to every exchange, DEX aggregator and RPC endpoint was blocked. Therefore **no connector
  was verified against a live venue**; all are labelled `SUPPORTED — NOT VERIFIED LIVE`. PAPER mode boots and
  reports the unreachable venues honestly (verified). SIMULATION mode (synthetic market) exercised the full
  pipeline end to end (verified).

## Architecture

See `docs/ARCHITECTURE.md`. Single Python service (FastAPI + asyncio engines) serving a React SPA on port
**8935**, Hummingbot Gateway as a private DEX middleware container, SQLite (WAL) in `/data`.

## Repositories inspected

ccxt 4.5.78 (MIT), Hummingbot 2.16.0 (Apache-2.0), Hummingbot Gateway 2.16.0 (Apache-2.0), hftbacktest
2.4.4 (MIT); plus Aave V3 origin, Balancer V2/V3, Morpho Blue, Uniswap v3-core/periphery/swap-router/
universal-router, OpenZeppelin 5.6.1, Flashbots and Jito docs. Details and reuse: `docs/RESEARCH.md`.

## Components reused vs written

| Reused | Written |
|---|---|
| ccxt / ccxt.pro (runtime), Gateway (runtime container), OpenZeppelin (contract), web3/solders/solana-py, FastAPI/SQLAlchemy/structlog/qrcode | everything under `backend/fedr` (~9k lines), `frontend/src`, `contracts/src/FlashLoanArbitrage.sol`, Docker/compose, docs, 84 tests |

## Connectors

* **CEX (22 via ccxt)**: binance, binanceus, coinbase, coinbaseexchange, kraken, krakenfutures, okx, bybit,
  kucoin (+kucoinfutures), gate, bitget, mexc, hyperliquid, htx, cryptocom, bitfinex, bitstamp, woo, bingx,
  phemex, deribit, bitmex. Sandbox availability recorded per venue from ccxt metadata.
* **DEX (via Gateway 2.16)**: Jupiter, Raydium, Meteora, Orca (Solana); Uniswap (Ethereum, Base, Arbitrum,
  Optimism, Polygon, BSC, Avalanche); PancakeSwap (BSC, Base, Arbitrum); 0x (Ethereum, Base, Arbitrum,
  Optimism, Polygon).
* **Chains**: Solana, Ethereum, Base, Arbitrum, Optimism, Polygon, BSC, Avalanche. Testnets: Solana devnet,
  Sepolia (chain-level only in Gateway).

## Strategies

CEX↔CEX and CEX↔DEX **on** by default. DEX↔DEX, Spot↔Perp, Funding-rate, Basis and Flash-loan **off** by
default, each independently switchable. Carry strategies double fees (open + close) and halve worst-case
funding income in the Profit Guard.

## Flash loans

`contracts/src/FlashLoanArbitrage.sol`: Aave V3 `flashLoanSimple` receiver, owner-only, router/token
allowlists, per-leg `minAmountOut`, on-chain `minProfit`, deadline, reentrancy guard, pausable, exact
approvals, no generic call primitive. Foundry tests cover profitable execution, insufficient profit revert,
cannot-repay revert, deadline, non-owner, unlisted router, callback-only-from-pool. **Not compiled or
fork-tested here (no Foundry / RPC in the build environment). Not audited. Never auto-deployed.**
Python engine: size ladder, simulation requirement (`eth_call` + `estimateGas` live; quote-based in
paper), abort-before-broadcast on any drift, private RPC submission when configured.

## Wallets, deposits, withdrawals

Bot wallets (EVM via eth-account, Solana via solders) generated/imported server-side, AES-256-GCM at rest,
encrypted keystore backups (verified round-trip), registration with the local Gateway, deposit address + QR
+ network warnings + minimum recommendation, deposit detection by balance diff, withdrawals with fee quote,
explicit confirmation and allowlist (native assets only; token withdrawals documented as unavailable).
MetaMask/Phantom are funding wallets only.

## Guards

* **Profit Guard**: gross at reference, attributed slippage / price impact / swap fee, both trading fees,
  maker/taker adjustment, gas + priority, bridge/withdrawal/deposit, funding, rebalance / latency /
  partial-fill / failure allowances, safety buffer (+ learned), flash-loan fee, MEV reserve; expected and
  worst case; unknown-cost policy = block (or documented fallback). Hard rule implemented exactly as specified.
* **Gas Guard**: per-trade max, % of gross, min profit after gas, stress multiplier, priority/gas price caps,
  regimes NORMAL/ELEVATED/HIGH/EXTREME from an EWMA baseline; EXTREME pauses on-chain only.
* **Risk Engine**: every limit in §38, 0–100 score, health gating, balances/inventory, gas reserve.
* **Circuit breakers**: 17 reasons incl. daily-loss hard limit, one-leg fill, prediction error, gas spike,
  balance discrepancy; manual reset; persisted across restarts.

## Modes

Paper (default, real data + simulated execution with latency/drift/stress/partial fills/gas), Testnet
(sandboxes + devnet/Sepolia), Shadow (record-only with re-priced hypothetical P&L), Live (env gate +
readiness checklist + phrase; starts in shadow with auto-execute off), Simulation (synthetic market),
Backtest/replay (same pipeline on recorded JSONL or seeded synthetic data; labelled, with disclaimer).

## Learning / experience

Per route key (mode|strategy|buy|sell|pair|size-bucket): EWMA prediction error, slippage bias, fee/gas
variance, fill reliability, latency → bounded extra safety buffer (max 0.25% by default). Deterministic,
auditable, never authorises a trade.

## Security model

`docs/SECURITY.md`. Verified here: encryption round-trips, master-key file mode 0600, redaction of
key-shaped values in logs, API responses contain no secret material (grep over every endpoint), auth
token + CSRF header enforcement, security headers, CSP.

## Docker

`docker compose up -d` → app on `127.0.0.1:8935`, Gateway private. Overlays for dev/paper/testnet/live.
Non-root, `cap_drop ALL`, read-only FS, no-new-privileges. **Compose config validated; images not built here.**

## Environment variables

See `.env.example` (complete list with comments).

## Tests (98, all passing)

Order-book walking; Profit Guard identity, unknown costs, freshness, depth, DEX attribution, worst-case,
ROI, gas regimes, experience buffer, explanations; **acceptance tests 1–5** (headline-profit-but-true-
profit-below-minimum → BLOCK; expected-positive-but-worst-case-negative → BLOCK; flash-loan eligible only
when all conditions pass; flash-loan route unprofitable after refresh → ABORT BEFORE BROADCAST; CEX leg fills
+ DEX leg fails → recovery/hedge/reconcile/record/breaker); gas becomes expensive → BLOCK; quote change →
requote or block; partial fill → recovery; one-leg fill without hedge venue → breaker; both legs fail →
breaker; DEX/RPC failure → failed leg not crash; gas spike blocks on-chain but not CEX; emergency stop
persists across restart; reconciliation discrepancy pauses; restart recovery of trades/P&L/paper balances;
shadow never submits; concurrency limit; slippage/latency/risk limits; circuit breakers; ledger/inventory/
rebalancer/experience; crypto/redaction; API (health, headers, settings guards, live gate, toggles, paper
controls, emergency stop, wallets/backup/deposit, exchanges honesty, backtest, auth + CSRF); offline connector
tests (ccxt error mapping, every registry id exists in ccxt and its sandbox flag matches ccxt's `urls['test']`,
order status/fee parsing, conservative price rounding, Gateway quote parsing / error codes / stale quoteId
fail-closed / unreachable gateway via a mocked HTTP transport).

## UI QA (performed)

React 18 + TypeScript SPA (`frontend/`), built with Vite into `backend/fedr/static` and served by the API on
port **8935** (verified: `GET /` and SPA routes return the app; `/api/*` unaffected). Playwright/Chromium runs
against the live SIMULATION backend:

* `qa/ui_qa.py`: all 7 pages at 1280 px and 400 px — **0 console errors, 0 horizontal overflow, every
  expected label present** (Gross / Expected / Worst case / Required, Emergency Stop, mode badge, Shadow,
  Paper, Inventory, Deposit / Withdraw / External wallets, connector statuses, Trades / Audit, Risk profile /
  Strategies / Flash loans / Live).
* Frontend agent pass (`frontend/qa/report.json`, 54 checks): emergency-stop dialog open/Esc, "Why?" expander
  persisting across 1 Hz snapshots, opportunity detail with expected-vs-worst-case cost table and explanation,
  deposit modal error surfacing, MetaMask/Phantom-missing messages, Add-API-keys modal with the
  READ/TRADE/WITHDRAW-OFF note, history tabs and trade-detail modal, advanced settings round-trip (11 sections),
  flash-loan warning text, 15-item readiness checklist with Activate disabled when not ready, keyboard focus,
  dark mode.
* Issues found and fixed during QA: event-stream indicator read "Live" (confusable with LIVE mode) → "Connected";
  connection state not synced when another component opened the stream first; recent trades empty after a
  restart (now DB-backed); simulated balances satisfied the "funded" readiness item (now never); simulated
  venues indistinguishable from real exchanges on the Exchanges page (now badged SIMULATED); cramped
  opportunity rows; aborted trades showed "pending" instead of "—".
* Not exercisable here: real MetaMask/Phantom connections, real deposits/withdrawals, LIVE activation (env gate
  off by design), cookie login end-to-end in a browser (covered by API tests).
* LIVE mode styling (red top border, "LIVE — REAL MONEY" badge) is implemented and unit-visible in code; it
  could not be screenshotted because live activation is impossible in this environment.

## Known limitations

* No live venue verification (see constraints). Fee tiers, order-book limits, IOC support and sandbox
  behaviour per exchange must be confirmed on a real deployment (paper → testnet → shadow → live).
* Withdrawals: native assets only; ERC-20/SPL token withdrawals not implemented (use the venue or a DEX).
* Flash-loan route encoding needs router/token addresses per DEX venue; Gateway token metadata provides
  token addresses, router addresses must be configured (documented in `contracts/README.md`); Balancer/Morpho
  providers are documented but not implemented.
* Carry strategies (spot/perp, funding, basis) open positions; automatic close-out on convergence is not
  implemented — positions are visible and closable through the venue.
* Bridging is never automatic; the rebalancer only recommends.
* Gateway 2.16 has no MEV/Jito support; private submission applies to the flash-loan path and to any
  private RPC you configure as Gateway `nodeURL`.
* Fees paid in a third asset (BNB/KCS) are estimated from the schedule until reconciliation, and flagged.

## Unverified features (explicit)

Live order placement on every exchange; Gateway swaps on every DEX; Solana devnet DEX availability; Binance
key-restriction endpoint; Hyperliquid wallet-key auth; withdrawal broadcasting; flash-loan contract on a
fork/testnet; Docker image build and health checks at runtime.
