# UI QA report — FEDR frontend

Run date: 2026-09-14 · Runner: `python3 qa/ui_qa.py --mock` (Python Playwright 1.62.0, sync API; Chromium 1194 from `/opt/pw-browsers`, headless) · Exit code **0**.

Build under test: `frontend/` compiled with `npx tsc --noEmit` (clean) and `npm run build` (vite 8.3.0, `dist/assets/index-DWrWPPUs.js` 377.64 kB / `index-DAYLI-4v.css` 27.22 kB), copied to `backend/fedr/static` exactly as `scripts/build-ui.sh` does. The mock mode serves `frontend/dist` from a `python -m http.server`-style `ThreadingHTTPServer` subprocess with an SPA fallback (unknown paths → `index.html`) and answers every `/api/**` and `/health/**` request from `page.route()` mocks whose shapes mirror `backend/fedr/api/routes/*.py` and `backend/fedr/main.py`.

Every line below is an assertion the script actually executed in this run; nothing is inferred. Screenshots: `qa/screenshots/` (gitignored, 80 files), machine-readable results: `qa/screenshots/report.json`.

## Summary

| Width | Checks | Pass | Fail |
|---|---:|---:|---:|
| 1280 px | 164 | 164 | 0 |
| 1024 px | 164 | 164 | 0 |
| 768 px | 164 | 164 | 0 |
| 400 px | 164 | 164 | 0 |
| **Total** | **656** | **656** | **0** |

* Unmocked API requests: **0** (every request the UI made was answered by a mock).
* Console errors: **0 unexpected**. 8 recorded lines are the browser's own `Failed to load resource … 400/409` messages for the responses the wallet-import (wrong passphrase → 400) and withdrawal (duplicate → 409) flows trigger on purpose; they are excluded from the pass/fail check and listed in `report.json` with `expected: true`.
* Keyboard pass (Trading page, fresh load, Tab until the positions **Close** button owns focus, max 60): 1280 px → 20 tabs, 1024 px → 20 tabs, 768 px → 12 tabs, 400 px → 12 tabs. At 768/400 px the sidebar is hidden and the bottom nav sits after `<main>`, hence fewer tabs.

## What each mocked state sets (real backend fields only)

| Scenario | Mocked fields | UI expected (asserted) |
|---|---|---|
| paper | `/api/system/status.mode=paper` | muted chip `PAPER · simulated balances`; no LIVE / E-stop banner |
| simulation | `mode=simulation`, `market_data_source="synthetic (SIMULATION)"` | muted chip `SIMULATION · synthetic data` |
| testnet | `mode=testnet` | amber chip `TESTNET · test assets, real testnet APIs` |
| shadow | `shadow_mode=true` | blue chip `SHADOW · recording only, nothing is submitted` |
| live | `mode=live`, `live_activated=true` | red pulsing chip `LIVE — REAL FUNDS`, full-width red `#live-banner` with pulsing dot and text `LIVE — REAL FUNDS` |
| estop | `mode=live`, `emergency_stop=true`, `emergency_stop_reason=manual`; `/health/ready` → 503 with `not_emergency_stopped=false` | red `#estop-banner` (`EMERGENCY STOP ACTIVE`, Release button) rendered **before** the LIVE banner; red chip linking to `#estop-banner`; header `E-STOP` pill; Health page shows the failing check as `fail` / `NOT READY` / `HTTP 503` |
| flash_loans | `strategies.flash_loan=true` | amber chip `FLASH LOANS ON` |
| high_gas | `gas.base.gas_price=0.0300`, `baseline=0.0100` (3.0× ≥ `GasSettings.high_multiple` 2.5) | amber chip `HIGH GAS · base 3.0× baseline` |
| connector_degraded | `/api/system/metrics.venues = {kraken: degraded, coinbase: healthy, uniswap_base: unhealthy}` | amber chips `CONNECTOR DEGRADED · kraken` and `CONNECTOR UNHEALTHY · uniswap_base` |
| wallet_mismatch | breaker `reason=balance_discrepancy` (scope kraken) | red chip `WALLET MISMATCH · <detail>` (+ existing breaker banner) |
| position_discrepancy | breaker `reason=position_discrepancy` | red chip `POSITION DISCREPANCY · <detail>` |

Feature flows (scenario `features`, paper mode, every width): Dashboard render, positions table (2 open rows incl. a `suspended` row and a liquidation-distance warning, Close per row, Close all, verbatim `verification` footer, collapsible closed history with exit reason and realized net), keyboard pass + Enter opens the close dialog + Escape closes it + focus restored, Health page (7 readiness checks with pass/fail text, liveness, process counters, venue health, breakers, gas), Wallets ledgers (deposits/withdrawals with status, asset, amount, truncated hash/address, time), Import backup (client-side JSON validation, API 400 detail shown verbatim, passphrase cleared after every attempt, restored address shown, Escape closes), token withdrawal (asset select = native + `token_registry` per chain, allowlist state with `new address — withdrawal allowed after <time>` from `added_at_ms + new_address_delay_minutes`, quote with fee asset and token/native transfer type, submit disabled when `available=false`, `request_key` sent with `confirm=true` and the CSRF header, HTTP 409 rendered as `Duplicate request — not sent twice.`). Labelled-input audits on Trading and Wallets. No horizontal page overflow on every page/state loaded.

## Results at 1280 px

