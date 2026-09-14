# Profit Guard call graph — every path to an order

The Profit Guard is only meaningful if nothing can place an order without passing it *immediately before
submission* on *fresh* quotes. This document lists every code path that can submit an order, what gate it
passes, and the test that proves it. Evidence was produced by `grep` over `backend/fedr` and by
`backend/tests/test_profit_guard_bypass.py` (which re-runs the grep as a test so a future change that adds
an order path fails CI).

## Where decisions are made

```
OpportunityEngine.evaluate(cand, size)                   fedr/engine/opportunity.py
  ├─ pick_size()  (depth / inventory / collateral caps)
  ├─ quotes: _leg_quote() ×2  or evaluate_carry()          ← fresh venue quotes, never cached prices
  ├─ GasGuard.assess()  (per on-chain leg)                  fedr/engine/gas_guard.py
  ├─ ProfitGuard.assess(ProfitGuardInputs)                  fedr/engine/profit_guard.py
  │     gross → expected (all known costs) → worst case (stress) → required minimum → Decision
  ├─ RiskEngine.assess()                                    fedr/engine/risk_engine.py
  ├─ circuit breakers / venue health / market-data quality
  └─ Opportunity(decision = SAFE_TO_EXECUTE only if reasons == [])
```

`ProfitGuard.assess` is called from exactly one place: `OpportunityEngine._assess`
(`grep -rn "profit_guard.assess" backend/fedr` → `engine/opportunity.py:341`).

## Every order-placing path

| # | Entry point | Path to the venue | Gate before submission | Proof |
|---|---|---|---|---|
| 1 | scan loop, auto-execute (`FedrApp._scan_loop`) | `ExecutionEngine.execute(opp)` → `_submit` ×2 → `venue.place_order` / `PaperExecutor.execute_leg` | `execute()` **re-evaluates**: `opps.evaluate(candidate_for(opp), size)`; aborts unless `fresh.decision is SAFE_TO_EXECUTE`; the caller's `opp.profit` fields are discarded | `test_execution.py::test_execution_revalidates_and_aborts_when_spread_disappears`, `test_profit_guard_bypass.py::test_forged_decision_on_a_blocked_route_is_not_executed`, `::test_tampered_profit_fields_are_replaced_by_the_fresh_assessment` |
| 2 | manual execute (`POST /api/opportunities/{id}/execute`) | same as 1 (or 4 for flash loans) | the id must be a *current* scan result (`404` otherwise); shadow mode refuses; then path 1's re-evaluation | `test_profit_guard_bypass.py::test_api_cannot_execute_forged_ids_or_activate_live_from_env_alone` |
| 3 | carry open (`PositionManager.open_from_opportunity`) | `ExecutionEngine.execute(opp, allow_carry=True)` → path 1 | path 1's re-evaluation (carry inputs: doubled fees, halved worst-case funding) | `test_profit_guard_bypass.py::test_position_manager_cannot_open_a_forged_carry_opportunity`, `test_positions.py` |
| 4 | flash loan (`FlashLoanEngine.execute`) | on-chain tx via bot wallet | re-evaluates through `opportunity_engine.evaluate` (which also re-runs the local EVM simulation via `ctx.flash_loan_simulator`); aborts on any non-SAFE decision **before broadcast** | `test_execution.py::test_flash_loan_paper_path_aborts_when_unprofitable_after_refresh`, `test_profit_guard_bypass.py::test_flash_loan_engine_revalidates_before_broadcast`; the contract itself enforces `minProfit` on-chain (`test_contract_evm.py`) |
| 5 | emergency hedge (`EmergencyHedger`) | `venue.place_order` (reduce exposure after a one-leg fill) | **risk-reducing by design**: no Profit Guard; bounded by the hedge tolerance and only for an existing exposure | `test_execution.py::test_one_leg_fill_enters_recovery_and_hedges`, `::test_one_leg_fill_without_hedge_venue_trips_breaker` |
| 6 | carry close (`PositionManager.close`) | `ExecutionEngine.submit_leg` ×2 | **risk-reducing by design**: closes an existing hedged position (exit rule, liquidation distance, manual); limit prices bound slippage; incomplete close trips `POSITION_DISCREPANCY` | `test_positions.py::test_close_on_convergence_books_pnl_and_returns_collateral`, `::test_incomplete_close_trips_breaker_and_keeps_position` |
| 7 | emergency stop (`EmergencyStop.activate`) | `venue.cancel_order` only | cancels, never places | `test_api.py::test_emergency_stop_and_breakers`, `tests/integration/test_process_lifecycle.py` |

Paths 5 and 6 are the only submissions without a Profit Guard pass, and both can only *reduce* an existing
exposure; neither is reachable from the API or from a strategy (`test_profit_guard_bypass.py::
test_single_leg_submission_is_not_reachable_from_the_api_or_strategies` asserts that `submit_leg`/`_submit`
are referenced only from `executor.py` and `positions.py`, that no API module calls `place_order`, and that
`place_order` is called only from `executor.py` and `emergency_hedge.py`).

## Things that are *not* bypasses (and why)

* **Environment variables.** `FEDR_LIVE_TRADING_ALLOWED=true` alone does not enable live mode: activation
  additionally needs the readiness checklist to pass and the typed confirmation phrase, and the boot-time
  validator refuses `FEDR_DEFAULT_MODE=live` outright (`test_api.py::test_startup_refuses_live_boot_default`).
* **Settings.** `min_net_profit_usd`, `min_roi_pct` and the buffers can be lowered by the user, but not below
  the schema's floors, and the worst-case check cannot be disabled.
* **Experience engine.** Learning can only *raise* the required buffer (bounded by
  `experience_max_extra_buffer_pct`); it has no path to lower a requirement
  (`test_profit_guard.py::test_experience_extra_buffer_reduces_expected`).
* **Paper mode.** Paper legs go through the same `execute()`; only the venue call is replaced
  (`PaperExecutor.execute_leg`).

## Known limitation

The call graph is enforced by tests over the source tree, not by the type system. A new module that imports a
connector and calls `place_order` directly would be caught by `test_single_leg_submission_is_not_reachable…`
only if the test suite runs — keep it in CI.
