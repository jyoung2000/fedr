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

`.env.example` documents every variable. Start from the profile that matches what you are doing and copy it
to `.env`: `.env.dev.example` (SIMULATION, nothing external), `.env.paper.example` (PAPER, the default),
`.env.testnet.example` (sandbox keys + test networks), `.env.live.example` (read the header first).
Startup validation refuses unsafe combinations - `FEDR_DEFAULT_MODE=live`, `FEDR_LIVE_TRADING_ALLOWED=true`
without a >=16-character `FEDR_AUTH_TOKEN`, an unwritable data dir, a malformed master key - and exits
with the reason instead of running degraded. Highlights:

* `FEDR_PORT=8935`, `FEDR_DATA_DIR=/data`, `FEDR_DEFAULT_MODE=paper`
* `FEDR_MASTER_KEY` (optional; auto-generated at `/data/config/master.key`)
* `FEDR_AUTH_TOKEN` (recommended; required for live)
* `FEDR_LIVE_TRADING_ALLOWED=false` (hard gate)
* `FEDR_GATEWAY_URL=http://gateway:15888`, `GATEWAY_PASSPHRASE`
* `FEDR_RPC_<CHAIN>` (gas oracles, deposits/withdrawals, flash loans)
* `FEDR_EVM_PRIVATE_RPC` (MEV-aware submission), `FEDR_FLASHLOAN_CONTRACT_<CHAIN>`

## Persistent layout (volume `fedr-data`, mounted at `/data`)

Default is the named volume `fedr-data` (survives `docker compose down`, removed by `down -v`). Inspect it with
`docker compose exec app ls -la /data` or copy files out with `docker compose cp app:/data/backups ./backups`.
Bind-mounting `./data:/data` works too but the directory must be owned by uid 10001 (`chown -R 10001:10001 data`);
the app refuses to start on an unwritable data dir rather than running without persistence.

```
/data/
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

**Backup / restore** (`scripts/backup.py`, works while FEDR runs - SQLite online backup API):

```bash
python3 scripts/backup.py create --data-dir ./data                 # -> data/backups/fedr-backup-<ts>.tar.gz
python3 scripts/backup.py verify data/backups/fedr-backup-<ts>.tar.gz
python3 scripts/backup.py restore data/backups/fedr-backup-<ts>.tar.gz --data-dir ./data-restored
```

The archive holds the database, config (minus secrets), encrypted wallet keystores, strategies and reports
with a SHA-256 manifest; `verify` also scans for plaintext keys. `config/master.key` and `config/ui-token`
are **excluded** unless `--include-secrets` is given: keep the master key offline - a restored database
cannot decrypt credentials or bot-wallet keys without the original key. Also export each bot wallet's
passphrase-encrypted keystore from Wallets → Backup; `POST /api/wallets/bot/import` (Wallets → Import
backup) restores it into a fresh installation and verifies the address.

**Verify a deployment** (`scripts/verify_production.py`): runs lint, unit + adversarial tests, integration
tests (process-level; external checks skip with `EXTERNAL ENVIRONMENT REQUIRED` unless enabled), secret
scan, dependency audits, frontend build, contract compile, compose config; `--docker` builds and starts
the stack and probes `/health`; `--ui` runs the Playwright QA. Writes `docs/PRODUCTION_VERIFICATION_REPORT.md`.
Enable external checks with: `FEDR_IT_DOCKER=1` (running stack), `FEDR_IT_NETWORK=1` (public market data),
`FEDR_IT_CCXT_EXCHANGE/KEY/SECRET` + `FEDR_IT_CCXT_SANDBOX=1` (sandbox round-trip),
`FEDR_IT_GATEWAY_URL` (Gateway), `FEDR_IT_TESTNET=1` + `FEDR_RPC_SEPOLIA` + `FEDR_IT_TESTNET_ADDRESS`.

**Upgrade**: `git pull && docker compose build && docker compose up -d`. The settings document is versioned
(`AppSettings.version`); unknown fields are ignored, missing ones get defaults.

## Health

* `GET /health/live` — process is up (always 200 once the server binds)
* `GET /health/ready` — 200 only when the database, guards, strategy engine, scan loop and (when DEX
  strategies are on outside simulation) the Gateway are up and no emergency stop is active; body lists each check
* `GET /health` — summary (200 healthy / 503 degraded)
* `GET /api/system/metrics` — counters (opportunities, trades, P&L, breaker trips, market-data rejections,
  connector errors, clock drift per venue), venue health, breakers, gas
* `GET /api/system/health` — liveness summary (used by the Docker healthcheck)
* `GET /api/system/startup-check` — SYSTEM READY or explicit issues
* `GET /api/system/readiness` — live readiness checklist
* Dashboard → venue health (HEALTHY / DEGRADED / UNHEALTHY / BLOCKED with reasons)
