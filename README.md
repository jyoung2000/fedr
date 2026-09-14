# FEDR — self-hosted, capital-preserving crypto arbitrage

> **Simple UI. Serious execution and risk engine underneath.**
> Discover arbitrage → calculate all-in profit → verify risk → execute → reconcile → learn.
>
> Arbitrage is **not** risk-free and profits are **never** guaranteed. FEDR is built to say **no**
> far more often than **yes**: every trade must clear a deterministic Profit Guard on *expected* **and**
> *worst-case* net profit after every known cost, a Gas Guard, a Risk Engine and 19 circuit breakers.
> Estimated profit is never treated as realized profit.

```
cp .env.paper.example .env      # then set FEDR_AUTH_TOKEN and GATEWAY_PASSPHRASE
docker compose up -d
open http://localhost:8935      # log in with FEDR_AUTH_TOKEN
```

Default mode is **PAPER** (real market data, simulated balances and execution). **LIVE is off** and can
only be entered through the readiness checklist + typed confirmation in the UI; an environment variable
alone never enables it.

## Verification status — read before trusting anything

`docs/STATUS.json` says **`production_ready: false`**, and it will keep saying so until the checks that
need a real exchange, RPC, Gateway and Docker registry have been run on *your* deployment. What that
means concretely (full detail in [docs/PRODUCTION_VERIFICATION_MATRIX.md](docs/PRODUCTION_VERIFICATION_MATRIX.md),
machine-readable in `docs/AUDIT_REPORT.json`):

| Verified here (automated evidence) | Blocked by this build environment | Must stay blocked |
|---|---|---|
| SIMULATION and SHADOW as a real process; paper execution engine; Profit Guard incl. bypass tests; Gas Guard; Risk Engine; all 19 circuit-breaker reasons; emergency stop + hedge; reconciliation; carry lifecycle (paper); flash-loan contract compile + EVM tests; wallet encryption, backup → restore into a fresh install; withdrawal construction/allowlist/idempotency; market-data quality gate; mandatory auth, CSRF, lockout; migrations, backup/restore tooling; UI danger states at 4 widths; dependency + secret scans | anything touching a live venue: public market data, exchange sandbox/live orders, Gateway/DEX swaps, RPC/gas, on-chain deposits/withdrawals, testnet; the official Docker images (registry blocked — the Dockerfile was built with substituted base images instead); contract static analysis / fork tests | **LIVE trading with real funds** until sandbox → testnet → paper → shadow have been completed on your deployment and the flash-loan contract has been audited |

Every blocked check has a ready-made test in `backend/tests/integration/` that skips with
`EXTERNAL ENVIRONMENT REQUIRED: …` until you set the documented variable. `scripts/verify_production.py`
runs the whole sequence and writes `docs/PRODUCTION_VERIFICATION_REPORT.md`; a skipped check is never a pass.

## What you get

| Area | Summary |
|---|---|
| Modes | SIMULATION (synthetic/replayed data) · PAPER · TESTNET · LIVE — P&L never mixed |
| Shadow mode | Evaluate real markets, submit nothing, record would-trade / predicted vs hypothetical |
| Venues | CEX via **ccxt** (Binance, Binance US, Coinbase, Kraken, OKX, Bybit, KuCoin, Gate, Bitget, MEXC, Hyperliquid, …) · DEX via **Hummingbot Gateway** (Jupiter, Raydium, Meteora, Orca, Uniswap, PancakeSwap, 0x on Solana/Ethereum/Base/Arbitrum/Optimism/Polygon/BSC/Avalanche) |
| Strategies | CEX↔CEX, CEX↔DEX (on by default) · DEX↔DEX, Spot↔Perp, Funding, Basis, Flash loans (off) |
| Guards | Profit Guard (gross / expected / worst-case), Gas Guard (regimes), Slippage Guard, Latency Guard, Risk Engine, 19 circuit breakers (all wired + tested), market-data quality gate, clock-drift check, reconciliation, emergency stop |
| Execution | Paired IOC execution, partial-fill handling, emergency hedge on one-leg fills, quote expiry requote-or-abort, flash-loan abort-before-broadcast |
| Wallets | Isolated bot wallets (EVM + Solana) encrypted at rest, encrypted backups + restore into a fresh install, deposit detection with dedupe, allowlisted withdrawals (native + ERC-20/SPL, new-address delay, idempotent), MetaMask/Phantom for funding only |
| Carry | Spot↔perp / funding / basis positions: paired entry, funding accrual, exit rules (convergence, funding flip, max hold, liquidation distance, basis stop), manual/forced close, reconciliation — paper-verified only |
| Operations | `/health/live`, `/health/ready`, `/api/system/metrics`, audit log, versioned migrations, `scripts/backup.py`, `scripts/verify_production.py`, integration tests gated on your environment |
| Learning | Per-route execution error, slippage/fee/gas variance, fill reliability → bounded extra safety buffer (never the trade authority) |
| Persistence | Everything in `/data` (SQLite WAL): settings, wallets, trades, fills, P&L, audit log, breakers, paper balances |

