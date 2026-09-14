# Production verification matrix

Generated 2026-09-14 by `scripts/audit_matrix.py` (edit that file, not this one). Version 0.2.0-rc1, git `d8a48c8`.

**production_ready = false.** critical capabilities are not IMPLEMENTED + VERIFIED in this environment: RT-02, RT-03, RT-04, RT-05, MD-03, CX-01, CX-02, CX-03, CX-04, WL-03, WL-04, MO-02, MO-04, MO-06, ST-02, ST-05.

Vocabulary (exactly one per row): `IMPLEMENTED + VERIFIED` · `IMPLEMENTED + PARTIALLY VERIFIED` · `IMPLEMENTED + UNVERIFIED` · `PARTIALLY IMPLEMENTED` · `NOT IMPLEMENTED` · `UNSAFE` · `MUST BLOCK` · `BLOCKED BY EXTERNAL DEPENDENCY`

Environment that produced this matrix: no exchange / RPC / registry egress; local Docker daemon with imported base images; no Gateway image; no foundry/slither. Everything marked BLOCKED BY EXTERNAL DEPENDENCY has a ready-to-run check in `tests/integration/` gated on the documented environment variables.

## Summary

| Status | Rows |
|---|---:|
| IMPLEMENTED + VERIFIED | 45 |
| IMPLEMENTED + PARTIALLY VERIFIED | 14 |
| IMPLEMENTED + UNVERIFIED | 2 |
| PARTIALLY IMPLEMENTED | 0 |
| NOT IMPLEMENTED | 0 |
| UNSAFE | 0 |
| MUST BLOCK | 1 |
| BLOCKED BY EXTERNAL DEPENDENCY | 8 |

Critical rows not yet VERIFIED (16): RT-02 (BLOCKED BY EXTERNAL DEPENDENCY), RT-03 (IMPLEMENTED + PARTIALLY VERIFIED), RT-04 (IMPLEMENTED + PARTIALLY VERIFIED), RT-05 (BLOCKED BY EXTERNAL DEPENDENCY), MD-03 (BLOCKED BY EXTERNAL DEPENDENCY), CX-01 (IMPLEMENTED + PARTIALLY VERIFIED), CX-02 (BLOCKED BY EXTERNAL DEPENDENCY), CX-03 (BLOCKED BY EXTERNAL DEPENDENCY), CX-04 (BLOCKED BY EXTERNAL DEPENDENCY), WL-03 (IMPLEMENTED + PARTIALLY VERIFIED), WL-04 (IMPLEMENTED + PARTIALLY VERIFIED), MO-02 (BLOCKED BY EXTERNAL DEPENDENCY), MO-04 (BLOCKED BY EXTERNAL DEPENDENCY), MO-06 (MUST BLOCK), ST-02 (IMPLEMENTED + PARTIALLY VERIFIED), ST-05 (IMPLEMENTED + PARTIALLY VERIFIED)

## runtime

