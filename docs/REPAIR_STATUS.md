# FEDR repair status

Working ledger for the audit-driven repair programme (findings F01–F25 plus anything found on the way).
"Implemented" and "verified" are tracked separately. A check that could not run is listed as **blocked**
with the reason. It never counts as a pass.

## 1. Freshness check (mandatory before any edit)

| Item | Value |
|---|---|
| Verification time | 2026-10-04T15:49Z |
| Remote | `https://github.com/jyoung2000/fedr` (`git remote -v` → origin) |
| `git ls-remote --symref origin HEAD` | `ref: refs/heads/claude/crypto-arbitrage-platform-mm9ytb  HEAD` |
| Remote heads | `claude/crypto-arbitrage-platform-mm9ytb` and `claude/fedr-audit-repairs-kir930`, both at `6576a7a69d7157f7978fe1faab452ef1507f9cf5` |
| Selected development branch | `claude/crypto-arbitrage-platform-mm9ytb` (remote default; no newer lineage exists) |
| Fetched development tip | `6576a7a69d7157f7978fe1faab452ef1507f9cf5` (2026-09-14T23:15:59Z, "final pass: slither static analysis, live-backend UI QA, clean-checkout verification") |
| Working branch | `claude/fedr-audit-repairs-kir930` (created at the same SHA) |
| Difference from audited SHA | none. The audit reviewed exactly this tip, so every finding was revalidated against it |

## 2. Baseline (before any change)

Clean venv, Python 3.11.15, pip 26.2.1, Node 22.22.0. Commands were run from the directory shown.

| Command (cwd) | Result |
|---|---|
| `pip install -e "backend[dev]"` (repo root), pip 26.2.1 | exit 0. pip 26 silently accepted the pre-release `eth-tester 0.14.0b1` |
| same spec with **pip 24.0** | **fails**: `No matching distribution found for eth-tester>=0.13` (only pre-releases exist). Reproduces the audit observation |
| `npm ci && node compile.js` (contracts) | exit 0, solc 0.8.28, 0 warnings |
| `pytest tests -q` (backend) | **201 passed, 2 failed, 5 skipped**, the same as the audit. Both failures: `bitmex` is absent from ccxt 4.5.85 (`test_registry_ids_exist_in_ccxt`, `test_ccxt_connector_builds_every_registry_exchange_offline`). The 5 skips are the opt-in external integration tests |

Installed key versions: ccxt 4.5.85, web3 7.16.0, eth-tester 0.14.0b1, py-evm 0.12.1b1, SQLAlchemy 2.1.3,
fastapi 0.142.2, pydantic 2.13.5, pytest 9.1.1, pytest-asyncio 1.4.0, ruff 0.16.10.

## 3. Findings

Status vocabulary: reproduced · source-confirmed · fixed/local-verified · externally-qualified · blocked ·
not-applicable-with-evidence.

| ID | Pri | Applicability at tip | Status | Changed files | Regression tests | External blockers |
|---|---|---|---|---|---|---|
| F01 flash-loan contract consumes existing capital | P0 | reproduced (8 new tests failed on the old bytecode) | **fixed/local-verified** | `contracts/src/FlashLoanArbitrage.sol`, `contracts/test/mocks/Mocks.sol`, `contracts/test/FlashLoanArbitrage.t.sol` | `backend/tests/test_contract_capital_preservation.py` (14 cases: 6/8/18 decimals, zero and non-zero start balance, repeated routes); Foundry tests added | Foundry not installed here, so the `.t.sol` additions were not executed. Fork tests and independent review are still required before any mainnet use |
| F19 install not reproducible | P1 | reproduced (pip 24.0) | **fixed/local-verified** | `backend/pyproject.toml`, `backend/constraints.txt`, `docker/Dockerfile`, `README.md`, `backend/fedr/connectors/cex/registry.py`, `backend/fedr/api/routes/exchanges.py` | pip 24.0 and pip 26.2.1 dry-run resolve; `test_curated_venues_missing_from_ccxt_are_reported_not_offered` | Docker image build not run here (no registry access) |

Rows for F02–F08 and the cross-cutting invariants are added as each slice lands (see §4).

## 4. Progress ledger

1. Freshness check and baseline recorded (this file).
2. F01 contract fix committed.
3. F19 dependency reproducibility committed.
