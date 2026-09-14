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
| UI token | `FEDR_AUTH_TOKEN` | compared in constant time; cookie is HttpOnly + SameSite=Strict |

* Secrets never reach the frontend: the API returns masked permission flags, never keys.
  Wallet backups are exported as **passphrase-encrypted keystores** (EVM: Web3 Secret Storage v3,
  Solana: scrypt/AES-GCM JSON) — ciphertext only.
* Logs go through a redaction processor (`fedr/security/redaction.py`) that masks key-like fields and
  hex/base58 key-shaped values.
* The bot wallet key is transmitted exactly once to the **local** Gateway container (same compose
  network, no host port) so Gateway can sign swaps. It is never sent to a third party.

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
- [x] Dependencies pinned to minimum versions; `pip audit`/`npm audit` recommended before deploy
