# Source projects inspected, licenses and what was reused

All four reference projects were inspected at source level (default branches, September 2026) before
implementation. Nothing was copied verbatim; patterns were re-implemented in this codebase.

| Project | Version inspected | License | How it is used |
|---|---|---|---|
| [ccxt/ccxt](https://github.com/ccxt/ccxt) | 4.5.78 (PyPI wheel + master) | MIT | **Runtime dependency.** `ccxt.pro` / `ccxt.async_support` power every CEX connector: markets, precision (TICK_SIZE), order books (REST + `watch_order_book`), balances, IOC limit orders, fills, fees (`fetch_trading_fees`), funding rates, positions, deposit addresses, sandbox mode, error hierarchy → connector health. |
| [hummingbot/gateway](https://github.com/hummingbot/gateway) | 2.16.0 (`main`) | Apache-2.0 | **Runtime service (Docker image `hummingbot/gateway:version-2.16.0`).** Preferred DEX middleware: `/connectors/{jupiter,uniswap,…}/router/quote-swap` + `execute-quote` (quoteId), AMM/CLMM quotes, `/chains/*/balances|estimate-gas|poll`, `/wallet/add`, `/config/connectors`, `/tokens`. Error codes (`TRANSACTION_TIMEOUT`, `SLIPPAGE_EXCEEDED`, `SIMULATION_FAILED`, `NO_ROUTE_FOUND`) drive the retry policy. Gateway simulates Solana transactions before sending and applies priority fees. |
| [hummingbot/hummingbot](https://github.com/hummingbot/hummingbot) | 2.16.0 (`master`) | Apache-2.0 | **Patterns only (no dependency; Cython build too heavy).** Reused ideas: `OrderBook.get_vwap_for_volume` / `get_price_for_volume` (our `OrderBook.walk`), `InFlightOrder`/`OrderState` lifecycle (our `OrderResult`), `AddedToCostTradeFee` vs `DeductedFromReturnsTradeFee` (buy fee added to cost, sell fee deducted from proceeds), in-flight balance reservation and `BudgetChecker` (inventory reservations + risk balance checks), `status_dict` readiness and `NetworkStatus` (startup health / venue health), lost-order polling (`fetch_order` retries), Gateway retry only on `TRANSACTION_TIMEOUT`, `ArbitrageExecutor` profitability = executable quotes minus fees in base (we extend with worst case, allowances, gas regimes, hedging), paper-trade wrapping a real market-data source (`PaperTradeExchange` → our `PaperExecutor` over real connectors), `TradeFill` persistence with fee-in-quote (our `fills` table). |
| [nkaz001/hftbacktest](https://github.com/nkaz001/hftbacktest) | py-hftbacktest 2.4.4 | MIT | **Concepts only.** Latency-delayed arrival (entry/response latency), IOC/FOK walking of displayed depth with per-level fills (`PartialFillExchange`), `leaves_qty`/`exec_qty` partial-fill bookkeeping, `TradingValueFeeModel` (fee = rate × notional), event stream with `exch_ts`/`local_ts` (our market/quote/decision/submit/fill timeline). Queue-position models (`PowerProbQueueModel`, `LogProbQueueModel`) were studied but not adopted because FEDR only submits taker (IOC) orders. |

Additional references (interfaces verified from source repositories, addresses from `bgd-labs/aave-address-book`,
`Uniswap/sdks`, `Uniswap/smart-order-router`, `pancakeswap/pancake-v3-contracts`, `morpho-org/sdks`):

* Aave V3 (`aave-dao/aave-v3-origin`, BUSL-1.1 for the protocol; interfaces MIT): `flashLoanSimple` /
  `IFlashLoanSimpleReceiver.executeOperation`, premium 5 bps default.
* Balancer V2 `flashLoan` (0% historically) and Morpho Blue `flashLoan` (0%) — documented as future providers;
  only Aave V3 is implemented in `contracts/src/FlashLoanArbitrage.sol`.
* Uniswap V3 SwapRouter02 `exactInputSingle`, QuoterV2, V2 `swapExactTokensForTokens` — used by the contract.
* OpenZeppelin Contracts 5.6.1 (MIT): `Ownable2Step`, `ReentrancyGuard`, `Pausable`, `SafeERC20.forceApprove`.
* Flashbots Protect / MEV Blocker / Jito docs — see `docs/MEV.md`.
* Python: web3 7.x, eth-account, solders + solana-py, cryptography, SQLAlchemy 2, FastAPI, structlog, qrcode (all
  permissive licenses: MIT/BSD/Apache).

## License compliance

* This repository is Apache-2.0.
* ccxt (MIT) is imported as a library; its license text ships with the wheel.
* Hummingbot Gateway (Apache-2.0) runs unmodified as a container; no code was copied.
* Hummingbot (Apache-2.0) and hftbacktest (MIT) inspired designs; no source was copied, so no NOTICE
  carry-over is required. Attribution is given here as a courtesy.
* OpenZeppelin (MIT) is imported by the Solidity contract via Foundry remapping; the MIT notice is preserved
  in the library.
