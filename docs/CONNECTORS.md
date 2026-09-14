# Connector matrix

## Status language (mandatory)

Every connector carries exactly one of these statuses. "supported" alone is never used.

| Status | Meaning |
|---|---|
| **SUPPORTED + TESTED** | code path exists and is exercised by the offline test suite (ccxt market/capability metadata, synthetic venues, unit tests); *not* exercised against the venue's API |
| **SUPPORTED + SANDBOX VERIFIED** | market data, balances and an order create/cancel round-trip were run against the venue's sandbox/testnet (`tests/integration/test_external_environment.py::test_cex_sandbox_balance_and_order_round_trip`) |
| **SUPPORTED + LIVE VERIFIED** | same round-trip on a production account with a tiny size, plus a reconciled paper→live comparison |
| **UNVERIFIED** | code exists but no test exercises it |
| **BLOCKED** | verification impossible in the environment that produced this document (no egress / no keys) |

Progressive runtime state (shown in the UI) is separate: SUPPORTED → CONNECTED → HEALTHY → TRADEABLE →
ARBITRAGE-ELIGIBLE.

> **State of this build (2026-09-14).** The verification environment had **no egress to any exchange,
> DEX aggregator, Gateway or RPC endpoint**. Therefore every connector below is
> **SUPPORTED + TESTED (offline)** and **BLOCKED** for sandbox/live verification *here*. Nothing in this
> table is SANDBOX VERIFIED or LIVE VERIFIED. Run the integration tests with the documented environment
> variables on your deployment and update the "Result" column from the test output - never by hand.

## CEX (via ccxt 4.5.78)

| Exchange | ccxt id | Sandbox | Perps/funding | Tier | Notes |
|---|---|---|---|---|---|
| Binance | `binance` (perps `binanceusdm`) | yes | yes | 1 | testnet.binance.vision |
| Binance US | `binanceus` | inherits (unverified) | no | 1 | |
| Coinbase Advanced | `coinbase` | no | no | 1 | tier-based fees fetched with keys |
| Coinbase Exchange | `coinbaseexchange` | yes | no | 1 | needs API passphrase |
| Kraken | `kraken` | no spot sandbox | no | 1 | |
| Kraken Futures | `krakenfutures` | yes | yes | 1 | |
| OKX | `okx` | demo keys | yes | 1 | needs passphrase |
| Bybit | `bybit` | yes | yes | 1 | |
| KuCoin | `kucoin` (perps `kucoinfutures`) | no | yes | 1 | needs passphrase |
| Gate | `gate` | REST only | yes | 1 | |
| Bitget | `bitget` | no | yes | 1 | needs passphrase |
| MEXC | `mexc` | no | limited | 1 | |
| Hyperliquid | `hyperliquid` | yes | yes | 1 | wallet-key auth |
| HTX, Crypto.com, Bitfinex, Bitstamp, WOO X, BingX, Phemex, Deribit, BitMEX | see registry | varies | varies | 2 | supported via ccxt |

Per-connector test matrix (fill in on your deployment; `docs/CONNECTORS.md` is the record):

| Check | How | Result (this build) |
|---|---|---|
| market data | `fetch_order_book` via paper mode; quality gate `fedr/marketdata/quality.py` | SUPPORTED + TESTED (offline, synthetic + ccxt metadata); live: BLOCKED (no egress) - enable with `FEDR_IT_NETWORK=1` |
| order book WS | ccxt.pro `watch_order_book` with REST fallback; `WEBSOCKET_FAILURE` breaker on loss | SUPPORTED + TESTED (fallback logic); live: BLOCKED |
| balance | `fetch_balance` on account add and every balance loop | UNVERIFIED against a venue; BLOCKED here (`FEDR_IT_CCXT_*`) |
| order create/cancel/fills | IOC limit round-trip in TESTNET | UNVERIFIED against a venue; BLOCKED here (sandbox keys) |
| fees | `fetch_trading_fees` → market metadata → documented fallback (unknown fee = BLOCKED route) | SUPPORTED + TESTED |
| precision / min sizes | `amount_step`, `min_amount`, `min_cost` from `load_markets` | SUPPORTED + TESTED (ccxt metadata) |
| rate limits | ccxt throttler + `RateLimitExceeded` → DEGRADED | SUPPORTED + TESTED (error mapping); behaviour under real limits UNVERIFIED |
| errors | ccxt error hierarchy → ConnectorError classes | SUPPORTED + TESTED |
| sandbox | `set_sandbox_mode` gated by registry `sandbox` flag | UNVERIFIED (BLOCKED here) |
| clock drift | `fetch_time` vs local clock every 5 min → DEGRADED ≥1 s, UNHEALTHY ≥10 s | SUPPORTED + TESTED (`tests/test_clock.py`) |

## DEX (via Hummingbot Gateway 2.16.0)

| Venue | Chain(s) | Type | Testnet | Notes |
|---|---|---|---|---|
| Jupiter | Solana | router (quoteId) | devnet (nominal) | API key recommended |
| Raydium | Solana | amm | devnet (nominal) | pool address from Gateway pool list |
| Meteora, Orca | Solana | clmm | devnet (nominal) | |
| Uniswap | Ethereum, Base, Arbitrum, Optimism, Polygon, BSC, Avalanche | router (Universal Router, Permit2) | none (Sepolia has no Gateway DEX connector) | |
| PancakeSwap | BSC, Base, Arbitrum, Ethereum | router | none | |
| 0x | Ethereum, Base, Arbitrum, Optimism, Polygon | router | none | needs 0x API key in Gateway |

Enabled by default for scanning: `jupiter`, `uniswap-base`, `uniswap-arbitrum` (others via Settings → Exchanges).

## Chains

Solana, Ethereum, Base, Arbitrum, Optimism, Polygon, BSC, Avalanche (all through Gateway). Testnets
through Gateway: Solana devnet, Ethereum Sepolia (chain-level only). Gas reserves per chain are configurable.