## Quick start

1. Copy the profile you need — `.env.dev.example` (simulation, nothing external), `.env.paper.example`
   (default), `.env.testnet.example`, `.env.live.example` — to `.env`; set `GATEWAY_PASSPHRASE` and
   `FEDR_AUTH_TOKEN` (if you leave the token empty one is generated into `data/config/ui-token`, mode 0600).
2. `docker compose up -d` — app on `127.0.0.1:8935`, Gateway on the private network only. Startup refuses
   unsafe configurations (live boot default, live-allowed without a token) and exits with the reason.
3. Open http://localhost:8935 and log in with the token. You start in PAPER mode with simulated inventory
   over Kraken / Coinbase / Binance (public market data) and Solana / Base / Arbitrum bot wallets.
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

State persists across `docker compose down && docker compose up -d` in the named volume `fedr-data`
(database, config, wallets, logs, backups, market-data, reports) and `./gateway/conf`. `docker compose down -v`
deletes it. To keep state in a host directory instead, mount `./data:/data` and `chown -R 10001:10001 data`
first (the app runs as uid 10001 and refuses to start on an unwritable data dir).

## Documentation

* [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — modules, data flow, profit maths
* [docs/OPERATIONS.md](docs/OPERATIONS.md) — modes, environment variables, runbooks, backups
* [docs/SECURITY.md](docs/SECURITY.md) — threat model, secrets, Docker hardening, live gates
* [docs/CONNECTORS.md](docs/CONNECTORS.md) — connector matrix and verification status
* [docs/MEV.md](docs/MEV.md) — private submission options
* [docs/RESEARCH.md](docs/RESEARCH.md) — source projects inspected, licenses, what was reused
* [docs/PRODUCTION_VERIFICATION_MATRIX.md](docs/PRODUCTION_VERIFICATION_MATRIX.md) — every capability with exactly one classification and its evidence (`docs/AUDIT_REPORT.json` is the machine-readable form, `docs/STATUS.json` the summary)
* [docs/FINAL_PRODUCTION_REPORT.md](docs/FINAL_PRODUCTION_REPORT.md) — phase-2 verification + hardening report; what changed, what is proven, what is not
* [docs/PRODUCTION_VERIFICATION_REPORT.md](docs/PRODUCTION_VERIFICATION_REPORT.md) — output of the last `scripts/verify_production.py` run
* [docs/DOCKER_SECURITY_REPORT.md](docs/DOCKER_SECURITY_REPORT.md) — image/compose hardening and the runtime checks that were actually executed
* [docs/PROFIT_GUARD_CALL_GRAPH.md](docs/PROFIT_GUARD_CALL_GRAPH.md) — every path to an order and the gate it passes
* [docs/UI_QA_REPORT.md](docs/UI_QA_REPORT.md) — Playwright results at 1280 / 1024 / 768 / 400 px incl. danger states
* [docs/DEPENDENCY_LICENSES.md](docs/DEPENDENCY_LICENSES.md) — third-party licenses
* [docs/RELEASE_REPORT.md](docs/RELEASE_REPORT.md) — phase-1 build report
* [contracts/README.md](contracts/README.md) — flash-loan contract

## Development

```bash
python3.11 -m venv .venv && . .venv/bin/activate
pip install -e "backend[dev]"
cd frontend && npm ci && npm run build && cd ..
FEDR_DEFAULT_MODE=simulation FEDR_GATEWAY_ENABLED=false FEDR_DATA_DIR=./data python -m fedr.main
cd backend && pytest tests -q                      # unit + adversarial + process-level integration
python ../scripts/verify_production.py --ui        # full sequence → docs/PRODUCTION_VERIFICATION_REPORT.md
python qa/ui_qa.py --mock                          # Playwright UI QA (mocked states, 4 widths)
```

Contract tests: `cd contracts && npm ci && node compile.js` then `pytest backend/tests/test_contract_evm.py`.

## License

Apache-2.0. Third-party: ccxt (MIT), Hummingbot Gateway (Apache-2.0, run as a container), OpenZeppelin
(MIT), see `docs/RESEARCH.md` for attribution.