| ID | Capability | Status | Evidence | Limitation / next step | Critical |
|---|---|---|---|---|---|
| RT-01 | Application port fixed at 8935; startup configuration validation | **IMPLEMENTED + VERIFIED** | backend/fedr/config/env.py::validate_startup; tests/test_api.py::test_startup_refuses_live_gate_without_auth_token, ::test_startup_refuses_live_boot_default; tests/integration/test_process_lifecycle.py (real process boot) | — | yes |
| RT-02 | Docker image build from docker/Dockerfile with the official base images (python:3.11-slim-bookworm, node:22-bookworm-slim) | **BLOCKED BY EXTERNAL DEPENDENCY** | Registry access is denied in this environment (docker pull → 'download failed: Forbidden'). The Dockerfile was parameterised (ARG BASE_IMAGE / NODE_IMAGE / APT_PACKAGES / PY_SITE) and built unmodified against a locally imported Debian rootfs: see RT-03. | Run `docker compose build` on a host with registry access and record the result in docs/PRODUCTION_VERIFICATION_REPORT.md. | yes |
| RT-03 | Image build + container runtime with substituted (locally imported) base images: pip install, npm ci, vite build, non-root user, healthcheck | **IMPLEMENTED + PARTIALLY VERIFIED** | docker/Dockerfile built unmodified with --build-arg BASE_IMAGE/NODE_IMAGE=fedr/base-local:sandbox (trimmed copy of this machine's Debian rootfs, Python 3.11.15 / Node 22.22.2): npm ci, vite build, python -m pip install ., user creation all executed; image runs as uid 10001 with the healthcheck green. docs/DOCKER_SECURITY_REPORT.md. | Substitute base images are not the images users get; official images could not be pulled (registry denied). | yes |
| RT-04 | Compose stack up, /health, restart, down/up persistence, gateway not published | **IMPLEMENTED + PARTIALLY VERIFIED** | docs/DOCKER_SECURITY_REPORT.md runtime section: compose config OK for base + 4 overlays; `docker compose up -d --no-deps app` → healthy; /health/live, /health, /health/ready 200 (SYSTEM READY); 401/200 auth; UI served; emergency stop persisted across `compose restart` and `compose down && up`; SQLite WAL in the named volume. | The gateway service could not run (RT-05); its isolation was proven with a stand-in container on the same private network. Repeat with the real Gateway image on a host with registry access. | yes |
| RT-05 | Hummingbot Gateway container (DEX middleware) runs and answers on the internal network | **BLOCKED BY EXTERNAL DEPENDENCY** | Image pull denied; source build fails at pnpm install (JSR registry blocked). The app boots and reports DEX venues unavailable without it: tests/integration (FEDR_GATEWAY_ENABLED=false), fedr/app.py start() gateway ping. | Start `hummingbot/gateway:version-2.16.0` on a host with registry access; run tests/integration with FEDR_IT_GATEWAY_URL. | yes |
| RT-06 | Gateway is never published on a host port; app publishes 127.0.0.1:8935 only | **IMPLEMENTED + VERIFIED** | Static: `docker compose config` shows no `ports:` on gateway and `127.0.0.1:8935` only on app. Runtime: a container attached to `fedr_fedr-internal` with alias `gateway` answered the app (200) and was unreachable from the host (`docker port` empty, curl → no route). docs/DOCKER_SECURITY_REPORT.md. | — | yes |
| RT-07 | Container hardening: non-root uid 10001, cap_drop ALL, no-new-privileges, read-only root FS, tmpfs /tmp, /data volume | **IMPLEMENTED + VERIFIED** | docker inspect: User=fedr ReadonlyRootfs=true CapDrop=[ALL] no-new-privileges tmpfs /tmp uid 10001; inside the container: uid=10001, CapEff=0, /app read-only, /data and /tmp writable, master.key 0600. docs/DOCKER_SECURITY_REPORT.md. | Verified in the sandbox daemon (overlay2, --iptables=false); re-run on the target host. | yes |
| RT-08 | Clean-checkout build (no dependency on untracked files) | **IMPLEMENTED + PARTIALLY VERIFIED** | .dockerignore excludes data/, node_modules/, dist/, .env*, backend/fedr/static; the image builds the UI itself. scripts/secret_scan.py confirms no .env / keystores are tracked. | A `git clone` into an empty directory followed by `docker compose build` was not executed here (registry blocked). | no |
| RT-09 | Environment profiles: .env.dev/.paper/.testnet/.live examples with safe defaults; compose overlays per mode | **IMPLEMENTED + VERIFIED** | .env.*.example; docker-compose.{dev,paper,testnet,live}.yml; `docker compose config -q` for each overlay in scripts/verify_production.py output. | — | no |
| RT-10 | Startup fails safely on unsafe/missing critical configuration (live boot default, live-allowed without token, bad master key, unwritable data dir) | **IMPLEMENTED + VERIFIED** | backend/fedr/config/env.py::validate_startup; tests/test_api.py startup tests; observed in Docker: a root-owned bind-mounted /data made the container exit with 'FEDR_DATA_DIR /data is not writable' instead of running without persistence (docs/DOCKER_SECURITY_REPORT.md finding 4). | — | yes |
| RT-11 | Mandatory authentication for every sensitive route; generated token file 0600; CSRF header + same-host Origin on cookie sessions; login lockout | **IMPLEMENTED + VERIFIED** | fedr/api/auth.py; tests/test_api.py::test_auth_is_always_required_and_csrf_guard, ::test_login_lockout_after_repeated_failures; tests/integration/test_process_lifecycle.py::test_api_requires_token_and_csrf_header, ::test_generated_ui_token_file_is_private_when_no_token_configured | — | yes |
| RT-12 | Observability: /health, /health/live, /health/ready (per-check booleans, 503 when not ready), /api/system/metrics | **IMPLEMENTED + VERIFIED** | fedr/main.py; tests/test_api.py::test_health_endpoints_are_public_and_readiness_reflects_state; tests/integration/test_process_lifecycle.py::test_live_ready_and_public_health | No Prometheus exposition format (JSON only). | no |
| RT-13 | Audit log of every state change / sensitive action | **IMPLEMENTED + VERIFIED** | repo.audit calls throughout fedr/app.py and routes; tests/integration/test_process_lifecycle.py::test_database_schema_and_audit_trail | — | yes |
| RT-14 | Database: versioned migrations (schema_version, v1→v2 idempotent), WAL mode, persistence across restart | **IMPLEMENTED + VERIFIED** | fedr/db/migrations.py; tests/test_wallets_and_migrations.py (v1→v2 idempotent); integration: WAL + schema_version=2 + restart persistence | Corruption recovery = restore from backup (RT-15); there is no automatic repair. | yes |
| RT-15 | Backup / restore / verify tooling; secrets excluded by default; plaintext-key scan | **IMPLEMENTED + VERIFIED** | scripts/backup.py; tests/test_backup_script.py (create→verify→restore into a fresh dir, tamper detection, plaintext key detection, --include-secrets manifest) | Restore into a fresh *container* was exercised only via the process-level wallet restore test (WL-02), not a compose volume swap. | yes |
| RT-16 | Idempotency: withdrawal request_key (409 on repeat), deposit dedupe_key, one execution per scan tick | **IMPLEMENTED + VERIFIED** | tests/test_wallets_and_migrations.py (ledger idempotency, deposit dedupe); fedr/api/routes/wallets.py; fedr/app.py::_scan_loop (single execution per tick) | — | yes |
| RT-17 | UTC everywhere; clock drift vs venue server time measured every 5 min → DEGRADED ≥1 s / UNHEALTHY ≥10 s | **IMPLEMENTED + VERIFIED** | fedr/core/clock.py; tests/test_clock.py; fedr/app.py::_health_check_all | Drift against a real exchange not measured here (no egress). | no |
| RT-18 | Rate limiting: login lockout; Gateway client token bucket + backoff (GET only, POST never retried); ccxt built-in throttler; RATE_LIMIT breaker | **IMPLEMENTED + PARTIALLY VERIFIED** | tests/test_api.py::test_login_lockout_after_repeated_failures; tests/test_gateway_client_resilience.py; tests/test_breakers_all_reasons.py (RATE_LIMIT) | Behaviour under real exchange rate limits unverified (no egress). No per-IP limiter on the general API (single-operator tool behind loopback). | no |
| RT-19 | Secrets never reach logs, the frontend, or browser storage | **IMPLEMENTED + VERIFIED** | fedr/security/redaction.py + tests/test_security.py; API never returns key material (wallet backups are ciphertext); UI keeps passphrases in component state only (frontend/src/pages/wallets/*). | — | yes |
| RT-20 | No committed secrets (.env, keys, keystores, test API keys) | **IMPLEMENTED + VERIFIED** | scripts/secret_scan.py over `git ls-files` → OK; .gitignore covers .env, data/, gateway/conf, keystores. | — | yes |
| RT-21 | Dependency vulnerability scans | **IMPLEMENTED + VERIFIED** | pip-audit: 0 known vulnerabilities; npm audit (frontend): 0 vulnerabilities after upgrading vite 8.3.0 / @vitejs/plugin-react 6.1.1 / react-router-dom 7.18.3 (2026-09-14). | Re-run before every release (`scripts/verify_production.py`). | yes |
| RT-22 | Third-party license inventory | **IMPLEMENTED + VERIFIED** | docs/DEPENDENCY_LICENSES.md | — | no |

## market data

| ID | Capability | Status | Evidence | Limitation / next step | Critical |
|---|---|---|---|---|---|
| MD-01 | Market-data quality gate: future/stale timestamps, sequence regressions, duplicates, out-of-order, non-positive prices, crossed books, abnormal spread, cross-source deviation | **IMPLEMENTED + VERIFIED** | fedr/marketdata/quality.py; tests/test_market_data_quality.py | — | yes |
| MD-02 | MARKET DATA UNHEALTHY stops the strategy for that venue (venue-scoped MARKET_DATA_FAILURE breaker + UNHEALTHY health) and recovers when the feed is clean | **IMPLEMENTED + VERIFIED** | fedr/app.py::_on_market_data_reject/_health_check_all; tests/test_api.py::test_market_data_rejections_trip_venue_breaker_and_recover | — | yes |
| MD-03 | Live CEX order books via ccxt REST | **BLOCKED BY EXTERNAL DEPENDENCY** | No exchange egress here. tests/integration/test_external_environment.py::test_public_market_data_passes_quality_gate runs the real check with FEDR_IT_NETWORK=1. | Run on the target host. | yes |
| MD-04 | WebSocket feeds (ccxt.pro) with REST fallback and WEBSOCKET_FAILURE breaker | **IMPLEMENTED + PARTIALLY VERIFIED** | Fallback + breaker logic: tests/test_breakers_all_reasons.py; hub stats expose ws state. | Never connected to a real WebSocket here. | no |
| MD-05 | Rapid market move detection (≥3 % within 10 s) → symbol-scoped RAPID_MARKET_MOVE breaker | **IMPLEMENTED + VERIFIED** | fedr/marketdata/hub.py::_check_rapid_move; tests/test_breakers_all_reasons.py::test_gas_spike_market_data_failure_rapid_move_and_scopes | — | no |

## connectors

| ID | Capability | Status | Evidence | Limitation / next step | Critical |
|---|---|---|---|---|---|
| CX-01 | 22 CEX connectors via ccxt (market data, balances, orders, fees, precision, error mapping, sandbox flag) | **IMPLEMENTED + PARTIALLY VERIFIED** | Status per docs/CONNECTORS.md: SUPPORTED + TESTED (offline: ccxt metadata + tests/test_connectors_offline.py). None is SANDBOX VERIFIED or LIVE VERIFIED. | BLOCKED here for any venue-facing check (no egress, no keys). | yes |
| CX-02 | CEX order create / cancel / fills round-trip | **BLOCKED BY EXTERNAL DEPENDENCY** | tests/integration/test_external_environment.py::test_cex_sandbox_balance_and_order_round_trip (needs sandbox keys; refuses to run against a live account). | Run with FEDR_IT_CCXT_* + FEDR_IT_CCXT_SANDBOX=1. | yes |
| CX-03 | DEX venues via Hummingbot Gateway 2.16 (quotes, quoteId execution, wallets, pools) | **BLOCKED BY EXTERNAL DEPENDENCY** | Client verified offline: tests/test_connectors_offline.py, tests/test_gateway_client_resilience.py (throttle/backoff). Gateway itself cannot run here (RT-05). | Run tests/integration with FEDR_IT_GATEWAY_URL. | yes |
| CX-04 | Chains / RPC: gas oracle, deposits, withdrawals, flash-loan broadcast | **BLOCKED BY EXTERNAL DEPENDENCY** | Gas oracle + RPC_FAILURE breaker tested with synthetic data; nothing reached a real RPC here. | Run with FEDR_RPC_* on the target host; testnet first. | yes |

## wallets

| ID | Capability | Status | Evidence | Limitation / next step | Critical |
|---|---|---|---|---|---|
| WL-01 | Isolated bot wallets (EVM + Solana), keys encrypted at rest (AES-256-GCM, AAD = wallet id) | **IMPLEMENTED + VERIFIED** | fedr/wallets/manager.py; tests/test_security.py; tests/test_api.py::test_wallets_create_backup_deposit | — | yes |
| WL-02 | Encrypted backup export (EVM v3 / Solana scrypt+AES-GCM) → import → restore into a fresh installation with address verification | **IMPLEMENTED + VERIFIED** | fedr/wallets/keystore.py; POST /api/wallets/bot/import; tests/test_wallets_and_migrations.py (export/restore, tamper + wrong passphrase fail closed); tests/integration/test_wallet_process.py::test_backup_restores_into_fresh_installation (second process, fresh data dir) | — | yes |
| WL-03 | Deposit detection with deduplication and a persisted deposit ledger | **IMPLEMENTED + PARTIALLY VERIFIED** | fedr/wallets/transfers.py::DepositMonitor; tests/test_wallets_and_migrations.py (dedupe); ledger table + /api/wallets listing. | Never observed a real on-chain deposit (BLOCKED here). | yes |
| WL-04 | Withdrawals: native + ERC-20 + SPL transfer construction, destination validation, allowlist with new-address delay, explicit confirm, idempotent request_key, withdrawal ledger, never in paper/simulation | **IMPLEMENTED + PARTIALLY VERIFIED** | fedr/wallets/transfers.py::WithdrawalService; tests/test_wallets_and_migrations.py (tx construction incl. ERC-20 selector, quote refusals); tests/integration/test_wallet_process.py (mode gate); API allowlist/delay/409 paths. | Broadcasting on a real network (EVM or Solana) has NOT been verified; token withdrawals are therefore not claimed as working end-to-end. | yes |
| WL-05 | Wallet accounting: inventory lines, reservations, gas reserves, collateral buckets, paper ledger conservation | **IMPLEMENTED + VERIFIED** | tests/test_ledger_inventory_experience.py; tests/test_positions.py (ledger conservation on a full round trip) | — | yes |
| WL-06 | External wallets (MetaMask / Phantom) for funding only | **IMPLEMENTED + UNVERIFIED** | Code path present (fedr/api/routes/wallets.py external wallets); no browser-wallet automation in QA. | Manual test with a real browser extension required. | no |

## modes

| ID | Capability | Status | Evidence | Limitation / next step | Critical |
|---|---|---|---|---|---|
| MO-01 | SIMULATION end-to-end as a real process: opportunities → paper execution → reconciled trades → P&L, restart persistence | **IMPLEMENTED + VERIFIED** | tests/integration/test_process_lifecycle.py, test_paper_shadow_execution.py | — | yes |
| MO-02 | PAPER with real public market data | **BLOCKED BY EXTERNAL DEPENDENCY** | Paper executor fully exercised offline (tests/test_execution.py, tests/test_adversarial_matrix.py); the real-data half needs egress. | Run the stack on the target host in PAPER and compare against docs/PRODUCTION_VERIFICATION_REPORT.md. | yes |
| MO-03 | SHADOW: evaluates and records, never submits | **IMPLEMENTED + VERIFIED** | tests/test_execution.py::test_shadow_mode_records_but_never_submits; tests/integration/test_paper_shadow_execution.py::test_shadow_mode_records_but_submits_nothing | — | yes |
| MO-04 | TESTNET (exchange sandboxes, Sepolia, Solana devnet) | **BLOCKED BY EXTERNAL DEPENDENCY** | tests/integration/test_external_environment.py::test_testnet_rpc_and_funded_wallet is gated on FEDR_IT_TESTNET=1. | Run on the target host with test funds. | yes |
| MO-05 | LIVE gate: env flag alone cannot enable live; readiness checklist + typed phrase; starts in shadow with auto-execute off; boot never defaults to live | **IMPLEMENTED + VERIFIED** | tests/test_api.py::test_settings_patch_profiles_and_live_guard, startup tests; tests/test_profit_guard_bypass.py::test_api_cannot_execute_forged_ids_or_activate_live_from_env_alone; fedr/engine/readiness.py | — | yes |
| MO-06 | LIVE trading with real funds | **MUST BLOCK** | Never executed anywhere. Prerequisites still outstanding: CX-02, CX-03, CX-04, MD-03, MO-02, MO-04, ST-05 (contract audit). | Stays blocked until the operator completes sandbox → testnet → paper → shadow verification on their deployment. | yes |

## strategies

| ID | Capability | Status | Evidence | Limitation / next step | Critical |
|---|---|---|---|---|---|
| ST-01 | CEX↔CEX paired execution (IOC, partial fills, one-leg recovery, hedge) | **IMPLEMENTED + VERIFIED** | tests/test_execution.py; tests/test_adversarial_matrix.py | Real venues: BLOCKED. | yes |
| ST-02 | CEX↔DEX (Gateway quoteId flow, gas-aware) | **IMPLEMENTED + PARTIALLY VERIFIED** | Paper path with a synthetic DEX: tests/test_execution.py (quote expiry, venue unavailable, gas spike), tests/test_adversarial_matrix.py. | Real Gateway execution BLOCKED (RT-05). | yes |
| ST-03 | DEX↔DEX | **IMPLEMENTED + UNVERIFIED** | Strategy catalog + evaluation code exist; no test exercises a two-DEX route. | Add a synthetic two-DEX harness or verify on testnet. | no |
| ST-04 | Carry (spot↔perp, funding, basis): entry through the paired executor, position tracking, funding accrual, exit rules (convergence, funding flip, max hold, liquidation distance, basis widening), forced/manual close, incomplete-close breaker, restart reload, reconciliation vs venue positions, funding-anomaly guard | **IMPLEMENTED + VERIFIED** | fedr/engine/positions.py; tests/test_positions.py (9 tests incl. ledger conservation); tests/test_breakers_all_reasons.py (FUNDING_ANOMALY); API /api/trading/positions. | Paper-verified only. A live derivatives venue (margin mode, funding settlement, liquidation mechanics) has NOT been verified → not production-ready for real perps. | yes |
| ST-05 | Flash-loan contract: compiles (solc 0.8.28, OpenZeppelin 5.4.0), EVM tests (profit, min-profit revert, cannot-repay, min-out, deadline, non-owner, unlisted router/token, callback-only-pool + foreign initiator, pause, reentrancy, rescue, 2-step ownership, gas) | **IMPLEMENTED + PARTIALLY VERIFIED** | contracts/compile.js; tests/test_contract_evm.py (12 tests on eth-tester with real bytecode); engine aborts before broadcast unless the local EVM simulation and Profit Guard pass (tests/test_execution.py). | NOT audited; slither (static analysis) and foundry fork tests could not be run (binaries unreachable). Mainnet use is MUST BLOCK until an audit + fork tests exist. | yes |
| ST-06 | Rebalancing: cost comparison per method (transfer / bridge / swap); recommendations only, bridging never automated, no automatic capital moves | **IMPLEMENTED + VERIFIED** | fedr/engine/rebalancer.py; tests/test_positions.py::test_rebalancer_compares_methods_and_never_automates_bridging | — | yes |

## guards

| ID | Capability | Status | Evidence | Limitation / next step | Critical |
|---|---|---|---|---|---|
| GD-01 | Profit Guard: gross / expected / worst-case with full cost attribution and no double counting; unknown fee blocks | **IMPLEMENTED + VERIFIED** | fedr/engine/profit_guard.py; tests/test_profit_guard.py (23 tests incl. acceptance scenarios) | — | yes |
| GD-02 | Profit Guard cannot be bypassed: every order path re-evaluates on fresh quotes; forged opportunities rejected; single-leg submission unreachable from the API | **IMPLEMENTED + VERIFIED** | docs/PROFIT_GUARD_CALL_GRAPH.md; tests/test_profit_guard_bypass.py (6 tests) | — | yes |
| GD-03 | Gas Guard regimes (normal/elevated/extreme) raise required profit or block on-chain routes | **IMPLEMENTED + VERIFIED** | tests/test_profit_guard.py gas tests; tests/test_execution.py::test_gas_spike_blocks_on_chain_route_but_not_cex_route | — | yes |
| GD-04 | Risk Engine: capital usage, per-trade/asset/venue exposure, concurrent trades, daily loss | **IMPLEMENTED + VERIFIED** | tests/test_guards_and_risk.py; tests/test_execution.py::test_max_concurrent_trades_respected | — | yes |
| GD-05 | Circuit breakers: all 19 reasons wired to a real trigger and tested end-to-end; scopes (global / venue / chain / strategy / symbol) block the opportunity engine; persistence + explicit reset | **IMPLEMENTED + VERIFIED** | tests/test_breakers_all_reasons.py (incl. test_every_reason_is_wired_to_a_trigger); tests/test_execution.py; tests/test_api.py::test_emergency_stop_and_breakers | — | yes |
| GD-06 | Emergency stop: blocks execution, cancels cancellable orders, persists across restart, explicit release | **IMPLEMENTED + VERIFIED** | tests/test_execution.py::test_emergency_stop_blocks_execution_and_persists; tests/integration/test_process_lifecycle.py::test_emergency_stop_persists_across_restart | — | yes |
| GD-07 | Emergency hedge on one-leg / partial fills | **IMPLEMENTED + VERIFIED** | tests/test_execution.py::test_one_leg_fill_enters_recovery_and_hedges, ::test_partial_fill_is_hedged_and_recorded | Real venues: BLOCKED. | yes |
| GD-08 | Reconciliation: balances vs expected (BALANCE_DISCREPANCY), perp positions vs tracked (POSITION_DISCREPANCY) | **IMPLEMENTED + VERIFIED** | tests/test_execution.py::test_reconciliation_pauses_on_discrepancy; tests/test_positions.py::test_positions_survive_restart_and_reconcile_mismatch | Against real venue balances: BLOCKED. | yes |
| GD-09 | Experience engine: learning can only raise buffers (bounded), never lowers a requirement | **IMPLEMENTED + VERIFIED** | tests/test_profit_guard.py::test_experience_extra_buffer_reduces_expected; tests/test_ledger_inventory_experience.py | — | no |
| GD-10 | Adversarial P&L matrix: fee spike, adverse drift, thin depth, venue rejections, partial fills, gas spike × CEX-CEX / CEX-DEX; carry funding flip | **IMPLEMENTED + VERIFIED** | tests/test_adversarial_matrix.py (16 cases): every case is blocked pre-trade or ends with bounded loss / no naked exposure | — | yes |
| GD-11 | P&L accounting: actual vs expected attribution, prediction error, ledger conservation | **IMPLEMENTED + VERIFIED** | fedr/engine/accounting.py; tests/test_execution.py (value delta == realized net); tests/test_positions.py | — | yes |
| GD-12 | Backtesting / replay clearly labelled as simulation | **IMPLEMENTED + VERIFIED** | tests/test_api.py::test_backtest_runs_and_is_labelled | — | no |

## ui

| ID | Capability | Status | Evidence | Limitation / next step | Critical |
|---|---|---|---|---|---|
| UI-01 | Health/status page (/status): readiness checklist, liveness, metrics, venue health, breakers, gas | **IMPLEMENTED + VERIFIED** | frontend/src/pages/Health.tsx; qa/ui_qa.py --mock; docs/UI_QA_REPORT.md | — | no |
| UI-02 | Positions panel: open carry positions with marks, Close / Close all with confirmation, closed history, verification note | **IMPLEMENTED + PARTIALLY VERIFIED** | frontend/src/pages/trading/PositionsSection.tsx; mocked QA covers rendering + keyboard + confirm; the close POST against the backend is covered by tests/test_positions.py only at API level. | Close flow through a real browser against the backend not exercised. | no |
| UI-03 | Wallets: keystore import, token-aware withdrawal form (registry, fee asset, delay, request_key, 409 handling), deposit/withdrawal ledgers | **IMPLEMENTED + PARTIALLY VERIFIED** | frontend/src/pages/wallets/*; mocked QA. Backend endpoints verified separately (WL-02/WL-04). | Real withdrawal 2xx path never exercised (no network). | no |
| UI-04 | Danger states unmistakable: PAPER / SIMULATION / TESTNET / SHADOW / LIVE (red, pulsing, 'LIVE — REAL FUNDS') / EMERGENCY STOP / FLASH LOANS ON / HIGH GAS / CONNECTOR DEGRADED / WALLET MISMATCH – text never colour-only | **IMPLEMENTED + VERIFIED** | frontend/src/lib/alerts.ts, components/StateBar.tsx, Layout.tsx; qa/ui_qa.py --mock (11 state scenarios × 4 widths); docs/UI_QA_REPORT.md | — | yes |
| UI-05 | Responsive at 1280 / 1024 / 768 / 400 px with no horizontal page scroll | **IMPLEMENTED + VERIFIED** | qa/ui_qa.py --mock overflow assertions on every page and state; docs/UI_QA_REPORT.md | — | no |
| UI-06 | Keyboard / accessibility basics: tab order, visible focus, labelled inputs, Escape closes dialogs | **IMPLEMENTED + PARTIALLY VERIFIED** | qa/ui_qa.py --mock keyboard pass (Close button reached, Enter/Escape); labels asserted. | No screen-reader or contrast-ratio tooling run. | no |
| UI-07 | Frontend security: CSP default-src 'self', X-Frame-Options DENY, no key material in the bundle or browser storage | **IMPLEMENTED + VERIFIED** | tests/test_api.py::test_health_status_and_security_headers; code review of frontend/src (passphrases cleared after submit; no localStorage of secrets) | — | yes |
| UI-08 | UI QA against a running backend (not mocks) | **IMPLEMENTED + PARTIALLY VERIFIED** | Phase-1 QA ran against a live simulation backend (docs/RELEASE_REPORT.md); the phase-2 danger-state suite ran in mock mode only. | Re-run `python qa/ui_qa.py http://127.0.0.1:8935` on the target host after login support is added to the script. | no |

## docs

| ID | Capability | Status | Evidence | Limitation / next step | Critical |
|---|---|---|---|---|---|
| DOC-01 | Documentation set: README, ARCHITECTURE, OPERATIONS, SECURITY, CONNECTORS (mandatory status language), MEV, DEPENDENCY_LICENSES, PROFIT_GUARD_CALL_GRAPH, DOCKER_SECURITY_REPORT, UI_QA_REPORT, PRODUCTION_VERIFICATION_MATRIX, FINAL_PRODUCTION_REPORT, STATUS.json | **IMPLEMENTED + VERIFIED** | docs/ | — | no |

## Circuit breaker reasons (19/19 wired and tested)

`UNEXPECTED_FEES`, `ABNORMAL_SLIPPAGE`, `REPEATED_ORDER_FAILURE`, `DEX_TX_FAILURE`, `RPC_FAILURE`, `WEBSOCKET_FAILURE`, `MARKET_DATA_FAILURE`, `FLASH_LOAN_FAILURE`, `BALANCE_DISCREPANCY`, `POSITION_DISCREPANCY`, `PREDICTION_ERROR`, `RAPID_MARKET_MOVE`, `GAS_SPIKE`, `FUNDING_ANOMALY`, `EXCHANGE_MAINTENANCE`, `RATE_LIMIT`, `ONE_LEG_FILL`, `DAILY_LOSS_LIMIT`, `MANUAL`

Evidence: `tests/test_breakers_all_reasons.py` triggers each reason through its real code path (no direct `trip()` calls) and asserts that every enum member has a trigger in `backend/fedr`.

## What must happen before `production_ready` can become true

- **RT-02** Docker image build from docker/Dockerfile with the official base images (python:3.11-slim-bookworm, node:22-bookworm-slim) — BLOCKED BY EXTERNAL DEPENDENCY: Run `docker compose build` on a host with registry access and record the result in docs/PRODUCTION_VERIFICATION_REPORT.md.
- **RT-03** Image build + container runtime with substituted (locally imported) base images: pip install, npm ci, vite build, non-root user, healthcheck — IMPLEMENTED + PARTIALLY VERIFIED: Substitute base images are not the images users get; official images could not be pulled (registry denied).
- **RT-04** Compose stack up, /health, restart, down/up persistence, gateway not published — IMPLEMENTED + PARTIALLY VERIFIED: The gateway service could not run (RT-05); its isolation was proven with a stand-in container on the same private network. Repeat with the real Gateway image on a host with registry access.
- **RT-05** Hummingbot Gateway container (DEX middleware) runs and answers on the internal network — BLOCKED BY EXTERNAL DEPENDENCY: Start `hummingbot/gateway:version-2.16.0` on a host with registry access; run tests/integration with FEDR_IT_GATEWAY_URL.
- **MD-03** Live CEX order books via ccxt REST — BLOCKED BY EXTERNAL DEPENDENCY: Run on the target host.
- **CX-01** 22 CEX connectors via ccxt (market data, balances, orders, fees, precision, error mapping, sandbox flag) — IMPLEMENTED + PARTIALLY VERIFIED: BLOCKED here for any venue-facing check (no egress, no keys).
- **CX-02** CEX order create / cancel / fills round-trip — BLOCKED BY EXTERNAL DEPENDENCY: Run with FEDR_IT_CCXT_* + FEDR_IT_CCXT_SANDBOX=1.
- **CX-03** DEX venues via Hummingbot Gateway 2.16 (quotes, quoteId execution, wallets, pools) — BLOCKED BY EXTERNAL DEPENDENCY: Run tests/integration with FEDR_IT_GATEWAY_URL.
- **CX-04** Chains / RPC: gas oracle, deposits, withdrawals, flash-loan broadcast — BLOCKED BY EXTERNAL DEPENDENCY: Run with FEDR_RPC_* on the target host; testnet first.
- **WL-03** Deposit detection with deduplication and a persisted deposit ledger — IMPLEMENTED + PARTIALLY VERIFIED: Never observed a real on-chain deposit (BLOCKED here).
- **WL-04** Withdrawals: native + ERC-20 + SPL transfer construction, destination validation, allowlist with new-address delay, explicit confirm, idempotent request_key, withdrawal ledger, never in paper/simulation — IMPLEMENTED + PARTIALLY VERIFIED: Broadcasting on a real network (EVM or Solana) has NOT been verified; token withdrawals are therefore not claimed as working end-to-end.
- **MO-02** PAPER with real public market data — BLOCKED BY EXTERNAL DEPENDENCY: Run the stack on the target host in PAPER and compare against docs/PRODUCTION_VERIFICATION_REPORT.md.
- **MO-04** TESTNET (exchange sandboxes, Sepolia, Solana devnet) — BLOCKED BY EXTERNAL DEPENDENCY: Run on the target host with test funds.
- **MO-06** LIVE trading with real funds — MUST BLOCK: Stays blocked until the operator completes sandbox → testnet → paper → shadow verification on their deployment.
- **ST-02** CEX↔DEX (Gateway quoteId flow, gas-aware) — IMPLEMENTED + PARTIALLY VERIFIED: Real Gateway execution BLOCKED (RT-05).
- **ST-05** Flash-loan contract: compiles (solc 0.8.28, OpenZeppelin 5.4.0), EVM tests (profit, min-profit revert, cannot-repay, min-out, deadline, non-owner, unlisted router/token, callback-only-pool + foreign initiator, pause, reentrancy, rescue, 2-step ownership, gas) — IMPLEMENTED + PARTIALLY VERIFIED: NOT audited; slither (static analysis) and foundry fork tests could not be run (binaries unreachable). Mainnet use is MUST BLOCK until an audit + fork tests exist.
