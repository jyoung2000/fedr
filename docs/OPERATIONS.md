# Operations

## Modes

| Mode | Market data | Balances | Execution | Notes |
|---|---|---|---|---|
| SIMULATION | synthetic (deterministic seed) or replayed JSONL | simulated | paper executor | exercises the whole pipeline offline; clearly labelled |
| PAPER (default) | real (public ccxt endpoints + Gateway quotes) | simulated, editable | paper executor (latency, drift, stress, partial fills, gas) | same Profit Guard / Risk Engine as live |
| TESTNET | exchange sandboxes, Solana devnet, Sepolia | real test assets | real orders | only exchanges with a ccxt sandbox; separate credential scope |
| LIVE | real | real | real | requires env gate + checklist + activation phrase; starts in shadow mode |

Shadow mode is a toggle available in every mode: evaluate, never submit, record `would_trade`, route, size,
predicted profit, worst case, estimated costs, reason, and a re-priced hypothetical profit after a delay.

## Environment variables

See `.env.example` — every variable is documented there. Highlights:

* `FEDR_PORT=8935`, `FEDR_DATA_DIR=/data`, `FEDR_DEFAULT_MODE=paper`
* `FEDR_MASTER_KEY` (optional; auto-generated at `/data/config/master.key`)
* `FEDR_AUTH_TOKEN` (recommended; required for live)
* `FEDR_LIVE_TRADING_ALLOWED=false` (hard gate)
* `FEDR_GATEWAY_URL=http://gateway:15888`, `GATEWAY_PASSPHRASE`
* `FEDR_RPC_<CHAIN>` (gas oracles, deposits/withdrawals, flash loans)
* `FEDR_EVM_PRIVATE_RPC` (MEV-aware submission), `FEDR_FLASHLOAN_CONTRACT_<CHAIN>`

## Persistent layout (`./data`)

```
data/
  database/fedr.db     trades, orders, fills, P&L, audit, settings, wallets (encrypted), breakers
  config/master.key    encryption key (0600) unless FEDR_MASTER_KEY is set
  wallets/             reserved for exported backups you choose to keep here
  strategies/          reserved for strategy presets
  logs/                (app logs go to stdout; Gateway logs to ./gateway/logs)
  backups/             copy fedr.db here with `sqlite3 data/database/fedr.db ".backup data/backups/fedr-$(date +%F).db"`
  market-data/         recorded order-book JSONL for replay backtests
  reports/             exported reports
```

## Runbooks

**Restart**: `docker compose restart app` — state is in `/data`; open trades are reconciled on boot.

**Emergency stop**: header button (or `POST /api/system/emergency-stop`). Stops new trades, cancels
cancellable CEX orders, blocks DEX / flash-loan execution, runs reconciliation, persists the flag. Release
from the banner after investigating.

**Circuit breaker tripped**: the banner lists reason and detail; investigate (History → Risk events, Audit),
then "Reset after investigation". Daily-loss and one-leg-fill breakers deserve a look at History → Trades.

**Reconciliation discrepancy**: trading pauses (`BALANCE_DISCREPANCY`). Compare the exchange balance with
History → Trades and the audit log, correct inventory, then reset the breaker.

**Rotate the UI token**: change `FEDR_AUTH_TOKEN`, `docker compose up -d`.

**Backup**: stop the app or use SQLite's online backup (above); also export encrypted wallet keystores from
Wallets → Backup and store them offline.

**Upgrade**: `git pull && docker compose build && docker compose up -d`. The settings document is versioned
(`AppSettings.version`); unknown fields are ignored, missing ones get defaults.

## Health

* `GET /api/system/health` — liveness (used by the Docker healthcheck)
* `GET /api/system/startup-check` — SYSTEM READY or explicit issues
* `GET /api/system/readiness` — live readiness checklist
* Dashboard → venue health (HEALTHY / DEGRADED / UNHEALTHY / BLOCKED with reasons)
