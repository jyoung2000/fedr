# Live verification runbook

The exact sequence that takes a fresh FEDR checkout to a verified live deployment. Every step names the
command to run and the evidence it must produce; do not skip a step or advance past one that failed.
Nothing before step 14 touches real funds beyond deposits you make yourself, and even step 14 is capped at
a hard-coded $25 notional. As steps complete on *your* host, update the "Result" column in
`docs/CONNECTORS.md` and re-run `python3 scripts/audit_matrix.py` — the software never marks itself verified.

Prerequisites: Docker with registry access, ~4 GB free, and (from step 7) exchange sandbox keys made for
this purpose with withdrawal permission OFF.

## 1. Install

```bash
git clone https://github.com/jyoung2000/fedr && cd fedr
cp .env.paper.example .env
python3 -c "import secrets;print('FEDR_AUTH_TOKEN='+secrets.token_urlsafe(32))"   # paste into .env
python3 -c "import secrets;print('GATEWAY_PASSPHRASE='+secrets.token_urlsafe(24))" # paste into .env
```

Evidence: `.env` exists with a ≥16-char token; it is gitignored (`git check-ignore .env`).

## 2. Start Docker

```bash
docker compose build && docker compose up -d && docker compose ps
FEDR_IT_DOCKER=1 python3 -m pytest backend/tests/integration -q -k docker
```

Evidence: both containers `Up (healthy)`; `curl -s 127.0.0.1:8935/health/ready` → 200 with every check true
(`gateway` true once the Gateway container is up). This closes matrix rows RT-02/RT-04/RT-05.

## 3. Open the UI on port 8935

Open http://localhost:8935, log in with `FEDR_AUTH_TOKEN`. Evidence: dashboard renders with the PAPER
chip; `/status` page shows the readiness checklist.

## 4. Create the bot trading wallet(s)

Wallets → Create bot wallet (EVM and/or Solana; testnet mode first). Evidence: address shown; audit log
entry; `GET /api/wallets` lists it with `backed_up: false`.

## 5. Back the wallet up

Wallets → Backup → passphrase → download keystore → confirm. Then prove the restore path:

```bash
python3 scripts/backup.py create --data-dir ./data && python3 scripts/backup.py verify data/backups/fedr-backup-*.tar.gz
```

Evidence: wallet shows `backed_up: true` (a live-readiness requirement); keystore restores in a scratch
install via Wallets → Import backup with the same address.

## 6. Connect market data (no keys needed)

```bash
FEDR_IT_NETWORK=1 python3 -m pytest backend/tests/integration -q -k market_data
```

Evidence: test passes the quality gate against real public books; UI Opportunities page shows live routes
with Gross / Expected / Worst-case and block reasons. Closes MD-03; move the exchanges you use to
`SUPPORTED + TESTED (live data)` in CONNECTORS.md.

## 7. Connect the first CEX

Exchanges → Add keys (READ + TRADE only, withdrawal OFF). Evidence: connection test green; permissions
panel shows withdraw disabled; balances load.

## 8. Validate the exchange sandbox

```bash
FEDR_IT_CCXT_EXCHANGE=binance FEDR_IT_CCXT_SANDBOX=1 FEDR_IT_CCXT_KEY=… FEDR_IT_CCXT_SECRET=… \
  python3 -m pytest backend/tests/integration -q -k sandbox
```

Evidence: balance fetch + tiny order create/cancel/fill round-trip passes. Closes CX-02 for that venue →
`SUPPORTED + SANDBOX VERIFIED`.

## 9. Connect DEX infrastructure

Gateway is already up from step 2. Evidence:

```bash
FEDR_IT_GATEWAY_URL=http://127.0.0.1:15888 python3 -m pytest backend/tests/integration -q -k gateway
```

quotes for an exact size return with fee/impact/gas fields. Closes CX-03 (quoting).

## 10. Validate testnet paths

```bash
FEDR_IT_TESTNET=1 FEDR_RPC_SEPOLIA=… FEDR_IT_TESTNET_ADDRESS=0x… python3 -m pytest backend/tests/integration -q -k testnet
```

Switch the UI to TESTNET mode; run a test order on the sandbox exchange and a devnet/Sepolia transfer.
Evidence: fills and receipts reconcile in History. Closes MO-04, CX-04 (testnet).

## 11. Deposit tiny capital

Wallets → Deposit (QR / address) from your MetaMask/Phantom or exchange. Evidence: deposit detected,
deduplicated, credited once, listed in the deposit ledger with the tx hash.

## 12. Paper mode (days, not minutes)

Run PAPER with real data for at least several days. Evidence: History shows trades with estimated vs
actual (simulated) costs; no breaker storms; `GET /api/system/metrics` prediction error stays bounded.

## 13. Shadow mode

Trading → Shadow ON in LIVE-config (or paper). Evidence: would-trades recorded with predicted vs
re-priced hypothetical outcomes; zero orders submitted (`orders` count unchanged).

## 14. Small live test

Settings → Live: complete the readiness checklist, type `ACTIVATE LIVE TRADING`. Live ALWAYS starts in
the SMALL LIVE TEST stage: pick the one strategy and the one route (e.g. `kraken->coinbase`) — everything
else is blocked by the Risk Engine and the notional is hard-capped at $25. Turn shadow off, execute one
opportunity manually. Evidence: both legs fill; History shows estimated vs ACTUAL net with fee/slippage
variance; reconciliation clean.

## 15. Reconciliation check

```bash
curl -s -H "Authorization: Bearer $TOKEN" 127.0.0.1:8935/api/system/status | python3 -m json.tool | grep -A3 reconciliation
```

Compare exchange balances against the UI after the trade. Evidence: no `BALANCE_DISCREPANCY` breaker; the
trade's ledger delta equals its realized net.

## 16. Actual cost verification

In History open the trade detail. Evidence: actual fees/slippage/gas within your tolerance of the
estimate; if prediction error is large, stop and recalibrate (experience engine raises buffers on its own).

## 17. Enable full live for one strategy

`POST /api/system/live/full` with `ACTIVATE FULL LIVE TRADING` (the UI asks for it) — refused unless a
filled small-test trade is on record. Check `GET /api/system/readiness/strategies` and enable only the
strategy whose flag is ready and whose verification you completed above. Evidence: audit log entry;
`live_stage: "full"` in system status.

## 18. Expand cautiously

Raise `max_trade_size_usd` gradually; keep auto-execute off until days of manual live trades reconcile
cleanly; leave DEX↔DEX / funding / basis / flash loans off until their own rows are verified (flash loans
additionally need a contract audit + fork tests — see `contracts/README.md`). Never deploy the emergency
or gas reserves; never run capital you cannot lose.
