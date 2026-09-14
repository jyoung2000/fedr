# FlashLoanArbitrage contract

Owner-only, allowlist-restricted, atomic two-swap arbitrage funded by an **Aave V3 `flashLoanSimple`**
(premium currently 5 bps by default; the engine reads `POOL.FLASHLOAN_PREMIUM_TOTAL()` at runtime).

Safety properties (see `src/FlashLoanArbitrage.sol`):

| Property | Mechanism |
|---|---|
| Only the bot signer can start a trade | `Ownable2Step` + `onlyOwner` on `executeArbitrage` |
| Only *our* flash loan can call back | `msg.sender == POOL`, `initiator == address(this)`, transient `_executingAsset` |
| No arbitrary external calls | The contract only knows Uniswap V3 `exactInputSingle` and V2 `swapExactTokensForTokens` |
| Routers / tokens are allowlisted | `setRouter`, `setToken` (owner only) |
| Minimum outputs enforced on-chain | `minAmountOut` per leg |
| Minimum profit enforced on-chain | `InsufficientProfit` revert unless balance ≥ amount + premium + minProfit |
| Deadline | `DeadlinePassed` revert |
| Reentrancy | OpenZeppelin `ReentrancyGuard` |
| Emergency | `Pausable`, `rescueERC20` |
| Approvals | exact-amount `forceApprove`, reset to 0 after each swap |

**A reverted transaction still consumes gas.** The off-chain Profit Guard and Gas Guard account for that
(failure reserve + stress gas) and the engine always simulates (`eth_call` + `eth_estimateGas`) before broadcasting.

## Build & test (no Foundry needed)

```bash
cd contracts && npm ci            # solc-js 0.8.28 + @openzeppelin/contracts 5.4.0 (pinned)
node compile.js                   # standard-JSON compile, optimizer 200 runs, evm cancun → out/*.json
cd ../backend && pytest tests/test_contract_evm.py -q   # 12 tests on a real EVM (eth-tester / py-evm)
```

The pytest suite deploys the compiled bytecode with mock pool/router/token contracts (`test/mocks/Mocks.sol`)
and covers: profitable round-trip, `minProfit` revert, cannot-repay revert, min-out and deadline reverts,
owner-only execution, unlisted router/token rejection, callback only from the configured pool and only for
this initiator, pause, reentrancy, rescue + two-step ownership, and a gas ceiling. This verifies the
contract's *logic*. Static analysis has now been run too — slither 0.11.6 with native solc 0.8.28:
17 informational/low results, **zero high- or medium-severity findings**, triaged in
`contracts/SLITHER_REPORT.md`. It does not replace a fork test against real Aave/Uniswap deployments or
an audit.

## Fork tests (Foundry, optional)

```bash
cd contracts
forge install OpenZeppelin/openzeppelin-contracts@v5.6.1
forge build
# fork test against a real Aave/Uniswap deployment
forge test --fork-url $RPC_URL --fork-block-number <block> -vvv
```

## Deployment (never automatic)

The platform never deploys contracts. Deploy yourself, then set the address in `.env`:

```
FEDR_FLASHLOAN_CONTRACT_BASE=0x...
FEDR_FLASHLOAN_CONTRACT_ARBITRUM=0x...
```

Aave V3 Pool addresses (bgd-labs/aave-address-book, verified 2026-09):
Ethereum `0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2`, Arbitrum/Optimism/Polygon/Avalanche
`0x794a61358D6845594F94dc1DB02A252b5b4814aD`, Base `0xA238Dd80C259a72e81d7e4664a9801593F98d1c5`,
BNB `0x6807dc923806fE8Fd134338EABCA509979a7e0cB`, Sepolia `0x6Ae43d3271ff6888e7Fc43Fd7321a503ff738951`,
Base Sepolia `0x8bAB6d1b75f19e9eD9fCe8b9BD338844fF79aE27`.

After deployment allowlist the routers (e.g. Uniswap V3 SwapRouter02 `0x68b3465833fb72A70ecDF485E0e4C7bD8665Fc45`
on Ethereum/Arbitrum/Optimism/Polygon, `0x2626664c2603336E57B271c5C0b26F421741e481` on Base) and the tokens you trade.

## Status

Classification (see `docs/PRODUCTION_VERIFICATION_MATRIX.md`, row ST-05): **IMPLEMENTED + PARTIALLY VERIFIED**.
Compiles with solc 0.8.28; 12 EVM tests pass on eth-tester. **Not audited. Not deployed. Not exercised on a fork
(no RPC access). Statically analysed: slither 0.11.6, zero high/medium findings (`SLITHER_REPORT.md`).**
Mainnet use is MUST BLOCK until a fork test suite and an independent audit exist. Tests passing does not make
the contract safe; treat any deployment as at-risk capital.
