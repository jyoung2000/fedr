#!/usr/bin/env python3
"""Single source of truth for the production verification matrix.

Regenerates docs/AUDIT_REPORT.json, docs/PRODUCTION_VERIFICATION_MATRIX.md and docs/STATUS.json from the
ROWS below. Every row carries exactly one classification (the vocabulary is fixed) plus the evidence that
justifies it. Edit ROWS, run `python3 scripts/audit_matrix.py`, commit all three outputs.

Classifications:
  IMPLEMENTED + VERIFIED            exercised by automated tests / runs in this environment; evidence named
  IMPLEMENTED + PARTIALLY VERIFIED  some paths exercised, others not (the gap is named)
  IMPLEMENTED + UNVERIFIED          code exists; nothing here exercises the claim
  PARTIALLY IMPLEMENTED             a documented part of the capability is missing
  NOT IMPLEMENTED
  UNSAFE                            must not be used as-is
  MUST BLOCK                        must stay blocked until the named verification exists
  BLOCKED BY EXTERNAL DEPENDENCY    cannot be verified in this environment (no egress / no images / no keys)
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
GENERATED = time.strftime("%Y-%m-%d", time.gmtime())
VERSION = "0.2.0-rc1"

V = "IMPLEMENTED + VERIFIED"
PV = "IMPLEMENTED + PARTIALLY VERIFIED"
U = "IMPLEMENTED + UNVERIFIED"
PI = "PARTIALLY IMPLEMENTED"
NI = "NOT IMPLEMENTED"
UNSAFE = "UNSAFE"
MUST_BLOCK = "MUST BLOCK"
BLOCKED = "BLOCKED BY EXTERNAL DEPENDENCY"

# (id, area, capability, status, evidence, limitation / what is still needed, critical-for-production)
ROWS: list[tuple[str, str, str, str, str, str, bool]] = [
    # ------------------------------------------------------------------ runtime / platform
    (
        "RT-01",
        "runtime",
        "Application port fixed at 8935; startup configuration validation",
        V,
        "backend/fedr/config/env.py::validate_startup; tests/test_api.py::test_startup_refuses_live_gate_without_auth_token, ::test_startup_refuses_live_boot_default; tests/integration/test_process_lifecycle.py (real process boot)",
        "",
        True,
    ),
    (
        "RT-02",
        "runtime",
        "Docker image build from docker/Dockerfile with the official base images (python:3.11-slim-bookworm, node:22-bookworm-slim)",
        BLOCKED,
        "Registry access is denied in this environment (docker pull → 'download failed: Forbidden'). The Dockerfile was parameterised (ARG BASE_IMAGE / NODE_IMAGE / APT_PACKAGES / PY_SITE) and built unmodified against a locally imported Debian rootfs: see RT-03.",
        "Run `docker compose build` on a host with registry access and record the result in docs/PRODUCTION_VERIFICATION_REPORT.md.",
        True,
    ),
    (
        "RT-03",
        "runtime",
        "Image build + container runtime with substituted (locally imported) base images: pip install, npm ci, vite build, non-root user, healthcheck",
        PV,
        "docker/Dockerfile built unmodified with --build-arg BASE_IMAGE/NODE_IMAGE=fedr/base-local:sandbox (trimmed copy of this machine's Debian rootfs, Python 3.11.15 / Node 22.22.2): npm ci, vite build, python -m pip install ., user creation all executed; image runs as uid 10001 with the healthcheck green. docs/DOCKER_SECURITY_REPORT.md.",
        "Substitute base images are not the images users get; official images could not be pulled (registry denied).",
        True,
    ),
    (
        "RT-04",
        "runtime",
        "Compose stack up, /health, restart, down/up persistence, gateway not published",
        PV,
        "docs/DOCKER_SECURITY_REPORT.md runtime section: compose config OK for base + 4 overlays; `docker compose up -d --no-deps app` → healthy; /health/live, /health, /health/ready 200 (SYSTEM READY); 401/200 auth; UI served; emergency stop persisted across `compose restart` and `compose down && up`; SQLite WAL in the named volume.",
        "The gateway service could not run (RT-05); its isolation was proven with a stand-in container on the same private network. Repeat with the real Gateway image on a host with registry access.",
        True,
    ),
    (
        "RT-05",
        "runtime",
        "Hummingbot Gateway container (DEX middleware) runs and answers on the internal network",
        BLOCKED,
        "Image pull denied; source build fails at pnpm install (JSR registry blocked). The app boots and reports DEX venues unavailable without it: tests/integration (FEDR_GATEWAY_ENABLED=false), fedr/app.py start() gateway ping.",
        "Start `hummingbot/gateway:version-2.16.0` on a host with registry access; run tests/integration with FEDR_IT_GATEWAY_URL.",
        True,
    ),
    (
        "RT-06",
        "runtime",
        "Gateway is never published on a host port; app publishes 127.0.0.1:8935 only",
        V,
        "Static: `docker compose config` shows no `ports:` on gateway and `127.0.0.1:8935` only on app. Runtime: a container attached to `fedr_fedr-internal` with alias `gateway` answered the app (200) and was unreachable from the host (`docker port` empty, curl → no route). docs/DOCKER_SECURITY_REPORT.md.",
        "",
        True,
    ),
    (
        "RT-07",
        "runtime",
        "Container hardening: non-root uid 10001, cap_drop ALL, no-new-privileges, read-only root FS, tmpfs /tmp, /data volume",
        V,
        "docker inspect: User=fedr ReadonlyRootfs=true CapDrop=[ALL] no-new-privileges tmpfs /tmp uid 10001; inside the container: uid=10001, CapEff=0, /app read-only, /data and /tmp writable, master.key 0600. docs/DOCKER_SECURITY_REPORT.md.",
        "Verified in the sandbox daemon (overlay2, --iptables=false); re-run on the target host.",
        True,
    ),
    (
        "RT-08",
        "runtime",
        "Clean-checkout build (no dependency on untracked files)",
        PV,
        ".dockerignore excludes data/, node_modules/, dist/, .env*, backend/fedr/static; the image builds the UI itself. scripts/secret_scan.py confirms no .env / keystores are tracked.",
        "A `git clone` into an empty directory followed by `docker compose build` was not executed here (registry blocked).",
        False,
    ),
    (
        "RT-09",
        "runtime",
        "Environment profiles: .env.dev/.paper/.testnet/.live examples with safe defaults; compose overlays per mode",
        V,
        ".env.*.example; docker-compose.{dev,paper,testnet,live}.yml; `docker compose config -q` for each overlay in scripts/verify_production.py output.",
        "",
        False,
    ),
    (
        "RT-10",
        "runtime",
        "Startup fails safely on unsafe/missing critical configuration (live boot default, live-allowed without token, bad master key, unwritable data dir)",
        V,
        "backend/fedr/config/env.py::validate_startup; tests/test_api.py startup tests; observed in Docker: a root-owned bind-mounted /data made the container exit with 'FEDR_DATA_DIR /data is not writable' instead of running without persistence (docs/DOCKER_SECURITY_REPORT.md finding 4).",
        "",
        True,
    ),
    (
        "RT-11",
        "runtime",
        "Mandatory authentication for every sensitive route; generated token file 0600; CSRF header + same-host Origin on cookie sessions; login lockout",
        V,
        "fedr/api/auth.py; tests/test_api.py::test_auth_is_always_required_and_csrf_guard, ::test_login_lockout_after_repeated_failures; tests/integration/test_process_lifecycle.py::test_api_requires_token_and_csrf_header, ::test_generated_ui_token_file_is_private_when_no_token_configured",
        "",
        True,
    ),
    (
        "RT-12",
        "runtime",
        "Observability: /health, /health/live, /health/ready (per-check booleans, 503 when not ready), /api/system/metrics",
        V,
        "fedr/main.py; tests/test_api.py::test_health_endpoints_are_public_and_readiness_reflects_state; tests/integration/test_process_lifecycle.py::test_live_ready_and_public_health",
        "No Prometheus exposition format (JSON only).",
        False,
    ),
    (
        "RT-13",
        "runtime",
        "Audit log of every state change / sensitive action",
        V,
        "repo.audit calls throughout fedr/app.py and routes; tests/integration/test_process_lifecycle.py::test_database_schema_and_audit_trail",
        "",
        True,
    ),
    (
        "RT-14",
        "runtime",
        "Database: versioned migrations (schema_version, v1→v2 idempotent), WAL mode, persistence across restart",
        V,
        "fedr/db/migrations.py; tests/test_wallets_and_migrations.py (v1→v2 idempotent); integration: WAL + schema_version=2 + restart persistence",
        "Corruption recovery = restore from backup (RT-15); there is no automatic repair.",
        True,
    ),
    (
        "RT-15",
        "runtime",
        "Backup / restore / verify tooling; secrets excluded by default; plaintext-key scan",
        V,
        "scripts/backup.py; tests/test_backup_script.py (create→verify→restore into a fresh dir, tamper detection, plaintext key detection, --include-secrets manifest)",
        "Restore into a fresh *container* was exercised only via the process-level wallet restore test (WL-02), not a compose volume swap.",
        True,
    ),
    (
        "RT-16",
        "runtime",
        "Idempotency: withdrawal request_key (409 on repeat), deposit dedupe_key, one execution per scan tick",
        V,
        "tests/test_wallets_and_migrations.py (ledger idempotency, deposit dedupe); fedr/api/routes/wallets.py; fedr/app.py::_scan_loop (single execution per tick)",
        "",
        True,
    ),
    (
        "RT-17",
        "runtime",
        "UTC everywhere; clock drift vs venue server time measured every 5 min → DEGRADED ≥1 s / UNHEALTHY ≥10 s",
        V,
        "fedr/core/clock.py; tests/test_clock.py; fedr/app.py::_health_check_all",
        "Drift against a real exchange not measured here (no egress).",
        False,
    ),
    (
        "RT-18",
        "runtime",
        "Rate limiting: login lockout; Gateway client token bucket + backoff (GET only, POST never retried); ccxt built-in throttler; RATE_LIMIT breaker",
        PV,
        "tests/test_api.py::test_login_lockout_after_repeated_failures; tests/test_gateway_client_resilience.py; tests/test_breakers_all_reasons.py (RATE_LIMIT)",
        "Behaviour under real exchange rate limits unverified (no egress). No per-IP limiter on the general API (single-operator tool behind loopback).",
        False,
    ),
    (
        "RT-19",
        "runtime",
        "Secrets never reach logs, the frontend, or browser storage",
        V,
        "fedr/security/redaction.py + tests/test_security.py; API never returns key material (wallet backups are ciphertext); UI keeps passphrases in component state only (frontend/src/pages/wallets/*).",
        "",
        True,
    ),
    (
        "RT-20",
        "runtime",
        "No committed secrets (.env, keys, keystores, test API keys)",
        V,
        "scripts/secret_scan.py over `git ls-files` → OK; .gitignore covers .env, data/, gateway/conf, keystores.",
        "",
        True,
    ),
    (
        "RT-21",
        "runtime",
        "Dependency vulnerability scans",
        V,
        f"pip-audit: 0 known vulnerabilities; npm audit (frontend): 0 vulnerabilities after upgrading vite 8.3.0 / @vitejs/plugin-react 6.1.1 / react-router-dom 7.18.3 ({GENERATED}).",
        "Re-run before every release (`scripts/verify_production.py`).",
        True,
    ),
    (
        "RT-22",
        "runtime",
        "Third-party license inventory",
        V,
        "docs/DEPENDENCY_LICENSES.md",
        "",
        False,
    ),
    # ------------------------------------------------------------------ market data
    (
        "MD-01",
        "market data",
        "Market-data quality gate: future/stale timestamps, sequence regressions, duplicates, out-of-order, non-positive prices, crossed books, abnormal spread, cross-source deviation",
        V,
        "fedr/marketdata/quality.py; tests/test_market_data_quality.py",
        "",
        True,
    ),
    (
        "MD-02",
        "market data",
        "MARKET DATA UNHEALTHY stops the strategy for that venue (venue-scoped MARKET_DATA_FAILURE breaker + UNHEALTHY health) and recovers when the feed is clean",
        V,
        "fedr/app.py::_on_market_data_reject/_health_check_all; tests/test_api.py::test_market_data_rejections_trip_venue_breaker_and_recover",
        "",
        True,
    ),
    (
        "MD-03",
        "market data",
        "Live CEX order books via ccxt REST",
        BLOCKED,
        "No exchange egress here. tests/integration/test_external_environment.py::test_public_market_data_passes_quality_gate runs the real check with FEDR_IT_NETWORK=1.",
        "Run on the target host.",
        True,
    ),
    (
        "MD-04",
        "market data",
        "WebSocket feeds (ccxt.pro) with REST fallback and WEBSOCKET_FAILURE breaker",
        PV,
        "Fallback + breaker logic: tests/test_breakers_all_reasons.py; hub stats expose ws state.",
        "Never connected to a real WebSocket here.",
        False,
    ),
    (
        "MD-05",
        "market data",
        "Rapid market move detection (≥3 % within 10 s) → symbol-scoped RAPID_MARKET_MOVE breaker",
        V,
        "fedr/marketdata/hub.py::_check_rapid_move; tests/test_breakers_all_reasons.py::test_gas_spike_market_data_failure_rapid_move_and_scopes",
        "",
        False,
    ),
    # ------------------------------------------------------------------ connectors
    (
        "CX-01",
        "connectors",
        "22 CEX connectors via ccxt (market data, balances, orders, fees, precision, error mapping, sandbox flag)",
        PV,
        "Status per docs/CONNECTORS.md: SUPPORTED + TESTED (offline: ccxt metadata + tests/test_connectors_offline.py). None is SANDBOX VERIFIED or LIVE VERIFIED.",
        "BLOCKED here for any venue-facing check (no egress, no keys).",
        True,
    ),
    (
        "CX-02",
        "connectors",
        "CEX order create / cancel / fills round-trip",
        BLOCKED,
        "tests/integration/test_external_environment.py::test_cex_sandbox_balance_and_order_round_trip (needs sandbox keys; refuses to run against a live account).",
        "Run with FEDR_IT_CCXT_* + FEDR_IT_CCXT_SANDBOX=1.",
        True,
    ),
    (
        "CX-03",
        "connectors",
        "DEX venues via Hummingbot Gateway 2.16 (quotes, quoteId execution, wallets, pools)",
        BLOCKED,
        "Client verified offline: tests/test_connectors_offline.py, tests/test_gateway_client_resilience.py (throttle/backoff). Gateway itself cannot run here (RT-05).",
        "Run tests/integration with FEDR_IT_GATEWAY_URL.",
        True,
    ),
    (
        "CX-04",
        "connectors",
        "Chains / RPC: gas oracle, deposits, withdrawals, flash-loan broadcast",
        BLOCKED,
        "Gas oracle + RPC_FAILURE breaker tested with synthetic data; nothing reached a real RPC here.",
        "Run with FEDR_RPC_* on the target host; testnet first.",
        True,
    ),
    # ------------------------------------------------------------------ wallets
    (
        "WL-01",
        "wallets",
        "Isolated bot wallets (EVM + Solana), keys encrypted at rest (AES-256-GCM, AAD = wallet id)",
        V,
        "fedr/wallets/manager.py; tests/test_security.py; tests/test_api.py::test_wallets_create_backup_deposit",
        "",
        True,
    ),
    (
        "WL-02",
        "wallets",
        "Encrypted backup export (EVM v3 / Solana scrypt+AES-GCM) → import → restore into a fresh installation with address verification",
        V,
        "fedr/wallets/keystore.py; POST /api/wallets/bot/import; tests/test_wallets_and_migrations.py (export/restore, tamper + wrong passphrase fail closed); tests/integration/test_wallet_process.py::test_backup_restores_into_fresh_installation (second process, fresh data dir)",
        "",
        True,
    ),
    (
        "WL-03",
        "wallets",
        "Deposit detection with deduplication and a persisted deposit ledger",
        PV,
        "fedr/wallets/transfers.py::DepositMonitor; tests/test_wallets_and_migrations.py (dedupe); ledger table + /api/wallets listing.",
        "Never observed a real on-chain deposit (BLOCKED here).",
        True,
    ),
    (
        "WL-04",
        "wallets",
        "Withdrawals: native + ERC-20 + SPL transfer construction, destination validation, allowlist with new-address delay, explicit confirm, idempotent request_key, withdrawal ledger, never in paper/simulation",
        PV,
        "fedr/wallets/transfers.py::WithdrawalService; tests/test_wallets_and_migrations.py (tx construction incl. ERC-20 selector, quote refusals); tests/integration/test_wallet_process.py (mode gate); API allowlist/delay/409 paths.",
        "Broadcasting on a real network (EVM or Solana) has NOT been verified; token withdrawals are therefore not claimed as working end-to-end.",
        True,
    ),
    (
        "WL-05",
        "wallets",
        "Wallet accounting: inventory lines, reservations, gas reserves, collateral buckets, paper ledger conservation",
        V,
        "tests/test_ledger_inventory_experience.py; tests/test_positions.py (ledger conservation on a full round trip)",
        "",
        True,
    ),
    (
        "WL-06",
        "wallets",
        "External wallets (MetaMask / Phantom) for funding only",
        U,
        "Code path present (fedr/api/routes/wallets.py external wallets); no browser-wallet automation in QA.",
        "Manual test with a real browser extension required.",
        False,
    ),
    # ------------------------------------------------------------------ modes
    (
        "MO-01",
        "modes",
        "SIMULATION end-to-end as a real process: opportunities → paper execution → reconciled trades → P&L, restart persistence",
        V,
        "tests/integration/test_process_lifecycle.py, test_paper_shadow_execution.py",
        "",
        True,
    ),
    (
        "MO-02",
        "modes",
        "PAPER with real public market data",
        BLOCKED,
        "Paper executor fully exercised offline (tests/test_execution.py, tests/test_adversarial_matrix.py); the real-data half needs egress.",
        "Run the stack on the target host in PAPER and compare against docs/PRODUCTION_VERIFICATION_REPORT.md.",
        True,
    ),
    (
        "MO-03",
        "modes",
        "SHADOW: evaluates and records, never submits",
        V,
        "tests/test_execution.py::test_shadow_mode_records_but_never_submits; tests/integration/test_paper_shadow_execution.py::test_shadow_mode_records_but_submits_nothing",
        "",
        True,
    ),
    (
        "MO-04",
        "modes",
        "TESTNET (exchange sandboxes, Sepolia, Solana devnet)",
        BLOCKED,
        "tests/integration/test_external_environment.py::test_testnet_rpc_and_funded_wallet is gated on FEDR_IT_TESTNET=1.",
        "Run on the target host with test funds.",
        True,
    ),
    (
        "MO-05",
        "modes",
        "LIVE gate: env flag alone cannot enable live; readiness checklist + typed phrase; starts in shadow with auto-execute off; boot never defaults to live",
        V,
        "tests/test_api.py::test_settings_patch_profiles_and_live_guard, startup tests; tests/test_profit_guard_bypass.py::test_api_cannot_execute_forged_ids_or_activate_live_from_env_alone; fedr/engine/readiness.py",
        "",
        True,
    ),
    (
        "MO-06",
        "modes",
        "LIVE trading with real funds",
        MUST_BLOCK,
        "Never executed anywhere. Prerequisites still outstanding: CX-02, CX-03, CX-04, MD-03, MO-02, MO-04, ST-05 (contract audit).",
        "Stays blocked until the operator completes sandbox → testnet → paper → shadow verification on their deployment.",
        True,
    ),
    # ------------------------------------------------------------------ strategies
    (
        "ST-01",
        "strategies",
        "CEX↔CEX paired execution (IOC, partial fills, one-leg recovery, hedge)",
        V,
        "tests/test_execution.py; tests/test_adversarial_matrix.py",
        "Real venues: BLOCKED.",
        True,
    ),
    (
        "ST-02",
        "strategies",
        "CEX↔DEX (Gateway quoteId flow, gas-aware)",
        PV,
        "Paper path with a synthetic DEX: tests/test_execution.py (quote expiry, venue unavailable, gas spike), tests/test_adversarial_matrix.py.",
        "Real Gateway execution BLOCKED (RT-05).",
        True,
    ),
    (
        "ST-03",
        "strategies",
        "DEX↔DEX",
        U,
        "Strategy catalog + evaluation code exist; no test exercises a two-DEX route.",
        "Add a synthetic two-DEX harness or verify on testnet.",
        False,
    ),
    (
        "ST-04",
        "strategies",
        "Carry (spot↔perp, funding, basis): entry through the paired executor, position tracking, funding accrual, exit rules (convergence, funding flip, max hold, liquidation distance, basis widening), forced/manual close, incomplete-close breaker, restart reload, reconciliation vs venue positions, funding-anomaly guard",
        V,
        "fedr/engine/positions.py; tests/test_positions.py (9 tests incl. ledger conservation); tests/test_breakers_all_reasons.py (FUNDING_ANOMALY); API /api/trading/positions.",
        "Paper-verified only. A live derivatives venue (margin mode, funding settlement, liquidation mechanics) has NOT been verified → not production-ready for real perps.",
        True,
    ),
    (
        "ST-05",
        "strategies",
        "Flash-loan contract: compiles (solc 0.8.28, OpenZeppelin 5.4.0), EVM tests (profit, min-profit revert, cannot-repay, min-out, deadline, non-owner, unlisted router/token, callback-only-pool + foreign initiator, pause, reentrancy, rescue, 2-step ownership, gas)",
        PV,
        "contracts/compile.js; tests/test_contract_evm.py (12 tests on eth-tester with real bytecode); engine aborts before broadcast unless the local EVM simulation and Profit Guard pass (tests/test_execution.py).",
        "NOT audited; slither (static analysis) and foundry fork tests could not be run (binaries unreachable). Mainnet use is MUST BLOCK until an audit + fork tests exist.",
        True,
    ),
    (
        "ST-06",
        "strategies",
        "Rebalancing: cost comparison per method (transfer / bridge / swap); recommendations only, bridging never automated, no automatic capital moves",
        V,
        "fedr/engine/rebalancer.py; tests/test_positions.py::test_rebalancer_compares_methods_and_never_automates_bridging",
        "",
        True,
    ),
    # ------------------------------------------------------------------ guards
    (
        "GD-01",
        "guards",
        "Profit Guard: gross / expected / worst-case with full cost attribution and no double counting; unknown fee blocks",
        V,
        "fedr/engine/profit_guard.py; tests/test_profit_guard.py (23 tests incl. acceptance scenarios)",
        "",
        True,
    ),
    (
        "GD-02",
        "guards",
        "Profit Guard cannot be bypassed: every order path re-evaluates on fresh quotes; forged opportunities rejected; single-leg submission unreachable from the API",
        V,
        "docs/PROFIT_GUARD_CALL_GRAPH.md; tests/test_profit_guard_bypass.py (6 tests)",
        "",
        True,
    ),
    (
        "GD-03",
        "guards",
        "Gas Guard regimes (normal/elevated/extreme) raise required profit or block on-chain routes",
        V,
        "tests/test_profit_guard.py gas tests; tests/test_execution.py::test_gas_spike_blocks_on_chain_route_but_not_cex_route",
        "",
        True,
    ),
    (
        "GD-04",
        "guards",
        "Risk Engine: capital usage, per-trade/asset/venue exposure, concurrent trades, daily loss",
        V,
        "tests/test_guards_and_risk.py; tests/test_execution.py::test_max_concurrent_trades_respected",
        "",
        True,
    ),
    (
        "GD-05",
        "guards",
        "Circuit breakers: all 19 reasons wired to a real trigger and tested end-to-end; scopes (global / venue / chain / strategy / symbol) block the opportunity engine; persistence + explicit reset",
        V,
        "tests/test_breakers_all_reasons.py (incl. test_every_reason_is_wired_to_a_trigger); tests/test_execution.py; tests/test_api.py::test_emergency_stop_and_breakers",
        "",
        True,
    ),
    (
        "GD-06",
        "guards",
        "Emergency stop: blocks execution, cancels cancellable orders, persists across restart, explicit release",
        V,
        "tests/test_execution.py::test_emergency_stop_blocks_execution_and_persists; tests/integration/test_process_lifecycle.py::test_emergency_stop_persists_across_restart",
        "",
        True,
    ),
    (
        "GD-07",
        "guards",
        "Emergency hedge on one-leg / partial fills",
        V,
        "tests/test_execution.py::test_one_leg_fill_enters_recovery_and_hedges, ::test_partial_fill_is_hedged_and_recorded",
        "Real venues: BLOCKED.",
        True,
    ),
    (
        "GD-08",
        "guards",
        "Reconciliation: balances vs expected (BALANCE_DISCREPANCY), perp positions vs tracked (POSITION_DISCREPANCY)",
        V,
        "tests/test_execution.py::test_reconciliation_pauses_on_discrepancy; tests/test_positions.py::test_positions_survive_restart_and_reconcile_mismatch",
        "Against real venue balances: BLOCKED.",
        True,
    ),
    (
        "GD-09",
        "guards",
        "Experience engine: learning can only raise buffers (bounded), never lowers a requirement",
        V,
        "tests/test_profit_guard.py::test_experience_extra_buffer_reduces_expected; tests/test_ledger_inventory_experience.py",
        "",
        False,
    ),
    (
        "GD-10",
        "guards",
        "Adversarial P&L matrix: fee spike, adverse drift, thin depth, venue rejections, partial fills, gas spike × CEX-CEX / CEX-DEX; carry funding flip",
        V,
        "tests/test_adversarial_matrix.py (16 cases): every case is blocked pre-trade or ends with bounded loss / no naked exposure",
        "",
        True,
    ),
    (
        "GD-11",
        "guards",
        "P&L accounting: actual vs expected attribution, prediction error, ledger conservation",
        V,
        "fedr/engine/accounting.py; tests/test_execution.py (value delta == realized net); tests/test_positions.py",
        "",
        True,
    ),
    (
        "GD-12",
        "guards",
        "Backtesting / replay clearly labelled as simulation",
        V,
        "tests/test_api.py::test_backtest_runs_and_is_labelled",
        "",
        False,
    ),
    # ------------------------------------------------------------------ UI
    (
        "UI-01",
        "ui",
        "Health/status page (/status): readiness checklist, liveness, metrics, venue health, breakers, gas",
        V,
        "frontend/src/pages/Health.tsx; qa/ui_qa.py --mock; docs/UI_QA_REPORT.md",
        "",
        False,
    ),
    (
        "UI-02",
        "ui",
        "Positions panel: open carry positions with marks, Close / Close all with confirmation, closed history, verification note",
        PV,
        "frontend/src/pages/trading/PositionsSection.tsx; mocked QA covers rendering + keyboard + confirm; the close POST against the backend is covered by tests/test_positions.py only at API level.",
        "Close flow through a real browser against the backend not exercised.",
        False,
    ),
    (
        "UI-03",
        "ui",
        "Wallets: keystore import, token-aware withdrawal form (registry, fee asset, delay, request_key, 409 handling), deposit/withdrawal ledgers",
        PV,
        "frontend/src/pages/wallets/*; mocked QA. Backend endpoints verified separately (WL-02/WL-04).",
        "Real withdrawal 2xx path never exercised (no network).",
        False,
    ),
    (
        "UI-04",
        "ui",
        "Danger states unmistakable: PAPER / SIMULATION / TESTNET / SHADOW / LIVE (red, pulsing, 'LIVE — REAL FUNDS') / EMERGENCY STOP / FLASH LOANS ON / HIGH GAS / CONNECTOR DEGRADED / WALLET MISMATCH – text never colour-only",
        V,
        "frontend/src/lib/alerts.ts, components/StateBar.tsx, Layout.tsx; qa/ui_qa.py --mock (11 state scenarios × 4 widths); docs/UI_QA_REPORT.md",
        "",
        True,
    ),
    (
        "UI-05",
        "ui",
        "Responsive at 1280 / 1024 / 768 / 400 px with no horizontal page scroll",
        V,
        "qa/ui_qa.py --mock overflow assertions on every page and state; docs/UI_QA_REPORT.md",
        "",
        False,
    ),
    (
        "UI-06",
        "ui",
        "Keyboard / accessibility basics: tab order, visible focus, labelled inputs, Escape closes dialogs",
        PV,
        "qa/ui_qa.py --mock keyboard pass (Close button reached, Enter/Escape); labels asserted.",
        "No screen-reader or contrast-ratio tooling run.",
        False,
    ),
    (
        "UI-07",
        "ui",
        "Frontend security: CSP default-src 'self', X-Frame-Options DENY, no key material in the bundle or browser storage",
        V,
        "tests/test_api.py::test_health_status_and_security_headers; code review of frontend/src (passphrases cleared after submit; no localStorage of secrets)",
        "",
        True,
    ),
    (
        "UI-08",
        "ui",
        "UI QA against a running backend (not mocks)",
        PV,
        "Phase-1 QA ran against a live simulation backend (docs/RELEASE_REPORT.md); the phase-2 danger-state suite ran in mock mode only.",
        "Re-run `python qa/ui_qa.py http://127.0.0.1:8935` on the target host after login support is added to the script.",
        False,
    ),
    # ------------------------------------------------------------------ docs
    (
        "DOC-01",
        "docs",
        "Documentation set: README, ARCHITECTURE, OPERATIONS, SECURITY, CONNECTORS (mandatory status language), MEV, DEPENDENCY_LICENSES, PROFIT_GUARD_CALL_GRAPH, DOCKER_SECURITY_REPORT, UI_QA_REPORT, PRODUCTION_VERIFICATION_MATRIX, FINAL_PRODUCTION_REPORT, STATUS.json",
        V,
        "docs/",
        "",
        False,
    ),
]

BREAKER_REASONS = [
    "UNEXPECTED_FEES",
    "ABNORMAL_SLIPPAGE",
    "REPEATED_ORDER_FAILURE",
    "DEX_TX_FAILURE",
    "RPC_FAILURE",
    "WEBSOCKET_FAILURE",
    "MARKET_DATA_FAILURE",
    "FLASH_LOAN_FAILURE",
    "BALANCE_DISCREPANCY",
    "POSITION_DISCREPANCY",
    "PREDICTION_ERROR",
    "RAPID_MARKET_MOVE",
    "GAS_SPIKE",
    "FUNDING_ANOMALY",
    "EXCHANGE_MAINTENANCE",
    "RATE_LIMIT",
    "ONE_LEG_FILL",
    "DAILY_LOSS_LIMIT",
    "MANUAL",
]


def git_head() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=ROOT,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except Exception:
        return "unknown"


def main() -> int:
    counts = Counter(r[3] for r in ROWS)
    critical_open = [r for r in ROWS if r[6] and r[3] not in (V,)]
    blocking = [r for r in ROWS if r[3] in (MUST_BLOCK, UNSAFE, NI)]
    production_ready = False  # any critical row that is not VERIFIED keeps this false
    report = {
        "generated": GENERATED,
        "version": VERSION,
        "git": git_head(),
        "production_ready": production_ready,
        "production_ready_reason": "critical capabilities are not IMPLEMENTED + VERIFIED in this environment: "
        + ", ".join(r[0] for r in critical_open),
        "classification_vocabulary": [V, PV, U, PI, NI, UNSAFE, MUST_BLOCK, BLOCKED],
        "counts": dict(counts),
        "environment": {
            "exchange_egress": False,
            "rpc_egress": False,
            "docker_registry": False,
            "docker_daemon": "local overlay2 daemon started in the sandbox (no iptables, no bridge); base images imported from the host rootfs",
            "gateway_image": False,
            "foundry_slither": False,
        },
        "breaker_reasons": {
            "total": len(BREAKER_REASONS),
            "wired_and_tested": BREAKER_REASONS,
        },
        "rows": [
            {
                "id": r[0],
                "area": r[1],
                "capability": r[2],
                "status": r[3],
                "evidence": r[4],
                "limitation": r[5],
                "critical": r[6],
            }
            for r in ROWS
        ],
    }
    (DOCS / "AUDIT_REPORT.json").write_text(json.dumps(report, indent=2) + "\n")

    lines = [
        "# Production verification matrix",
        "",
        f"Generated {GENERATED} by `scripts/audit_matrix.py` (edit that file, not this one). Version {VERSION}, git `{report['git']}`.",
        "",
        "**production_ready = false.** " + report["production_ready_reason"] + ".",
        "",
        "Vocabulary (exactly one per row): "
        + " · ".join(f"`{v}`" for v in report["classification_vocabulary"]),
        "",
        "Environment that produced this matrix: no exchange / RPC / registry egress; local Docker daemon with imported base images; no Gateway image; no foundry/slither. "
        "Everything marked BLOCKED BY EXTERNAL DEPENDENCY has a ready-to-run check in `tests/integration/` gated on the documented environment variables.",
        "",
        "## Summary",
        "",
        "| Status | Rows |",
        "|---|---:|",
    ]
    for status in report["classification_vocabulary"]:
        lines.append(f"| {status} | {counts.get(status, 0)} |")
    lines += [
        "",
        f"Critical rows not yet VERIFIED ({len(critical_open)}): "
        + ", ".join(f"{r[0]} ({r[3]})" for r in critical_open),
        "",
    ]
    for area in (
        "runtime",
        "market data",
        "connectors",
        "wallets",
        "modes",
        "strategies",
        "guards",
        "ui",
        "docs",
    ):
        lines += [
            f"## {area}",
            "",
            "| ID | Capability | Status | Evidence | Limitation / next step | Critical |",
            "|---|---|---|---|---|---|",
        ]
        for r in ROWS:
            if r[1] != area:
                continue
            lines.append(
                f"| {r[0]} | {r[2]} | **{r[3]}** | {r[4]} | {r[5] or '—'} | {'yes' if r[6] else 'no'} |"
            )
        lines.append("")
    lines += [
        "## Circuit breaker reasons (19/19 wired and tested)",
        "",
        ", ".join(f"`{b}`" for b in BREAKER_REASONS),
        "",
        "Evidence: `tests/test_breakers_all_reasons.py` triggers each reason through its real code path (no direct `trip()` calls) and asserts that every enum member has a trigger in `backend/fedr`.",
        "",
        "## What must happen before `production_ready` can become true",
        "",
    ]
    for r in critical_open:
        lines.append(f"- **{r[0]}** {r[2]} — {r[3]}: {r[5] or r[4]}")
    (DOCS / "PRODUCTION_VERIFICATION_MATRIX.md").write_text("\n".join(lines) + "\n")

    status = {
        "name": "FEDR",
        "version": VERSION,
        "generated": GENERATED,
        "git": report["git"],
        "port": 8935,
        "production_ready": False,
        "reason": report["production_ready_reason"],
        "safe_modes_verified": ["simulation", "shadow (offline)"],
        "modes_blocked_by_environment": ["paper (real data)", "testnet"],
        "modes_must_block": ["live"],
        "counts": dict(counts),
        "critical_open": [r[0] for r in critical_open],
        "tests": {
            "unit_and_adversarial": "see docs/PRODUCTION_VERIFICATION_REPORT.md",
            "integration": "see docs/PRODUCTION_VERIFICATION_REPORT.md",
        },
        "docs": [
            "docs/PRODUCTION_VERIFICATION_MATRIX.md",
            "docs/AUDIT_REPORT.json",
            "docs/FINAL_PRODUCTION_REPORT.md",
            "docs/PRODUCTION_VERIFICATION_REPORT.md",
            "docs/DOCKER_SECURITY_REPORT.md",
            "docs/UI_QA_REPORT.md",
        ],
    }
    (DOCS / "STATUS.json").write_text(json.dumps(status, indent=2) + "\n")
    print(f"rows={len(ROWS)} counts={dict(counts)} critical_open={len(critical_open)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
