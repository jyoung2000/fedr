# Slither static analysis — FlashLoanArbitrage.sol

Run 2026-09-14 with slither 0.11.6, native solc 0.8.28 (`solc-static-linux` from the Solidity GitHub
releases), OpenZeppelin 5.4.0 from `node_modules`:

```bash
cd contracts && pip install slither-analyzer
slither src/FlashLoanArbitrage.sol --solc "$(command -v solc)" \
  --solc-remaps "@openzeppelin/=$PWD/node_modules/@openzeppelin/"
# 13 contracts, 102 detectors → 17 results, ZERO high- or medium-severity findings
```

`scripts/verify_production.py` runs this automatically when slither + solc are installed and fails only
on high-severity findings (`--fail-high`).

## Triage of the 17 informational/low results

| Detector | Where | Disposition |
|---|---|---|
| reentrancy-benign | `executeArbitrage`: `_executingAsset = address(0)` written after `POOL.flashLoanSimple` | **By design.** The flash loan is synchronous; the guard must hold *during* the callback and is cleared after. `executeArbitrage` is `onlyOwner nonReentrant whenNotPaused`, and the callback checks `msg.sender == POOL`, `initiator == address(this)` and `asset == _executingAsset`. |
| reentrancy-events | `ArbitrageExecuted` emitted after router calls inside the pool-only callback | Accepted: events after external calls in an atomic, guard-checked callback; no state consequence. |
| timestamp | `block.timestamp > p.deadline` | **That is what a deadline is.** Miner skew is bounded and the deadline granularity is seconds. |
| redundant-statements | `assetBack;` at former line 138 | **Fixed in this pass** (return value dropped; profit measured from balance). Recompiled, 12/12 EVM tests pass. |
| missing-zero-check, dead-code, assembly, unindexed-event-address | OpenZeppelin 5.4.0 internals | Upstream library code, unmodified. |
| pragma / solc-version | OZ files declare `^0.8.20` / `>=0.4.16` ranges | Everything is *compiled* with 0.8.28, which contains none of the listed historical bugs; the ranges are OZ's own. |
| naming-convention | `POOL`, `FLASHLOAN_PREMIUM_TOTAL` | Solidity convention for immutables and Aave's interface constant. |

## What this does and does not mean

Static analysis with zero high/medium findings is **not an audit**. Still outstanding before any mainnet
use (matrix row ST-05): fork tests against real Aave V3 + router deployments (`forge test --fork-url …`,
`contracts/README.md`) and an independent security audit. Mainnet deployment remains MUST BLOCK.