| Scenario | Check | Result | Detail |
|---|---|---|---|
| paper | trading: no horizontal overflow | PASS | scrollWidth=1280 clientWidth=1280 |
| paper | state chip shows 'PAPER' | PASS |  |
| paper | state chip shows 'simulated balances' | PASS |  |
| paper | no LIVE banner when not live | PASS |  |
| paper | no E-stop banner when not stopped | PASS |  |
| paper | state chips carry text, not colour alone | PASS |  |
| paper | no console errors | PASS |  |
| simulation | trading: no horizontal overflow | PASS | scrollWidth=1280 clientWidth=1280 |
| simulation | state chip shows 'SIMULATION' | PASS |  |
| simulation | state chip shows 'synthetic data' | PASS |  |
| simulation | no LIVE banner when not live | PASS |  |
| simulation | no E-stop banner when not stopped | PASS |  |
| simulation | state chips carry text, not colour alone | PASS |  |
| simulation | no console errors | PASS |  |
| testnet | trading: no horizontal overflow | PASS | scrollWidth=1280 clientWidth=1280 |
| testnet | state chip shows 'TESTNET' | PASS |  |
| testnet | no LIVE banner when not live | PASS |  |
| testnet | no E-stop banner when not stopped | PASS |  |
| testnet | state chips carry text, not colour alone | PASS |  |
| testnet | no console errors | PASS |  |
| shadow | trading: no horizontal overflow | PASS | scrollWidth=1280 clientWidth=1280 |
| shadow | state chip shows 'SHADOW' | PASS |  |
| shadow | state chip shows 'recording only, nothing is submitted' | PASS |  |
| shadow | no LIVE banner when not live | PASS |  |
| shadow | no E-stop banner when not stopped | PASS |  |
| shadow | state chips carry text, not colour alone | PASS |  |
| shadow | no console errors | PASS |  |
| live | trading: no horizontal overflow | PASS | scrollWidth=1280 clientWidth=1280 |
| live | state chip shows 'LIVE — REAL FUNDS' | PASS |  |
| live | LIVE banner visible with text 'LIVE — REAL FUNDS' | PASS |  |
| live | LIVE banner has pulsing dot | PASS |  |
| live | LIVE banner spans full width | PASS |  |
| live | no E-stop banner when not stopped | PASS |  |
| live | state chips carry text, not colour alone | PASS |  |
| live | no console errors | PASS |  |
| estop | trading: no horizontal overflow | PASS | scrollWidth=1280 clientWidth=1280 |
| estop | state chip shows 'EMERGENCY STOP ACTIVE' | PASS |  |
| estop | state chip shows 'LIVE — REAL FUNDS' | PASS |  |
| estop | LIVE banner visible with text 'LIVE — REAL FUNDS' | PASS |  |
| estop | LIVE banner has pulsing dot | PASS |  |
| estop | LIVE banner spans full width | PASS |  |
| estop | E-stop banner visible with text 'EMERGENCY STOP ACTIVE' | PASS |  |
| estop | E-stop banner has a Release button | PASS |  |
| estop | E-stop banner precedes the LIVE banner (overrides) | PASS |  |
| estop | E-stop chip links to the banner (#estop-banner) | PASS |  |
| estop | state chips carry text, not colour alone | PASS |  |
| estop | no console errors | PASS |  |
| flash_loans | trading: no horizontal overflow | PASS | scrollWidth=1280 clientWidth=1280 |
| flash_loans | state chip shows 'FLASH LOANS ON' | PASS |  |
| flash_loans | no LIVE banner when not live | PASS |  |
| flash_loans | no E-stop banner when not stopped | PASS |  |
| flash_loans | state chips carry text, not colour alone | PASS |  |
| flash_loans | no console errors | PASS |  |
| high_gas | trading: no horizontal overflow | PASS | scrollWidth=1280 clientWidth=1280 |
| high_gas | state chip shows 'HIGH GAS' | PASS |  |
| high_gas | state chip shows 'base 3.0×' | PASS |  |
| high_gas | no LIVE banner when not live | PASS |  |
| high_gas | no E-stop banner when not stopped | PASS |  |
| high_gas | state chips carry text, not colour alone | PASS |  |
| high_gas | no console errors | PASS |  |
| connector_degraded | trading: no horizontal overflow | PASS | scrollWidth=1280 clientWidth=1280 |
| connector_degraded | state chip shows 'CONNECTOR DEGRADED' | PASS |  |
| connector_degraded | state chip shows 'kraken' | PASS |  |
| connector_degraded | state chip shows 'CONNECTOR UNHEALTHY' | PASS |  |
| connector_degraded | state chip shows 'uniswap_base' | PASS |  |
| connector_degraded | no LIVE banner when not live | PASS |  |
| connector_degraded | no E-stop banner when not stopped | PASS |  |
| connector_degraded | state chips carry text, not colour alone | PASS |  |
| connector_degraded | no console errors | PASS |  |
| wallet_mismatch | trading: no horizontal overflow | PASS | scrollWidth=1280 clientWidth=1280 |
| wallet_mismatch | state chip shows 'WALLET MISMATCH' | PASS |  |
| wallet_mismatch | no LIVE banner when not live | PASS |  |
| wallet_mismatch | no E-stop banner when not stopped | PASS |  |
| wallet_mismatch | state chips carry text, not colour alone | PASS |  |
| wallet_mismatch | no console errors | PASS |  |
| position_discrepancy | trading: no horizontal overflow | PASS | scrollWidth=1280 clientWidth=1280 |
| position_discrepancy | state chip shows 'POSITION DISCREPANCY' | PASS |  |
| position_discrepancy | no LIVE banner when not live | PASS |  |
| position_discrepancy | no E-stop banner when not stopped | PASS |  |
| position_discrepancy | state chips carry text, not colour alone | PASS |  |
| position_discrepancy | no console errors | PASS |  |
| features | dashboard: no horizontal overflow | PASS | scrollWidth=1280 clientWidth=1280 |
| features | positions: 2 open rows rendered | PASS |  |
| features | positions: shows 'ETH/USDC' | PASS |  |
| features | positions: shows 'kraken' | PASS |  |
| features | positions: shows 'binance' | PASS |  |
| features | positions: shows '+0.500%' | PASS |  |
| features | positions: shows '+0.199%' | PASS |  |
| features | positions: shows '+$6.85' | PASS |  |
| features | positions: shows '+$4.20' | PASS |  |
| features | positions: shows '89.9%' | PASS |  |
| features | positions: shows '6.5 h' | PASS |  |
| features | positions: shows 'SUSPENDED' | PASS |  |
| features | positions: shows 'below 25% minimum' | PASS |  |
| features | positions: Close button per row | PASS |  |
| features | positions: Close all button enabled | PASS |  |
| features | positions: verification note shown verbatim | PASS |  |
| features | positions: closed history shows exit reason and realized net | PASS |  |
| features | trading (history expanded): no horizontal overflow | PASS | scrollWidth=1280 clientWidth=1280 |
| features | trading: every visible input is labelled | PASS |  |
| features | keyboard: Close button reached by Tab (<=60) | PASS | tabs=20 last={'testid': 'position-close', 'tag': 'BUTTON', 'text': 'Close', 'focusVisible' |
| features | keyboard: focused Close button shows a visible focus ring | PASS |  |
| features | keyboard: Enter opens the close confirmation dialog | PASS |  |
| features | keyboard: Escape closes the dialog | PASS |  |
| features | keyboard: focus returns to the Close button after Escape | PASS |  |
| features | positions: no close POST sent without confirmation | PASS |  |
| features | health: no horizontal overflow | PASS | scrollWidth=1280 clientWidth=1280 |
| features | health: 7 readiness checks listed | PASS |  |
| features | health: readiness items carry pass/fail text | PASS |  |
| features | health: READY pill shown | PASS |  |
| features | health: counters show 'Evaluated' | PASS |  |
| features | health: counters show '12,345' | PASS |  |
| features | health: counters show 'Executable' | PASS |  |
| features | health: counters show 'Blocked' | PASS |  |
| features | health: counters show 'Started' | PASS |  |
| features | health: counters show 'Filled' | PASS |  |
| features | health: counters show 'Failed' | PASS |  |
| features | health: counters show 'Aborted' | PASS |  |
| features | health: counters show 'Hedged' | PASS |  |
| features | health: counters show 'Gross' | PASS |  |
| features | health: counters show 'Fees' | PASS |  |
| features | health: counters show 'Gas' | PASS |  |
| features | health: counters show 'Net' | PASS |  |
| features | health: counters show '+$128.10' | PASS |  |
| features | health: counters show 'Prediction error' | PASS |  |
| features | health: counters show 'Breaker trips' | PASS |  |
| features | health: counters show 'Market data updates' | PASS |  |
| features | health: counters show '987,654' | PASS |  |
| features | health: counters show 'Market data rejections' | PASS |  |
| features | health: counters show 'Connector errors' | PASS |  |
| features | health: venue list names venues with health text | PASS |  |
| features | health: liveness ALIVE + breakers NONE TRIPPED + gas listed | PASS |  |
| features | wallets: no horizontal overflow | PASS | scrollWidth=1280 clientWidth=1280 |
| features | wallets: every visible input is labelled | PASS |  |
| features | wallets: ledgers show 'Deposits' | PASS |  |
| features | wallets: ledgers show 'Withdrawals' | PASS |  |
| features | wallets: ledgers show 'CONFIRMED' | PASS |  |
| features | wallets: ledgers show 'PENDING' | PASS |  |
| features | wallets: ledgers show 'BROADCAST' | PASS |  |
| features | wallets: ledgers show 'FAILED' | PASS |  |
| features | wallets: ledgers show '0xabcdef…89abcd' | PASS |  |
| features | wallets: ledgers show '0x111111…111111' | PASS |  |
| features | wallets: ledgers show 'insufficient funds for gas' | PASS |  |
| features | import: invalid JSON rejected client-side | PASS |  |
| features | import: passphrase cleared after invalid JSON | PASS |  |
| features | import: API 400 detail shown verbatim | PASS |  |
| features | import: passphrase field cleared after API error | PASS |  |
| features | import: restored address shown | PASS |  |
| features | import: POST body has keystore object, passphrase, label, mode + CSRF header | PASS |  |
| features | import: Escape closes the dialog | PASS |  |
| features | withdraw: asset select = native + token registry | PASS |  |
| features | withdraw: asset select follows the chain (solana) | PASS |  |
| features | withdraw: new allowlisted address shows 'allowed after <time>' | PASS |  |
| features | withdraw: quote shows token transfer + fee asset | PASS |  |
| features | withdraw: unavailable reason shown and submit disabled | PASS |  |
| features | withdraw: submit enabled once available + acknowledged | PASS |  |
| features | withdraw: HTTP 409 rendered as duplicate request | PASS |  |
| features | withdraw: client request_key sent (uuid-like) with confirm + CSRF header | PASS |  |
| features | wallets (withdraw modal open): no horizontal overflow | PASS | scrollWidth=1280 clientWidth=1280 |
| features | withdraw: Escape closes the dialog | PASS |  |
| features | features: no console errors (browser lines for the mocked 400/409 responses excluded) | PASS | 2 expected resource-load lines for mocked 400/409 |
| estop | health: failing readiness check reads 'fail' (text, not colour) | PASS |  |
| estop | health: NOT READY (HTTP 503) shown | PASS |  |
| estop | health: no horizontal overflow | PASS | scrollWidth=1280 clientWidth=1280 |

## Results at 1024 px

| Scenario | Check | Result | Detail |
|---|---|---|---|
| paper | trading: no horizontal overflow | PASS | scrollWidth=1024 clientWidth=1024 |
| paper | state chip shows 'PAPER' | PASS |  |
| paper | state chip shows 'simulated balances' | PASS |  |
| paper | no LIVE banner when not live | PASS |  |
| paper | no E-stop banner when not stopped | PASS |  |
| paper | state chips carry text, not colour alone | PASS |  |
| paper | no console errors | PASS |  |
| simulation | trading: no horizontal overflow | PASS | scrollWidth=1024 clientWidth=1024 |
| simulation | state chip shows 'SIMULATION' | PASS |  |
| simulation | state chip shows 'synthetic data' | PASS |  |
| simulation | no LIVE banner when not live | PASS |  |
| simulation | no E-stop banner when not stopped | PASS |  |
| simulation | state chips carry text, not colour alone | PASS |  |
| simulation | no console errors | PASS |  |
| testnet | trading: no horizontal overflow | PASS | scrollWidth=1024 clientWidth=1024 |
| testnet | state chip shows 'TESTNET' | PASS |  |
| testnet | no LIVE banner when not live | PASS |  |
| testnet | no E-stop banner when not stopped | PASS |  |
| testnet | state chips carry text, not colour alone | PASS |  |
| testnet | no console errors | PASS |  |
| shadow | trading: no horizontal overflow | PASS | scrollWidth=1024 clientWidth=1024 |
| shadow | state chip shows 'SHADOW' | PASS |  |
| shadow | state chip shows 'recording only, nothing is submitted' | PASS |  |
| shadow | no LIVE banner when not live | PASS |  |
| shadow | no E-stop banner when not stopped | PASS |  |
| shadow | state chips carry text, not colour alone | PASS |  |
| shadow | no console errors | PASS |  |
| live | trading: no horizontal overflow | PASS | scrollWidth=1024 clientWidth=1024 |
| live | state chip shows 'LIVE — REAL FUNDS' | PASS |  |
| live | LIVE banner visible with text 'LIVE — REAL FUNDS' | PASS |  |
| live | LIVE banner has pulsing dot | PASS |  |
| live | LIVE banner spans full width | PASS |  |
| live | no E-stop banner when not stopped | PASS |  |
| live | state chips carry text, not colour alone | PASS |  |
| live | no console errors | PASS |  |
| estop | trading: no horizontal overflow | PASS | scrollWidth=1024 clientWidth=1024 |
| estop | state chip shows 'EMERGENCY STOP ACTIVE' | PASS |  |
| estop | state chip shows 'LIVE — REAL FUNDS' | PASS |  |
| estop | LIVE banner visible with text 'LIVE — REAL FUNDS' | PASS |  |
| estop | LIVE banner has pulsing dot | PASS |  |
| estop | LIVE banner spans full width | PASS |  |
| estop | E-stop banner visible with text 'EMERGENCY STOP ACTIVE' | PASS |  |
| estop | E-stop banner has a Release button | PASS |  |
| estop | E-stop banner precedes the LIVE banner (overrides) | PASS |  |
| estop | E-stop chip links to the banner (#estop-banner) | PASS |  |
| estop | state chips carry text, not colour alone | PASS |  |
| estop | no console errors | PASS |  |
| flash_loans | trading: no horizontal overflow | PASS | scrollWidth=1024 clientWidth=1024 |
| flash_loans | state chip shows 'FLASH LOANS ON' | PASS |  |
| flash_loans | no LIVE banner when not live | PASS |  |
| flash_loans | no E-stop banner when not stopped | PASS |  |
| flash_loans | state chips carry text, not colour alone | PASS |  |
| flash_loans | no console errors | PASS |  |
| high_gas | trading: no horizontal overflow | PASS | scrollWidth=1024 clientWidth=1024 |
| high_gas | state chip shows 'HIGH GAS' | PASS |  |
| high_gas | state chip shows 'base 3.0×' | PASS |  |
| high_gas | no LIVE banner when not live | PASS |  |
| high_gas | no E-stop banner when not stopped | PASS |  |
| high_gas | state chips carry text, not colour alone | PASS |  |
| high_gas | no console errors | PASS |  |
| connector_degraded | trading: no horizontal overflow | PASS | scrollWidth=1024 clientWidth=1024 |
| connector_degraded | state chip shows 'CONNECTOR DEGRADED' | PASS |  |
| connector_degraded | state chip shows 'kraken' | PASS |  |
| connector_degraded | state chip shows 'CONNECTOR UNHEALTHY' | PASS |  |
| connector_degraded | state chip shows 'uniswap_base' | PASS |  |
| connector_degraded | no LIVE banner when not live | PASS |  |
| connector_degraded | no E-stop banner when not stopped | PASS |  |
| connector_degraded | state chips carry text, not colour alone | PASS |  |
| connector_degraded | no console errors | PASS |  |
| wallet_mismatch | trading: no horizontal overflow | PASS | scrollWidth=1024 clientWidth=1024 |
| wallet_mismatch | state chip shows 'WALLET MISMATCH' | PASS |  |
| wallet_mismatch | no LIVE banner when not live | PASS |  |
| wallet_mismatch | no E-stop banner when not stopped | PASS |  |
| wallet_mismatch | state chips carry text, not colour alone | PASS |  |
| wallet_mismatch | no console errors | PASS |  |
| position_discrepancy | trading: no horizontal overflow | PASS | scrollWidth=1024 clientWidth=1024 |
| position_discrepancy | state chip shows 'POSITION DISCREPANCY' | PASS |  |
| position_discrepancy | no LIVE banner when not live | PASS |  |
| position_discrepancy | no E-stop banner when not stopped | PASS |  |
| position_discrepancy | state chips carry text, not colour alone | PASS |  |
| position_discrepancy | no console errors | PASS |  |
| features | dashboard: no horizontal overflow | PASS | scrollWidth=1024 clientWidth=1024 |
| features | positions: 2 open rows rendered | PASS |  |
| features | positions: shows 'ETH/USDC' | PASS |  |
| features | positions: shows 'kraken' | PASS |  |
| features | positions: shows 'binance' | PASS |  |
| features | positions: shows '+0.500%' | PASS |  |
| features | positions: shows '+0.199%' | PASS |  |
| features | positions: shows '+$6.85' | PASS |  |
| features | positions: shows '+$4.20' | PASS |  |
| features | positions: shows '89.9%' | PASS |  |
| features | positions: shows '6.5 h' | PASS |  |
| features | positions: shows 'SUSPENDED' | PASS |  |
| features | positions: shows 'below 25% minimum' | PASS |  |
| features | positions: Close button per row | PASS |  |
| features | positions: Close all button enabled | PASS |  |
| features | positions: verification note shown verbatim | PASS |  |
| features | positions: closed history shows exit reason and realized net | PASS |  |
| features | trading (history expanded): no horizontal overflow | PASS | scrollWidth=1024 clientWidth=1024 |
| features | trading: every visible input is labelled | PASS |  |
| features | keyboard: Close button reached by Tab (<=60) | PASS | tabs=20 last={'testid': 'position-close', 'tag': 'BUTTON', 'text': 'Close', 'focusVisible' |
| features | keyboard: focused Close button shows a visible focus ring | PASS |  |
| features | keyboard: Enter opens the close confirmation dialog | PASS |  |
| features | keyboard: Escape closes the dialog | PASS |  |
| features | keyboard: focus returns to the Close button after Escape | PASS |  |
| features | positions: no close POST sent without confirmation | PASS |  |
| features | health: no horizontal overflow | PASS | scrollWidth=1024 clientWidth=1024 |
| features | health: 7 readiness checks listed | PASS |  |
| features | health: readiness items carry pass/fail text | PASS |  |
| features | health: READY pill shown | PASS |  |
| features | health: counters show 'Evaluated' | PASS |  |
| features | health: counters show '12,345' | PASS |  |
| features | health: counters show 'Executable' | PASS |  |
| features | health: counters show 'Blocked' | PASS |  |
| features | health: counters show 'Started' | PASS |  |
| features | health: counters show 'Filled' | PASS |  |
| features | health: counters show 'Failed' | PASS |  |
| features | health: counters show 'Aborted' | PASS |  |
| features | health: counters show 'Hedged' | PASS |  |
| features | health: counters show 'Gross' | PASS |  |
| features | health: counters show 'Fees' | PASS |  |
| features | health: counters show 'Gas' | PASS |  |
| features | health: counters show 'Net' | PASS |  |
| features | health: counters show '+$128.10' | PASS |  |
| features | health: counters show 'Prediction error' | PASS |  |
| features | health: counters show 'Breaker trips' | PASS |  |
| features | health: counters show 'Market data updates' | PASS |  |
| features | health: counters show '987,654' | PASS |  |
| features | health: counters show 'Market data rejections' | PASS |  |
| features | health: counters show 'Connector errors' | PASS |  |
| features | health: venue list names venues with health text | PASS |  |
| features | health: liveness ALIVE + breakers NONE TRIPPED + gas listed | PASS |  |
| features | wallets: no horizontal overflow | PASS | scrollWidth=1024 clientWidth=1024 |
| features | wallets: every visible input is labelled | PASS |  |
| features | wallets: ledgers show 'Deposits' | PASS |  |
| features | wallets: ledgers show 'Withdrawals' | PASS |  |
| features | wallets: ledgers show 'CONFIRMED' | PASS |  |
| features | wallets: ledgers show 'PENDING' | PASS |  |
| features | wallets: ledgers show 'BROADCAST' | PASS |  |
| features | wallets: ledgers show 'FAILED' | PASS |  |
| features | wallets: ledgers show '0xabcdef…89abcd' | PASS |  |
| features | wallets: ledgers show '0x111111…111111' | PASS |  |
| features | wallets: ledgers show 'insufficient funds for gas' | PASS |  |
| features | import: invalid JSON rejected client-side | PASS |  |
| features | import: passphrase cleared after invalid JSON | PASS |  |
| features | import: API 400 detail shown verbatim | PASS |  |
| features | import: passphrase field cleared after API error | PASS |  |
| features | import: restored address shown | PASS |  |
| features | import: POST body has keystore object, passphrase, label, mode + CSRF header | PASS |  |
| features | import: Escape closes the dialog | PASS |  |
| features | withdraw: asset select = native + token registry | PASS |  |
| features | withdraw: asset select follows the chain (solana) | PASS |  |
| features | withdraw: new allowlisted address shows 'allowed after <time>' | PASS |  |
| features | withdraw: quote shows token transfer + fee asset | PASS |  |
| features | withdraw: unavailable reason shown and submit disabled | PASS |  |
| features | withdraw: submit enabled once available + acknowledged | PASS |  |
| features | withdraw: HTTP 409 rendered as duplicate request | PASS |  |
| features | withdraw: client request_key sent (uuid-like) with confirm + CSRF header | PASS |  |
| features | wallets (withdraw modal open): no horizontal overflow | PASS | scrollWidth=1024 clientWidth=1024 |
| features | withdraw: Escape closes the dialog | PASS |  |
| features | features: no console errors (browser lines for the mocked 400/409 responses excluded) | PASS | 2 expected resource-load lines for mocked 400/409 |
| estop | health: failing readiness check reads 'fail' (text, not colour) | PASS |  |
| estop | health: NOT READY (HTTP 503) shown | PASS |  |
| estop | health: no horizontal overflow | PASS | scrollWidth=1024 clientWidth=1024 |

## Results at 768 px

| Scenario | Check | Result | Detail |
|---|---|---|---|
| paper | trading: no horizontal overflow | PASS | scrollWidth=768 clientWidth=768 |
| paper | state chip shows 'PAPER' | PASS |  |
| paper | state chip shows 'simulated balances' | PASS |  |
| paper | no LIVE banner when not live | PASS |  |
| paper | no E-stop banner when not stopped | PASS |  |
| paper | state chips carry text, not colour alone | PASS |  |
| paper | no console errors | PASS |  |
| simulation | trading: no horizontal overflow | PASS | scrollWidth=768 clientWidth=768 |
| simulation | state chip shows 'SIMULATION' | PASS |  |
| simulation | state chip shows 'synthetic data' | PASS |  |
| simulation | no LIVE banner when not live | PASS |  |
| simulation | no E-stop banner when not stopped | PASS |  |
| simulation | state chips carry text, not colour alone | PASS |  |
| simulation | no console errors | PASS |  |
| testnet | trading: no horizontal overflow | PASS | scrollWidth=768 clientWidth=768 |
| testnet | state chip shows 'TESTNET' | PASS |  |
| testnet | no LIVE banner when not live | PASS |  |
| testnet | no E-stop banner when not stopped | PASS |  |
| testnet | state chips carry text, not colour alone | PASS |  |
| testnet | no console errors | PASS |  |
| shadow | trading: no horizontal overflow | PASS | scrollWidth=768 clientWidth=768 |
| shadow | state chip shows 'SHADOW' | PASS |  |
| shadow | state chip shows 'recording only, nothing is submitted' | PASS |  |
| shadow | no LIVE banner when not live | PASS |  |
| shadow | no E-stop banner when not stopped | PASS |  |
| shadow | state chips carry text, not colour alone | PASS |  |
| shadow | no console errors | PASS |  |
| live | trading: no horizontal overflow | PASS | scrollWidth=768 clientWidth=768 |
| live | state chip shows 'LIVE — REAL FUNDS' | PASS |  |
| live | LIVE banner visible with text 'LIVE — REAL FUNDS' | PASS |  |
| live | LIVE banner has pulsing dot | PASS |  |
| live | LIVE banner spans full width | PASS |  |
| live | no E-stop banner when not stopped | PASS |  |
| live | state chips carry text, not colour alone | PASS |  |
| live | no console errors | PASS |  |
| estop | trading: no horizontal overflow | PASS | scrollWidth=768 clientWidth=768 |
| estop | state chip shows 'EMERGENCY STOP ACTIVE' | PASS |  |
| estop | state chip shows 'LIVE — REAL FUNDS' | PASS |  |
| estop | LIVE banner visible with text 'LIVE — REAL FUNDS' | PASS |  |
| estop | LIVE banner has pulsing dot | PASS |  |
| estop | LIVE banner spans full width | PASS |  |
| estop | E-stop banner visible with text 'EMERGENCY STOP ACTIVE' | PASS |  |
| estop | E-stop banner has a Release button | PASS |  |
| estop | E-stop banner precedes the LIVE banner (overrides) | PASS |  |
| estop | E-stop chip links to the banner (#estop-banner) | PASS |  |
| estop | state chips carry text, not colour alone | PASS |  |
| estop | no console errors | PASS |  |
| flash_loans | trading: no horizontal overflow | PASS | scrollWidth=768 clientWidth=768 |
| flash_loans | state chip shows 'FLASH LOANS ON' | PASS |  |
| flash_loans | no LIVE banner when not live | PASS |  |
| flash_loans | no E-stop banner when not stopped | PASS |  |
| flash_loans | state chips carry text, not colour alone | PASS |  |
| flash_loans | no console errors | PASS |  |
| high_gas | trading: no horizontal overflow | PASS | scrollWidth=768 clientWidth=768 |
| high_gas | state chip shows 'HIGH GAS' | PASS |  |
| high_gas | state chip shows 'base 3.0×' | PASS |  |
| high_gas | no LIVE banner when not live | PASS |  |
| high_gas | no E-stop banner when not stopped | PASS |  |
| high_gas | state chips carry text, not colour alone | PASS |  |
| high_gas | no console errors | PASS |  |
| connector_degraded | trading: no horizontal overflow | PASS | scrollWidth=768 clientWidth=768 |
| connector_degraded | state chip shows 'CONNECTOR DEGRADED' | PASS |  |
| connector_degraded | state chip shows 'kraken' | PASS |  |
| connector_degraded | state chip shows 'CONNECTOR UNHEALTHY' | PASS |  |
| connector_degraded | state chip shows 'uniswap_base' | PASS |  |
| connector_degraded | no LIVE banner when not live | PASS |  |
| connector_degraded | no E-stop banner when not stopped | PASS |  |
| connector_degraded | state chips carry text, not colour alone | PASS |  |
| connector_degraded | no console errors | PASS |  |
| wallet_mismatch | trading: no horizontal overflow | PASS | scrollWidth=768 clientWidth=768 |
| wallet_mismatch | state chip shows 'WALLET MISMATCH' | PASS |  |
| wallet_mismatch | no LIVE banner when not live | PASS |  |
| wallet_mismatch | no E-stop banner when not stopped | PASS |  |
| wallet_mismatch | state chips carry text, not colour alone | PASS |  |
| wallet_mismatch | no console errors | PASS |  |
| position_discrepancy | trading: no horizontal overflow | PASS | scrollWidth=768 clientWidth=768 |
| position_discrepancy | state chip shows 'POSITION DISCREPANCY' | PASS |  |
| position_discrepancy | no LIVE banner when not live | PASS |  |
| position_discrepancy | no E-stop banner when not stopped | PASS |  |
| position_discrepancy | state chips carry text, not colour alone | PASS |  |
| position_discrepancy | no console errors | PASS |  |
| features | dashboard: no horizontal overflow | PASS | scrollWidth=768 clientWidth=768 |
| features | positions: 2 open rows rendered | PASS |  |
| features | positions: shows 'ETH/USDC' | PASS |  |
| features | positions: shows 'kraken' | PASS |  |
| features | positions: shows 'binance' | PASS |  |
| features | positions: shows '+0.500%' | PASS |  |
| features | positions: shows '+0.199%' | PASS |  |
| features | positions: shows '+$6.85' | PASS |  |
| features | positions: shows '+$4.20' | PASS |  |
| features | positions: shows '89.9%' | PASS |  |
| features | positions: shows '6.5 h' | PASS |  |
| features | positions: shows 'SUSPENDED' | PASS |  |
| features | positions: shows 'below 25% minimum' | PASS |  |
| features | positions: Close button per row | PASS |  |
| features | positions: Close all button enabled | PASS |  |
| features | positions: verification note shown verbatim | PASS |  |
| features | positions: closed history shows exit reason and realized net | PASS |  |
| features | trading (history expanded): no horizontal overflow | PASS | scrollWidth=768 clientWidth=768 |
| features | trading: every visible input is labelled | PASS |  |
| features | keyboard: Close button reached by Tab (<=60) | PASS | tabs=12 last={'testid': 'position-close', 'tag': 'BUTTON', 'text': 'Close', 'focusVisible' |
| features | keyboard: focused Close button shows a visible focus ring | PASS |  |
| features | keyboard: Enter opens the close confirmation dialog | PASS |  |
| features | keyboard: Escape closes the dialog | PASS |  |
| features | keyboard: focus returns to the Close button after Escape | PASS |  |
| features | positions: no close POST sent without confirmation | PASS |  |
| features | health: no horizontal overflow | PASS | scrollWidth=768 clientWidth=768 |
| features | health: 7 readiness checks listed | PASS |  |
| features | health: readiness items carry pass/fail text | PASS |  |
| features | health: READY pill shown | PASS |  |
| features | health: counters show 'Evaluated' | PASS |  |
| features | health: counters show '12,345' | PASS |  |
| features | health: counters show 'Executable' | PASS |  |
| features | health: counters show 'Blocked' | PASS |  |
| features | health: counters show 'Started' | PASS |  |
| features | health: counters show 'Filled' | PASS |  |
| features | health: counters show 'Failed' | PASS |  |
| features | health: counters show 'Aborted' | PASS |  |
| features | health: counters show 'Hedged' | PASS |  |
| features | health: counters show 'Gross' | PASS |  |
| features | health: counters show 'Fees' | PASS |  |
| features | health: counters show 'Gas' | PASS |  |
| features | health: counters show 'Net' | PASS |  |
| features | health: counters show '+$128.10' | PASS |  |
| features | health: counters show 'Prediction error' | PASS |  |
| features | health: counters show 'Breaker trips' | PASS |  |
| features | health: counters show 'Market data updates' | PASS |  |
| features | health: counters show '987,654' | PASS |  |
| features | health: counters show 'Market data rejections' | PASS |  |
| features | health: counters show 'Connector errors' | PASS |  |
| features | health: venue list names venues with health text | PASS |  |
| features | health: liveness ALIVE + breakers NONE TRIPPED + gas listed | PASS |  |
| features | wallets: no horizontal overflow | PASS | scrollWidth=768 clientWidth=768 |
| features | wallets: every visible input is labelled | PASS |  |
| features | wallets: ledgers show 'Deposits' | PASS |  |
| features | wallets: ledgers show 'Withdrawals' | PASS |  |
| features | wallets: ledgers show 'CONFIRMED' | PASS |  |
| features | wallets: ledgers show 'PENDING' | PASS |  |
| features | wallets: ledgers show 'BROADCAST' | PASS |  |
| features | wallets: ledgers show 'FAILED' | PASS |  |
| features | wallets: ledgers show '0xabcdef…89abcd' | PASS |  |
| features | wallets: ledgers show '0x111111…111111' | PASS |  |
| features | wallets: ledgers show 'insufficient funds for gas' | PASS |  |
| features | import: invalid JSON rejected client-side | PASS |  |
| features | import: passphrase cleared after invalid JSON | PASS |  |
| features | import: API 400 detail shown verbatim | PASS |  |
| features | import: passphrase field cleared after API error | PASS |  |
| features | import: restored address shown | PASS |  |
| features | import: POST body has keystore object, passphrase, label, mode + CSRF header | PASS |  |
| features | import: Escape closes the dialog | PASS |  |
| features | withdraw: asset select = native + token registry | PASS |  |
| features | withdraw: asset select follows the chain (solana) | PASS |  |
| features | withdraw: new allowlisted address shows 'allowed after <time>' | PASS |  |
| features | withdraw: quote shows token transfer + fee asset | PASS |  |
| features | withdraw: unavailable reason shown and submit disabled | PASS |  |
| features | withdraw: submit enabled once available + acknowledged | PASS |  |
| features | withdraw: HTTP 409 rendered as duplicate request | PASS |  |
| features | withdraw: client request_key sent (uuid-like) with confirm + CSRF header | PASS |  |
| features | wallets (withdraw modal open): no horizontal overflow | PASS | scrollWidth=768 clientWidth=768 |
| features | withdraw: Escape closes the dialog | PASS |  |
| features | features: no console errors (browser lines for the mocked 400/409 responses excluded) | PASS | 2 expected resource-load lines for mocked 400/409 |
| estop | health: failing readiness check reads 'fail' (text, not colour) | PASS |  |
| estop | health: NOT READY (HTTP 503) shown | PASS |  |
| estop | health: no horizontal overflow | PASS | scrollWidth=768 clientWidth=768 |

## Results at 400 px

| Scenario | Check | Result | Detail |
|---|---|---|---|
| paper | trading: no horizontal overflow | PASS | scrollWidth=400 clientWidth=400 |
| paper | state chip shows 'PAPER' | PASS |  |
| paper | state chip shows 'simulated balances' | PASS |  |
| paper | no LIVE banner when not live | PASS |  |
| paper | no E-stop banner when not stopped | PASS |  |
| paper | state chips carry text, not colour alone | PASS |  |
| paper | no console errors | PASS |  |
| simulation | trading: no horizontal overflow | PASS | scrollWidth=400 clientWidth=400 |
| simulation | state chip shows 'SIMULATION' | PASS |  |
| simulation | state chip shows 'synthetic data' | PASS |  |
| simulation | no LIVE banner when not live | PASS |  |
| simulation | no E-stop banner when not stopped | PASS |  |
| simulation | state chips carry text, not colour alone | PASS |  |
| simulation | no console errors | PASS |  |
| testnet | trading: no horizontal overflow | PASS | scrollWidth=400 clientWidth=400 |
| testnet | state chip shows 'TESTNET' | PASS |  |
| testnet | no LIVE banner when not live | PASS |  |
| testnet | no E-stop banner when not stopped | PASS |  |
| testnet | state chips carry text, not colour alone | PASS |  |
| testnet | no console errors | PASS |  |
| shadow | trading: no horizontal overflow | PASS | scrollWidth=400 clientWidth=400 |
| shadow | state chip shows 'SHADOW' | PASS |  |
| shadow | state chip shows 'recording only, nothing is submitted' | PASS |  |
| shadow | no LIVE banner when not live | PASS |  |
| shadow | no E-stop banner when not stopped | PASS |  |
| shadow | state chips carry text, not colour alone | PASS |  |
| shadow | no console errors | PASS |  |
| live | trading: no horizontal overflow | PASS | scrollWidth=400 clientWidth=400 |
| live | state chip shows 'LIVE — REAL FUNDS' | PASS |  |
| live | LIVE banner visible with text 'LIVE — REAL FUNDS' | PASS |  |
| live | LIVE banner has pulsing dot | PASS |  |
| live | LIVE banner spans full width | PASS |  |
| live | no E-stop banner when not stopped | PASS |  |
| live | state chips carry text, not colour alone | PASS |  |
| live | no console errors | PASS |  |
| estop | trading: no horizontal overflow | PASS | scrollWidth=400 clientWidth=400 |
| estop | state chip shows 'EMERGENCY STOP ACTIVE' | PASS |  |
| estop | state chip shows 'LIVE — REAL FUNDS' | PASS |  |
| estop | LIVE banner visible with text 'LIVE — REAL FUNDS' | PASS |  |
| estop | LIVE banner has pulsing dot | PASS |  |
| estop | LIVE banner spans full width | PASS |  |
| estop | E-stop banner visible with text 'EMERGENCY STOP ACTIVE' | PASS |  |
| estop | E-stop banner has a Release button | PASS |  |
| estop | E-stop banner precedes the LIVE banner (overrides) | PASS |  |
| estop | E-stop chip links to the banner (#estop-banner) | PASS |  |
| estop | state chips carry text, not colour alone | PASS |  |
| estop | no console errors | PASS |  |
| flash_loans | trading: no horizontal overflow | PASS | scrollWidth=400 clientWidth=400 |
| flash_loans | state chip shows 'FLASH LOANS ON' | PASS |  |
| flash_loans | no LIVE banner when not live | PASS |  |
| flash_loans | no E-stop banner when not stopped | PASS |  |
| flash_loans | state chips carry text, not colour alone | PASS |  |
| flash_loans | no console errors | PASS |  |
| high_gas | trading: no horizontal overflow | PASS | scrollWidth=400 clientWidth=400 |
| high_gas | state chip shows 'HIGH GAS' | PASS |  |
| high_gas | state chip shows 'base 3.0×' | PASS |  |
| high_gas | no LIVE banner when not live | PASS |  |
| high_gas | no E-stop banner when not stopped | PASS |  |
| high_gas | state chips carry text, not colour alone | PASS |  |
| high_gas | no console errors | PASS |  |
| connector_degraded | trading: no horizontal overflow | PASS | scrollWidth=400 clientWidth=400 |
| connector_degraded | state chip shows 'CONNECTOR DEGRADED' | PASS |  |
| connector_degraded | state chip shows 'kraken' | PASS |  |
| connector_degraded | state chip shows 'CONNECTOR UNHEALTHY' | PASS |  |
| connector_degraded | state chip shows 'uniswap_base' | PASS |  |
| connector_degraded | no LIVE banner when not live | PASS |  |
| connector_degraded | no E-stop banner when not stopped | PASS |  |
| connector_degraded | state chips carry text, not colour alone | PASS |  |
| connector_degraded | no console errors | PASS |  |
| wallet_mismatch | trading: no horizontal overflow | PASS | scrollWidth=400 clientWidth=400 |
| wallet_mismatch | state chip shows 'WALLET MISMATCH' | PASS |  |
| wallet_mismatch | no LIVE banner when not live | PASS |  |
| wallet_mismatch | no E-stop banner when not stopped | PASS |  |
| wallet_mismatch | state chips carry text, not colour alone | PASS |  |
| wallet_mismatch | no console errors | PASS |  |
| position_discrepancy | trading: no horizontal overflow | PASS | scrollWidth=400 clientWidth=400 |
| position_discrepancy | state chip shows 'POSITION DISCREPANCY' | PASS |  |
| position_discrepancy | no LIVE banner when not live | PASS |  |
| position_discrepancy | no E-stop banner when not stopped | PASS |  |
| position_discrepancy | state chips carry text, not colour alone | PASS |  |
| position_discrepancy | no console errors | PASS |  |
| features | dashboard: no horizontal overflow | PASS | scrollWidth=400 clientWidth=400 |
| features | positions: 2 open rows rendered | PASS |  |
| features | positions: shows 'ETH/USDC' | PASS |  |
| features | positions: shows 'kraken' | PASS |  |
| features | positions: shows 'binance' | PASS |  |
| features | positions: shows '+0.500%' | PASS |  |
| features | positions: shows '+0.199%' | PASS |  |
| features | positions: shows '+$6.85' | PASS |  |
| features | positions: shows '+$4.20' | PASS |  |
| features | positions: shows '89.9%' | PASS |  |
| features | positions: shows '6.5 h' | PASS |  |
| features | positions: shows 'SUSPENDED' | PASS |  |
| features | positions: shows 'below 25% minimum' | PASS |  |
| features | positions: Close button per row | PASS |  |
| features | positions: Close all button enabled | PASS |  |
| features | positions: verification note shown verbatim | PASS |  |
| features | positions: closed history shows exit reason and realized net | PASS |  |
| features | trading (history expanded): no horizontal overflow | PASS | scrollWidth=400 clientWidth=400 |
| features | trading: every visible input is labelled | PASS |  |
| features | keyboard: Close button reached by Tab (<=60) | PASS | tabs=12 last={'testid': 'position-close', 'tag': 'BUTTON', 'text': 'Close', 'focusVisible' |
| features | keyboard: focused Close button shows a visible focus ring | PASS |  |
| features | keyboard: Enter opens the close confirmation dialog | PASS |  |
| features | keyboard: Escape closes the dialog | PASS |  |
| features | keyboard: focus returns to the Close button after Escape | PASS |  |
| features | positions: no close POST sent without confirmation | PASS |  |
| features | health: no horizontal overflow | PASS | scrollWidth=400 clientWidth=400 |
| features | health: 7 readiness checks listed | PASS |  |
| features | health: readiness items carry pass/fail text | PASS |  |
| features | health: READY pill shown | PASS |  |
| features | health: counters show 'Evaluated' | PASS |  |
| features | health: counters show '12,345' | PASS |  |
| features | health: counters show 'Executable' | PASS |  |
| features | health: counters show 'Blocked' | PASS |  |
| features | health: counters show 'Started' | PASS |  |
| features | health: counters show 'Filled' | PASS |  |
| features | health: counters show 'Failed' | PASS |  |
| features | health: counters show 'Aborted' | PASS |  |
| features | health: counters show 'Hedged' | PASS |  |
| features | health: counters show 'Gross' | PASS |  |
| features | health: counters show 'Fees' | PASS |  |
| features | health: counters show 'Gas' | PASS |  |
| features | health: counters show 'Net' | PASS |  |
| features | health: counters show '+$128.10' | PASS |  |
| features | health: counters show 'Prediction error' | PASS |  |
| features | health: counters show 'Breaker trips' | PASS |  |
| features | health: counters show 'Market data updates' | PASS |  |
| features | health: counters show '987,654' | PASS |  |
| features | health: counters show 'Market data rejections' | PASS |  |
| features | health: counters show 'Connector errors' | PASS |  |
| features | health: venue list names venues with health text | PASS |  |
| features | health: liveness ALIVE + breakers NONE TRIPPED + gas listed | PASS |  |
| features | wallets: no horizontal overflow | PASS | scrollWidth=400 clientWidth=400 |
| features | wallets: every visible input is labelled | PASS |  |
| features | wallets: ledgers show 'Deposits' | PASS |  |
| features | wallets: ledgers show 'Withdrawals' | PASS |  |
| features | wallets: ledgers show 'CONFIRMED' | PASS |  |
| features | wallets: ledgers show 'PENDING' | PASS |  |
| features | wallets: ledgers show 'BROADCAST' | PASS |  |
| features | wallets: ledgers show 'FAILED' | PASS |  |
| features | wallets: ledgers show '0xabcdef…89abcd' | PASS |  |
| features | wallets: ledgers show '0x111111…111111' | PASS |  |
| features | wallets: ledgers show 'insufficient funds for gas' | PASS |  |
| features | import: invalid JSON rejected client-side | PASS |  |
| features | import: passphrase cleared after invalid JSON | PASS |  |
| features | import: API 400 detail shown verbatim | PASS |  |
| features | import: passphrase field cleared after API error | PASS |  |
| features | import: restored address shown | PASS |  |
| features | import: POST body has keystore object, passphrase, label, mode + CSRF header | PASS |  |
| features | import: Escape closes the dialog | PASS |  |
| features | withdraw: asset select = native + token registry | PASS |  |
| features | withdraw: asset select follows the chain (solana) | PASS |  |
| features | withdraw: new allowlisted address shows 'allowed after <time>' | PASS |  |
| features | withdraw: quote shows token transfer + fee asset | PASS |  |
| features | withdraw: unavailable reason shown and submit disabled | PASS |  |
| features | withdraw: submit enabled once available + acknowledged | PASS |  |
| features | withdraw: HTTP 409 rendered as duplicate request | PASS |  |
| features | withdraw: client request_key sent (uuid-like) with confirm + CSRF header | PASS |  |
| features | wallets (withdraw modal open): no horizontal overflow | PASS | scrollWidth=400 clientWidth=400 |
| features | withdraw: Escape closes the dialog | PASS |  |
| features | features: no console errors (browser lines for the mocked 400/409 responses excluded) | PASS | 2 expected resource-load lines for mocked 400/409 |
| estop | health: failing readiness check reads 'fail' (text, not colour) | PASS |  |
| estop | health: NOT READY (HTTP 503) shown | PASS |  |
| estop | health: no horizontal overflow | PASS | scrollWidth=400 clientWidth=400 |

## Fixed during this QA (found by the script, then re-run)

* **Real UI bug:** at 400 px in LIVE and E-stop states the sticky header overflowed the viewport (scrollWidth 460 > 400) because the `LIVE — REAL FUNDS` badge, the `E-STOP` pill and the full-text `Emergency stop` button do not fit. Fixed with a ≤519 px rule: icon-only stop button (its accessible name stays `Emergency stop` via `aria-label`), compact LIVE badge. Re-run passes at every width.
* Positions table at 1280 px kept the Close column behind the container's horizontal scroll: column headers were shortened (`Venues (spot / perp)`, `Funding`, `Liq. distance`, `Open`), entry/current basis merged into one `Basis (entry → now)` column, the `suspended` note moved under the pair name, and the action column made `position: sticky; right: 0` so the Close button stays visible whenever a table scrolls inside its container. Tables still scroll inside their own `.table-wrap` when needed — the page never scrolls horizontally (asserted).
* QA-script defects corrected before the final run (they produced false failures, not UI failures): `innerText` returns CSS-transformed text so `pass`/card titles compare case-insensitively now; the truncated-hash expectation used the wrong tail length (`shortAddr(h, 8, 6)` → `0xabcdef…89abcd`); the `**/health` route pattern also intercepted the SPA navigation to `/health` (document requests now pass through to the static server); the browser's resource-load console lines for the intentionally mocked 400/409 responses are now classified as expected.

## Not verified (and why)

* **Live-backend mode RUN (2026-09-14, after this report's mock run):** `python qa/ui_qa.py http://127.0.0.1:<port> --token …` (token-cookie auth added to the script) against a real simulation-mode FEDR process: **192 checks, 0 failures, 0 console errors** at 1280/1024/768/400 across all 8 pages, real `/api/**` + `/health/*` payloads. The original caveat below is retained for the mock run's scope. Original note: **Live-backend mode was not run.** `python3 qa/ui_qa.py http://127.0.0.1:8935` needs a running backend; none was started in this session (backend Python files are being edited concurrently and `docker` was off-limits). Everything above is against mocked responses, so rendering of the real `/api/trading/positions`, `/api/system/metrics`, `/health/*` and `/api/wallets` payloads is unverified end-to-end.
* **Backend contract assumed, not observed:** `POST /api/wallets/bot/import` and the `withdrawals`, `token_registry`, `new_address_delay_minutes` fields of `GET /api/wallets` were not present in the backend working tree when this UI was written; the UI types them as optional and degrades (empty withdrawals ledger, native-asset-only select, no new-address delay note) when absent. Shapes used in the mock follow the task description and the `Withdrawal` DB model.
* **Resolved after this run:** the SPA page was moved from `/health` to `/status` (nav link, alert links and the QA script updated) so it no longer collides with the backend's `/health` JSON probe; `python3 qa/ui_qa.py --mock` was re-run after the move with the same result (656 checks, 0 failures). Original finding: **`/health` SPA route vs backend `/health` JSON:** client-side navigation to the Health page works and was tested (the mock static server has an SPA fallback), but on the real FastAPI server a hard reload / deep link to `/health` returns the JSON health probe (`main.py` registers `/health` before the SPA catch-all). Needs a backend follow-up (serve `index.html` for `Accept: text/html`, or move the page path); not fixable from `frontend/` alone.
* **Withdrawal success path (2xx)** was not exercised — the mock always answers 409 to verify the duplicate rendering and the `request_key` transmission. The real `POST /api/trading/positions/{id}/close` and `close-all` calls were not exercised either: the close dialog was opened by keyboard and dismissed with Escape (a check asserts no close POST is sent without confirmation); `Close all` was only asserted enabled.
* **Pulsing dot** was verified by the presence and visibility of the `.pulse` element, not by sampling animation frames; `prefers-reduced-motion: reduce` disables the animation (rule present, not tested).
* **Visible focus ring** was asserted on the positions Close button only (`:focus-visible` matched, computed outline 2px solid); other controls rely on the same global `:focus-visible` rule and were not individually checked. Escape-to-close was asserted on the close-position dialog, the Import backup dialog and the Withdraw dialog only.
* **Pages not loaded in mock mode:** Opportunities, Exchanges, History, Settings (their APIs are not mocked); they are unchanged by this work except the header/state bar, which is shared. Dark theme, real screen readers, touch input and non-Chromium browsers were not tested.
* **SSE indicator** reads `Reconnecting…` in the mock screenshots because the mocked event stream ends immediately after one snapshot; that is a mock artefact, not an assertion.
* Gas-regime chip thresholds (2.5× / 4× baseline) mirror the backend defaults in `GasSettings`; a user-customised `high_multiple` is not read by the UI (it also consults `opportunities[].gas_regime` from the snapshot and the `gas_spike` breaker, which are backend-computed).
