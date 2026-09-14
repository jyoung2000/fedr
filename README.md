# FEDR — self-hosted, capital-preserving crypto arbitrage

> **Simple UI. Serious execution and risk engine underneath.**
> Discover arbitrage → calculate all-in profit → verify risk → execute → reconcile → learn.
>
> Arbitrage is **not** risk-free and profits are **never** guaranteed. FEDR is built to say **no**
> far more often than **yes**: every trade must clear a deterministic Profit Guard on *expected* **and**
> *worst-case* net profit after every known cost, a Gas Guard, a Risk Engine and the circuit breakers.
> Estimated profit is never treated as realized profit.

```
docker compose up -d
open http://localhost:8935
```

Default mode is **PAPER** (real market data, simulated balances and execution). **LIVE is off** and can
only be entered through the readiness checklist + explicit activation phrase in the UI.

## What you get

| Area | Summary |
|---|---|
| Modes | SIMULATION (synthetic/replayed data) · PAPER · TESTNET · LIVE — P&L never mixed |
| Shadow mode | Evaluate real markets, submit nothing, record would-trade / predicted vs hypothetical |
| Venues | CEX via **ccxt** (Binance, Binance US, Coinbase, Kraken, OKX, Bybit, KuCoin, Gate, Bitget, MEXC, Hyperliquid, …) · DEX via **Hummingbot Gateway** (Jupiter, Raydium, Meteora, Orca, Uniswap, PancakeSwap, 0x on Solana/Ethereum/Base/Arbitrum/Optimism/Polygon/BSC/Avalanche) |
| Strategies | CEX↔CEX, CEX↔DEX (on by default) · DEX↔DEX, Spot↔Perp, Funding, Basis, Flash loans (off) |
| Guards | Profit Guard (gross / expected / worst-case), Gas Guard (regimes), Slippage Guard, Latency Guard, Risk Engine, 17 circuit breakers, reconciliation, emergency stop |
| Execution | Paired IOC execution, partial-fill handling, emergency hedge on one-leg fills, quote expiry requote-or-abort, flash-loan abort-before-broadcast |
| Wallets | Isolated bot wallets (EVM + Solana) encrypted at rest, encrypted backups, deposit QR, allowlisted withdrawals, MetaMask/Phantom for funding only |
| Learning | Per-route execution error, slippage/fee/gas variance, fill reliability → bounded extra safety buffer (never the trade authority) |
| Persistence | Everything in `/data` (SQLite WAL): settings, wallets, trades, fills, P&L, audit log, breakers, paper balances |

## Quick start

1. `cp .env.example .env` and set `GATEWAY_PASSPHRASE` (long random string). Optionally set
   `FEDR_AUTH_TOKEN` (required before live).
2. `docker compose up -d` — app on `127.0.0.1:8935`, Gateway on the private network only.
3. Open http://localhost:8935. You start in PAPER mode with $10k of simulated inventory spread over
   Kraken / Coinbase / Binance (public market data) and Solana / Base / Arbitrum bot wallets.
4. Watch **Opportunities**: every route shows Gross / Expected / Worst-case / Required and *why* it is
   blocked. Most routes are blocked most of the time — that is the point.
5. Add exchange API keys (READ + TRADE only, **never withdraw**) under **Exchanges**, create bot wallets
   under **Wallets**, fund them, run in shadow mode, then work through the live checklist in **Settings → Live**.

No exchange or RPC access at all? `docker compose -f docker-compose.yml -f docker-compose.dev.yml up`
starts in **SIMULATION** mode with a synthetic multi-venue market so you can exercise the whole pipeline.

## Overlays

| File | Purpose |
|---|---|
| `docker-compose.yml` | default (paper), loopback-only ports, hardened containers |
| `docker-compose.dev.yml` | hot reload, DEBUG logs, simulation mode |
| `docker-compose.paper.yml` | pins paper mode |
| `docker-compose.testnet.yml` | exchange sandboxes + Solana devnet / Sepolia |
| `docker-compose.live.yml` | sets the env gate `FEDR_LIVE_TRADING_ALLOWED=true`, requires `FEDR_AUTH_TOKEN` |

State persists across `docker compose down && docker compose up -d` in `./data` (database, config,
wallets, logs, backups, market-data, reports) and `./gateway/conf`.

## Documentation

* [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — modules, data flow, profit maths
* [docs/OPERATIONS.md](docs/OPERATIONS.md) — modes, environment variables, runbooks, backups
* [docs/SECURITY.md](docs/SECURITY.md) — threat model, secrets, Docker hardening, live gates
* [docs/CONNECTORS.md](docs/CONNECTORS.md) — connector matrix and verification status
* [docs/MEV.md](docs/MEV.md) — private submission options
* [docs/RESEARCH.md](docs/RESEARCH.md) — source projects inspected, licenses, what was reused
* [docs/RELEASE_REPORT.md](docs/RELEASE_REPORT.md) — what was built, tested, verified, and what was not
* [contracts/README.md](contracts/README.md) — flash-loan contract

## Development

```bash
python3.11 -m venv .venv && . .venv/bin/activate
pip install -e "backend[dev]"
cd frontend && npm ci && npm run build && cd ..
FEDR_DEFAULT_MODE=simulation FEDR_GATEWAY_ENABLED=false FEDR_DATA_DIR=./data python -m fedr.main
pytest backend/tests -q
```

## License

Apache-2.0. Third-party: ccxt (MIT), Hummingbot Gateway (Apache-2.0, run as a container), OpenZeppelin
(MIT), see `docs/RESEARCH.md` for attribution.
