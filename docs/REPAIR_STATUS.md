# FEDR repair status

Working ledger for the audit-driven repair programme (findings F01–F25 plus anything found on the way).
"Implemented" and "verified" are tracked separately. A check that could not run is listed as **blocked**
with the reason. It never counts as a pass.

## 1. Freshness check (mandatory before any edit)

| Item | Value |
|---|---|
| Verification time | 2026-10-04T15:49Z (re-checked 16:41Z: unchanged) |
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
not-applicable-with-evidence. No finding is **externally-qualified** yet. That requires sandbox/testnet
evidence this environment cannot produce (see §5).

| ID | Pri | At tip | Status | Main changes | Regression tests |
|---|---|---|---|---|---|
| F01 flash-loan contract spends existing capital | P0 | reproduced (8 new cases failed on the old bytecode) | **fixed/local-verified** | `contracts/src/FlashLoanArbitrage.sol`: pre-loan balance baseline, incremental `InsufficientProfit` check and event, `intermediate != asset` | `test_contract_capital_preservation.py` (14 cases: 6/8/18 decimals, 0 and 2,000 start balance, profit then loss); `test_upgrade_and_wiring.py::test_realized_flash_profit_is_decoded_from_the_contract_event`; Foundry tests added but **not run** (no forge) |
| F02 unknown outcomes treated as failure/cancel | P0 | reproduced (the audit's open, failed-cancel, empty-status case) | **fixed/local-verified** | `OrderStatus` SUBMITTING/UNKNOWN/CANCEL_PENDING; PARTIALLY_FILLED = final only; `ccxt_connector.place_order/_cancel_and_confirm/resolve_order` (client-order-id lookup); `gateway_connector` UNKNOWN on transport loss/timeout + `resolve_tx`; executor timeout derived from connector duration | `test_order_uncertainty.py` (13); `test_execution_journal.py::test_leg_timeout_is_unknown_not_failed`, `::test_unknown_leg_keeps_reservation_quarantines_venue_and_never_hedges` |
| F03 no durable intent before submission | P0 | source-confirmed | **fixed/local-verified** | order journal (trade row + `orders` rows with client ids, status `submitting`) written inside final admission before transport; `recover_on_startup` barrier; resolution loop; operator resolution API/UI | `test_execution_journal.py::test_restart_recovers_journaled_trade_resolves_by_lookup_and_books_once`, `::test_restart_with_simulated_legs_in_flight_treats_them_as_not_executed`, `::test_nothing_is_transmitted_when_the_intent_cannot_be_journaled`, `::test_startup_barrier_blocks_admission_until_recovery_completes` |
| F04 emergency stop races submission | P0 | reproduced (stop during re-quote still filled both legs) | **fixed/local-verified** | `AdmissionGate` (lock, generation, barrier) + `EngineContext.admission_block`; synchronous linearization check before transport tasks | `test_execution_journal.py`: stop during re-quote, shadow during re-quote, stop during journal write, generation change, concurrent capacity |
| F05 reconciliation blesses lost funds | P0 | reproduced (both audit cases) | **fixed/local-verified** | `engine/reconciliation.py` rewritten on the durable `ledger_entries` table (opening checkpoint, fills, fees, gas, transfers, adjustments); union comparison; incomplete ≠ OK; deferral of busy accounts; journal-based untracked-order check; ledger-based deposit detection | `test_reconciliation_ledger.py` (11) |
| F06 carry P&L counted at entry and exit | P0 | reproduced (the old close test only asserted `today_net > 0`) | **fixed/local-verified** | entry books fees/gas only; close books lifecycle − entry booking; `ctx.book_realized` updates daily loss immediately; cumulative, idempotent partial close; paper funding credited to the ledger; live funding needs venue history; legacy positions book the remainder | `test_carry_accounting.py` (10: booked total == realized == ledger equity change, immediate daily-loss, partial close, two positions per venue, late-resolved entry, legacy remainder, estimated funding not booked) |
| F07 derivatives lack contract/close semantics | P0 | reproduced (`reduceOnly` never reached `create_order`) | **fixed/local-verified (offline)**; venue behaviour **blocked** (no sandbox) | `reduceOnly` passthrough; base↔contract conversion for orders, fills and order books; positions keyed by venue+symbol+side in base units; `verify_derivative_settings` (one-way, ≤1x, isolated) gates live/testnet carry; reduce-only close capped to the venue short; venue liquidation price used when available. Inverse contracts are filtered out (linear only) | `test_order_uncertainty.py::test_reduce_only_and_contract_size_reach_the_venue`, `::test_swap_order_book_is_converted_from_contracts_to_base_units`; `test_carry_accounting.py::test_live_carry_requires_verified_derivative_settings`, `::test_reconcile_is_keyed_by_venue_symbol_and_side` |
| F08 confirmed fills with zero value | P0 | reproduced (closed order with cost 100 → quote 0; DEX poll ignored txData) | **fixed/local-verified** | `OrderResult.quote_value` (fills → venue cost → venue average, else None); fee provenance per fill/order; accounting refuses incomplete outcomes (UNRECONCILED); DEX poll merges txData; flash settlement decoded from `ArbitrageExecuted` | `test_order_uncertainty.py::test_fill_without_trade_details_keeps_venue_cost_and_unknown_fee`, `::test_dex_poll_confirmation_uses_tx_data`, `::test_dex_confirmed_without_amounts_has_unknown_value`; `test_upgrade_and_wiring.py::test_flash_receipt_without_event_is_unreconciled_not_fabricated` |
| F19 install not reproducible | P1 | reproduced (pip 24.0) | **fixed/local-verified** | `pyproject.toml` (`eth-tester>=0.13.0b1,<0.15`, `ccxt<5`), `backend/constraints.txt`, Dockerfile, registry split into `EXCHANGES`/`UNAVAILABLE` (ccxt 4.5.85 dropped `bitmex`) | pip 24.0 + 26.2.1 resolution; `test_curated_venues_missing_from_ccxt_are_reported_not_offered`; the 2 baseline failures now pass |
| F09–F18, F20–F25 | — | **not assessed: the audit text for these findings was not included in the repair prompt received in this session** (it ended inside F08). Work that overlaps their slice themes is listed under §4; none of them is claimed as fixed | — | — |

### Additional defects found and fixed during the work

| Defect | Fix | Test |
|---|---|---|
| Breakers tripped on `strategy:carry` never blocked `spot_perp`/`funding`/`basis` evaluation (scope string mismatch) | strategy families in `CircuitBreakerManager.blocks` | covered by carry tests + admission |
| Third-asset fees (BNB, KCS…) silently replaced by a fee-schedule estimate | kept in native units, converted by accounting with a price index or reported missing | `test_order_uncertainty.py`, `test_connectors_offline.py::test_order_status_mapping_and_fee_conversion` |
| `DepositMonitor` diffed balances without trade deltas, so DEX fills were logged as deposits | deposit detection moved onto the ledger (idle accounts only) | `test_reconciliation_ledger.py::test_increase_on_idle_account_is_booked_as_an_audited_deposit` |
| Partial / hedged carry entries left the matched spot+perp pair untracked | position created for the matched size by the settlement hook | `test_carry_accounting.py::test_entry_with_unknown_leg_creates_the_position_when_resolved` |
| Paper perp close released the whole venue collateral bucket (all positions) | `settle_perp(entry_notional=…)` | `test_carry_accounting.py::test_two_positions_on_one_perp_venue_release_only_their_own_collateral` |
| Withdrawal: an error after `send_raw_transaction` was recorded `failed`; nonce from `latest` | tx hash journaled (`signed`) before broadcast, `unknown` on transport error, receipt tracking + ledger booking, shared `NonceManager` (`pending`) | `test_upgrade_and_wiring.py` (nonce, rpc-rejection) |
| Flash broadcast: no chain-id check, fabricated legs, receipt timeout raised out of the engine, burned gas never booked | chain-id + approved code hash gate, journaled hash, UNKNOWN + receipt resolver, decoded event, burned gas booked | `test_upgrade_and_wiring.py` |
| Swap order books walked in contracts as if they were base units | `_levels_in_base` | `test_swap_order_book_is_converted_from_contracts_to_base_units` |
| Reconciliation released "stale" paper reservations / judged open orders by "any trade open" | journal-based checks, unresolved trades count as open | `test_reconciliation_ledger.py` |

## 4. Invariants A–G: where each is enforced

* **A Submission and uncertainty**: client order ids are generated before admission and journaled with status
  `submitting`. Account identity is in `request.extra.account_ref`. The venue idempotency/lookup key is
  `clientOrderId`. Unknown outcomes keep reservations and capacity and quarantine the venue
  (`UNRESOLVED_ORDER`). The emergency hedge never runs on an unknown leg, and an unknown hedge stops further
  hedge attempts. Exactly-once execution is not claimed: the only accepted "never accepted" evidence is a
  repeated definitive not-found (3 lookups over ≥120 s), never a transport failure.
* **B Final admission and stopping**: `AdmissionGate` in `engine/context.py`. The spot executor, carry entry,
  carry close (risk-reducing: allowed under a stop, still journaled and barrier-checked) and flash engine use
  it. The confirmed-imbalance hedge policy is explicit: `risk.hedge_during_emergency_stop` (default true,
  bounded to three venues and the confirmed size). Withdrawals do not pass the trade admission gate. They are
  explicit user actions with confirmation, allowlist and idempotency, and an emergency stop does not block
  them, so funds can still be moved to safety.
* **C Authoritative settlement**: `engine/accounting.py`, `OrderResult.quote_value`/`fee_known`/`gas_known`.
  `book_realized` is the single P&L path. Fills are replaced per order key on every save, so replay does not
  double count.
* **D Independent reconciliation and recovery**: `engine/reconciliation.py` (ledger), `recover_on_startup`,
  `/api/system/reconciliation/adjust`, `/api/trading/trades/{id}/legs/{cid}/resolve`, re-closing a `closing`
  position.
* **E Identity, modes, network boundaries**: structural changes are refused while work is active
  (`FedrApp._assert_no_active_work`), so live positions keep their monitoring. The generation token blocks
  stale admissions. Chain id is verified before every signing path. One `NonceManager` serves all signing
  paths, and only one process may use a data directory (`fedr.lock`). Partial: token metadata for ERC-20/SPL
  withdrawals still comes from an empty `token_registry`, so token withdrawals are refused rather than guessed.
  DEX token identity is Gateway's token list (no chain/contract pinning in FEDR yet).
* **F Readiness and evidence**: mainnet flash requires `FEDR_FLASHLOAN_APPROVED_CODEHASH_<CHAIN>` matching the
  on-chain code hash. TESTNET flash is refused. Live/testnet carry requires venue-verified derivative settings.
  The existing readiness checklist and small-live-test stage were **not** reworked to per-route/per-connector
  evidence scoping in this pass (that belongs to the unreceived F12–F16 text).
* **G Migration and compatibility**: schema v3 with `VACUUM INTO` backup before upgrade. The test upgrades a
  database produced by the audited release (`tests/fixtures/schema_v2_populated.sql`) and a fresh install.
  Legacy rows are marked, not corrected.

## 5. Validation (2026-10-04, this environment)

| Check (cwd) | Result |
|---|---|
| `pytest tests -q` (backend) | **275 passed, 5 skipped**. The skips are the opt-in external tests: Docker stack, exchange public API, exchange sandbox keys, Gateway, testnet RPC |
| `ruff check fedr tests` / `ruff format --check` (backend) | clean |
| `npm ci && npm run build` (frontend: `tsc --noEmit` + vite) | pass, 0 vulnerabilities |
| `node compile.js` (contracts, solc 0.8.28) | pass, 0 warnings |
| `python qa/ui_qa.py --mock` (Playwright, 4 widths) | 744 checks, 0 failed (new `unresolved_order` scenario included) |
| pip 24.0 / 26.2.1 resolution of the dev extras | pass |
| `ruff check qa scripts` | 7 **pre-existing** findings (B023 in `qa/ui_qa.py`, F841 in `scripts/audit_matrix.py`), outside the backend lint scope, unchanged |

### Blocked here (nothing below is claimed as passed)

| Check | Why blocked | How to run |
|---|---|---|
| Foundry tests incl. the new F01 tests | `forge` not installed | `cd contracts && forge test` |
| Fork test + independent review of the fixed contract | no mainnet RPC; review is a human process | fork-test the exact deployment, then set `FEDR_FLASHLOAN_APPROVED_CODEHASH_<CHAIN>` |
| CEX sandbox fills (unknown-outcome lookup by `clientOrderId`, fee reporting, reduce-only, contract sizes, `fetchPositionMode`/`fetchLeverage` behaviour per venue) | no exchange credentials or egress | `FEDR_IT_CCXT_*` integration tests, then a small sandbox round-trip per venue |
| Gateway DEX swaps, poll `txData` shape on the deployed Gateway version | no Gateway | `FEDR_IT_GATEWAY_URL` |
| Testnet withdrawals / receipt tracking | no RPC | `FEDR_IT_TESTNET=1` |
| Docker image build with official base images | registry access | `scripts/verify_production.py --docker` |
| Two-CEX spot route qualification with restart recovery (first release target) | needs two sandbox accounts | run sandbox → shadow → small live test on one route; kill the process between journal write and fill and confirm recovery |

`docs/STATUS.json` keeps `production_ready: false`. Its GREEN rows for reconciliation, carry and execution
predate this audit. Where they disagree, this file is authoritative.

## 6. Progress ledger

| Commit | Slice |
|---|---|
| `439a077` | F01 contract |
| `996dcd1` | F19 reproducible install, ccxt registry availability, this ledger |
| `9ea3344` | F02/F03/F04/F08 journal, admission, uncertainty, authoritative values, schema v3 |
| `7b9b258` | F05 ledger reconciliation + operator APIs |
| `a53dd0d` | F06/F07 carry accounting, derivative semantics |
| `5c936f5` | flash gating/decoding, withdrawal journaling, nonces, data-dir lock, v2 upgrade test |
| `23e7e56` | UI: unresolved orders, reconciliation, legacy P&L; UI QA scenario |

**Next**: obtain the F09–F25 audit text and assess each against this branch. Then run the blocked sandbox
checks on the two-CEX spot route.
