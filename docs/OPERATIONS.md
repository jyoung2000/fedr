# Operations

## Modes

| Mode | Market data | Balances | Execution | Notes |
|---|---|---|---|---|
| SIMULATION | synthetic (deterministic seed) or replayed JSONL | simulated | paper executor | exercises the whole pipeline offline; clearly labelled |
| PAPER (default) | real (public ccxt endpoints + Gateway quotes) | simulated, editable | paper executor (latency, drift, stress, partial fills, gas) | same Profit Guard / Risk Engine as live |
| TESTNET | exchange sandboxes, Solana devnet, Sepolia | real test assets | real orders | only exchanges with a ccxt sandbox; separate credential scope |
| LIVE | real | real | real | env gate + checklist + phrase; starts in shadow AND in the SMALL LIVE TEST stage (one strategy, one route, hard $25 cap); full live is a second phrase (`ACTIVATE FULL LIVE TRADING`) normally requiring a filled small-test trade |

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

**Restart**: `docker compose restart app` — state is in `/data`. On boot FEDR runs **journal recovery before
admitting anything**: every trade whose order intents were journaled but not settled is rebuilt, its
reservations are restored, its venues are paused (`UNRESOLVED_ORDER`) and each leg is looked up by client order
id / transaction hash. New submissions stay blocked until that pass completes (`GET /api/trading/unresolved` →
`recovery_complete`). Only one FEDR process may use a data directory (`/data/fedr.lock`); a second one exits.

**Unknown order outcome** (Trading → Unresolved orders): a timeout, dropped connection, failed cancel or
unconfirmed transaction is never treated as "not filled". The capital stays reserved, the venue is paused and
the resolution loop retries the lookup every 5 s. If the venue can no longer answer (order history expired,
DEX transaction without a hash), check the venue's own records and use "Resolve from venue records": enter
the filled amount, total quote cost and fee, plus where you verified it. The resolution is audited; the trade
is then settled exactly once (hedge of a confirmed imbalance, accounting, P&L).

**Unreconciled trade** (status `unreconciled`): every leg is final but the venue did not report a value needed
for realized P&L (cost, fee, gas price). Nothing is booked; resolve the leg from the venue's records the same way.

**Mode / venue changes with active work**: changing mode, pairs, DEXes, strategies, exchange accounts or entering
LIVE is refused while trades are in flight/unresolved or carry positions are open — the connectors that monitor
them would be swapped out. Close positions and resolve trades first.

**Emergency stop**: header button (or `POST /api/system/emergency-stop`). Stops new trades, cancels
cancellable CEX orders, blocks DEX / flash-loan execution, runs reconciliation, persists the flag. Release
from the banner after investigating.

**Circuit breaker tripped**: the banner lists reason and detail; investigate (History → Risk events, Audit),
then "Reset after investigation". Daily-loss and one-leg-fill breakers deserve a look at History → Trades.

**Reconciliation** (Health → Reconciliation, `GET /api/system/reconciliation`): on testnet/live, expected
balances come from a durable ledger: an opening checkpoint per account (its first observation, recorded before
any trade), confirmed fills/fees/gas, withdrawals (from their receipts), deposits and explicit adjustments. A
venue snapshot never overwrites the expectation; an expected asset the venue stops reporting is a discrepancy;
a failed balance or open-order query is *incomplete* (that venue is paused until it can be observed again);
accounts with work in flight are deferred. An increase on an idle account is booked as an audited "deposit
(source not verified)"; a decrease is never auto-explained.

**Reconciliation discrepancy**: trading pauses (`BALANCE_DISCREPANCY`). Compare the venue's history with
History → Trades, the ledger entries and the audit log. When the difference is explained (e.g. a venue fee
FEDR could not see), record it with "Record an adjustment" (`POST /api/system/reconciliation/adjust`, note
required) — the ledger gets a new `adjustment` row; history is never rewritten. Then reset the breaker.

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
the stack and probes `/health` (`--docker-prebuilt` skips the build and probes an already-built image); `--ui`
runs the Playwright QA. Writes `docs/PRODUCTION_VERIFICATION_REPORT.md`.
Enable external checks with: `FEDR_IT_DOCKER=1` (running stack), `FEDR_IT_NETWORK=1` (public market data),
`FEDR_IT_CCXT_EXCHANGE/KEY/SECRET` + `FEDR_IT_CCXT_SANDBOX=1` (sandbox round-trip),
`FEDR_IT_GATEWAY_URL` (Gateway), `FEDR_IT_TESTNET=1` + `FEDR_RPC_SEPOLIA` + `FEDR_IT_TESTNET_ADDRESS`.

**Upgrade**: `git pull && docker compose build && docker compose up -d`. The settings document is versioned
(`AppSettings.version`); unknown fields are ignored, missing ones get defaults. Before a schema upgrade FEDR
writes a consistent copy of the database next to it (`fedr.db.pre-v<new>-from-v<old>-<ts>.bak`, SQLite
`VACUUM INTO`). **Roll back** = stop FEDR, replace `database/fedr.db` with that file, start the previous image.

Schema v3 (order journal, settlement provenance, reconciliation ledger) marks existing trades
`settlement=legacy` and existing P&L days `legacy` instead of "correcting" them: releases before v3 booked a
carry entry's spread at entry *and* the lifecycle at close (audit F06). Open carry positions remember what was
booked at entry, so their close books only the remainder. The opening reconciliation checkpoint of each real
account is taken at the first observation after the upgrade.

**Flash loans on mainnet** stay disabled until the exact deployed bytecode passed fork validation and an
independent review: set `FEDR_FLASHLOAN_APPROVED_CODEHASH_<CHAIN>` to `keccak256(runtime code)` of that
deployment (`cast codehash <addr>`). FEDR compares it with the on-chain code hash before every simulation and
broadcast and verifies the RPC chain id before signing. TESTNET flash loans are not supported.

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
