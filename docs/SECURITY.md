# Security model

FEDR is **local-first**: it runs on your machine, talks only to the exchanges/RPCs you configure,
and sends no telemetry (`FEDR_TELEMETRY_ENABLED=false` is the default and nothing implements an
"on" path). You control the API keys, wallets, capital, strategies and data.

## Secrets

| Secret | Where | Protection |
|---|---|---|
| Exchange API keys | `exchange_accounts.credentials_enc` (SQLite in `/data/database`) | AES-256-GCM with the master key; AAD = account id |
| Bot wallet private keys | `wallets.key_enc` | AES-256-GCM with the master key; AAD = wallet id |
| Master key | `FEDR_MASTER_KEY` env **or** `/data/config/master.key` (mode 0600, generated once) | never logged; fingerprint only |
| Gateway wallet keystores | `gateway/conf/wallets/**` | Gateway's own scrypt + AES-256-GCM with `GATEWAY_PASSPHRASE` |
| UI token | `FEDR_AUTH_TOKEN`, or generated into `/data/config/ui-token` (mode 0600) when unset | compared in constant time; 0.5 s delay per failure; 5 failures / 10 min lock the client address; cookie is HttpOnly + SameSite=Strict |

* Secrets never reach the frontend: the API returns masked permission flags, never keys.
  Wallet backups are exported as **passphrase-encrypted keystores** (EVM: Web3 Secret Storage v3,
  Solana: scrypt/AES-GCM JSON) — ciphertext only.
* Logs go through a redaction processor (`fedr/security/redaction.py`) that masks key-like fields and
  hex/base58 key-shaped values.
* The bot wallet key is transmitted exactly once to the **local** Gateway container (same compose
  network, no host port) so Gateway can sign swaps. It is never sent to a third party.

## Authentication is mandatory

Every `/api/*` route except `/api/system/health`, `/api/auth/status` and `/api/auth/login` requires the
token (Bearer header or the login cookie); `/health/*` are unauthenticated liveness probes that expose no
data. There is no "no token" mode: when `FEDR_AUTH_TOKEN` is unset a random token is generated once and
written to `/data/config/ui-token` with mode 0600 (`docker compose exec app cat /data/config/ui-token`).

## Withdrawals

Bot-wallet withdrawals (native and ERC-20 / SPL tokens) require: not PAPER/SIMULATION mode, an explicit
`confirm`, a destination on the allowlist (new allowlist entries wait `new_address_delay_minutes`, default 60),
a fresh quote with a known fee, and an idempotency `request_key` (a repeated key returns 409 and sends nothing).
Every request is written to the `withdrawals` ledger before broadcast. FEDR never calls an exchange `withdraw()`.
Withdrawal broadcasting has **not** been verified against a live network in this build (see the matrix).

## Exchange API keys

Required permissions: **READ on, TRADE on, WITHDRAW OFF**. The platform never calls `withdraw()` on an
exchange. Where the exchange exposes key restrictions (Binance family) the connector reads them and
the readiness checklist fails if withdrawals are enabled; elsewhere the UI asks you to confirm.

## Network exposure (Docker)

* The app publishes **only** `127.0.0.1:8935` on the host. Put a TLS reverse proxy in front if you
  need remote access, and set `FEDR_AUTH_TOKEN`.
* Gateway publishes **no host port**; it is reachable only from the app container.
* Containers run as a non-root user, `cap_drop: ALL`, `no-new-privileges`, read-only root filesystem
  (`/data` and `/tmp` are the only writable paths).
* Security headers: CSP (`default-src 'self'`), `X-Frame-Options: DENY`, `nosniff`, no-referrer.
* CORS is same-origin only unless `FEDR_ALLOWED_ORIGINS` is set. Cookie-authenticated state changes
  require the `X-Fedr-Request` header (CSRF guard) and a same-host `Origin`.

## Live trading gates (all required)

1. `FEDR_LIVE_TRADING_ALLOWED=true` in the environment (a precondition, never an activator).
2. The readiness checklist (wallet backed up, funded, exchange API connected, withdrawal disabled,
   market data healthy, guards active, breakers armed, reconciliation clean, paper completed, shadow
   reviewed, auth enabled, no emergency stop).
3. Typing the exact phrase `ACTIVATE LIVE TRADING` in the UI.
4. Live starts in **shadow mode with auto-execute off**: a further explicit toggle is needed before any
   order is submitted.
5. A `LIVE` mode found in the settings document without a valid activation is reverted to PAPER on boot.

## What is *not* protected

* Anyone with read access to `/data` **and** the master key can decrypt credentials. Protect the host.
* The Gateway passphrase protects Gateway keystores; choose a long random value.
* No multi-user model: the UI token grants full control.
* Smart-contract risk of the flash-loan contract (unaudited) — see `contracts/README.md`.

## Review checklist (performed for this release)

- [x] No secrets in frontend bundle (API returns encrypted blobs only as absent fields)
- [x] Private keys never logged (redaction processor + tests)
- [x] Seed phrases: not stored; imports accept raw keys only
- [x] Docker: non-root, cap_drop ALL, read-only FS, loopback publish only, Gateway unpublished
- [x] CORS same-origin, CSRF header on cookie sessions
- [x] No public admin endpoint; `/api/docs` only in DEBUG
- [x] Database file lives in the `/data` volume with the container user as owner
- [x] `.gitignore`/`.dockerignore` exclude `.env`, `data/`, `gateway/conf`
- [x] Dependencies pinned to minimum versions; `pip-audit` (0 known vulnerabilities) and `npm audit` (0) run on 2026-09-14
- [x] Backups exclude the master key and UI token by default; `scripts/backup.py verify` scans for plaintext keys
- [x] Login lockout (5 failures / 10 min per address) + constant-time compare + delay
- [x] Profit Guard cannot be bypassed from the API or by a forged opportunity (`docs/PROFIT_GUARD_CALL_GRAPH.md`)
