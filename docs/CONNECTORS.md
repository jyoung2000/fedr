# Connector matrix

Status vocabulary (progressive): **SUPPORTED → CONNECTED → HEALTHY → TRADEABLE → ARBITRAGE-ELIGIBLE**.
Verification levels: `supported_not_verified_live`, `testnet_verified`, `live_verified`.

> **Honesty note.** This release was built in an environment with **no network access to any exchange,
> DEX or RPC endpoint** (egress blocked). Therefore every connector is `SUPPORTED — NOT VERIFIED LIVE`.
> The code paths were exercised against ccxt's offline market/capability metadata, the Gateway 2.16
> route contract (read from source), synthetic venues, and unit tests. Run the matrix below on your
> deployment (paper → testnet → shadow) before trusting a venue.

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
| market data | `fetch_order_book` via paper mode | not verified live (offline) |
| order book WS | ccxt.pro `watch_order_book` with REST fallback | not verified live |
| balance | `fetch_balance` on account add | not verified live |
| order create/cancel/fills | IOC limit round-trip in TESTNET | not verified live |
| fees | `fetch_trading_fees` → market metadata → documented fallback | code path unit-tested |
| precision / min sizes | `amount_step`, `min_amount`, `min_cost` from `load_markets` | unit-tested with ccxt metadata |
| rate limits | ccxt throttler + `RateLimitExceeded` → DEGRADED | code path |
| errors | ccxt error hierarchy → ConnectorError classes | unit-tested |
| sandbox | `set_sandbox_mode` gated by registry `sandbox` flag | code path |

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
