"""SMALL LIVE TEST stage: live trading always starts with one strategy, one explicitly selected route,
and a hard-coded tiny notional cap; FULL live is a separate opt-in with its own phrase and normally at
least one filled small-test trade on record. Enforcement lives in the Risk Engine, so no execution path
(auto, manual, carry) can bypass it."""

from __future__ import annotations

import asyncio
import pathlib
from decimal import Decimal

import pytest

from fedr.core.enums import Decision, Strategy, TradingMode
from fedr.engine.risk_engine import SMALL_LIVE_TEST_MAX_NOTIONAL_USD
from tests.test_execution import Harness

D = Decimal


def _live_small(h):
    h.settings.general.mode = TradingMode.LIVE
    h.settings.live.activated = True
    h.settings.live.stage = "small_test"
    h.settings.live.small_test_strategy = "cex_cex"
    h.settings.live.small_test_route = None
    return h


def test_cap_is_hard_coded_and_tiny():
    assert SMALL_LIVE_TEST_MAX_NOTIONAL_USD == D("25")
    from fedr.config.schema import AppSettings

    assert AppSettings().live.stage == "small_test"  # a restored/edited settings doc can never skip the stage
    assert not hasattr(AppSettings().live, "small_test_max_notional_usd")  # not a setting


def test_small_live_test_blocks_everything_until_a_route_is_selected():
    async def run():
        h = _live_small(await Harness().start())
        opps = await h.opps.scan()
        assert opps and all(o.decision is Decision.BLOCKED for o in opps)
        assert any("select the small-live-test route" in r for o in opps for r in o.block_reasons)

    asyncio.run(run())


def test_small_live_test_allows_only_the_selected_route_strategy_and_size():
    async def run():
        h = _live_small(await Harness().start())
        h.settings.live.small_test_route = "kraken->coinbase"
        opps = await h.opps.scan()
        selected = [o for o in opps if o.strategy is Strategy.CEX_CEX and o.buy.venue == "kraken"]
        others = [o for o in opps if not (o.strategy is Strategy.CEX_CEX and o.buy.venue == "kraken")]
        assert selected and others
        # the wrong route / strategy is named explicitly
        assert any("allows only the selected route" in r for o in others for r in o.block_reasons) or any(
            "allows only the selected strategy" in r for o in others for r in o.block_reasons
        )
        # the selected route still hits the hard cap when sizing exceeds it
        big = [o for o in selected if D(o.extra.get("notional_usd", "0")) > SMALL_LIVE_TEST_MAX_NOTIONAL_USD]
        for o in big:
            assert any("small live test caps notional" in r for r in o.block_reasons), o.block_reasons
        # no opportunity anywhere is executable above the cap
        for o in opps:
            if o.is_executable:
                assert D(o.extra.get("notional_usd", "0")) <= SMALL_LIVE_TEST_MAX_NOTIONAL_USD

    asyncio.run(run())


def test_full_stage_lifts_the_small_test_restrictions():
    async def run():
        h = _live_small(await Harness().start())
        h.settings.live.stage = "full"
        opps = await h.opps.scan()
        assert not any("small" in r for o in opps for r in o.block_reasons)

    asyncio.run(run())


def test_flash_loans_never_run_in_the_small_test():
    from fedr.config.schema import AppSettings
    from fedr.core.enums import OrderSide, VenueKind
    from fedr.core.models import ExecutionQuote
    from fedr.engine.risk_engine import PortfolioState, RiskEngine

    s = AppSettings()
    s.general.mode = TradingMode.LIVE
    s.live.activated = True
    s.live.small_test_route = "uniswap-base->jupiter"
    q = lambda v, side: ExecutionQuote(  # noqa: E731
        venue=v,
        kind=VenueKind.DEX,
        symbol="SOL/USDC",
        side=side,
        base_amount=D("1"),
        quote_amount=D("20"),
        avg_price=D("20"),
        reference_price=D("20"),
        slippage_pct=D("0"),
        fully_fillable=True,
        market_ts_ms=0,
        price_impact_pct=D("0"),
    )
    r = RiskEngine(s).assess(
        strategy=Strategy.FLASH_LOAN,
        buy=q("uniswap-base", OrderSide.BUY),
        sell=q("jupiter", OrderSide.SELL),
        base="SOL",
        quote="USDC",
        notional_usd=D("20"),
        capital_required_usd=D("20"),
        portfolio=PortfolioState(total_capital_usd=D("1000"), usable_capital_usd=D("500")),
        flash_loan_usd=D("1000"),
    )
    assert not r.passed and any("flash loans are not available" in x for x in r.reasons)


def test_activate_live_always_lands_in_small_test_stage():
    src = pathlib.Path("fedr/app.py").read_text()
    body = src[src.index("async def activate_live(") : src.index("async def activate_full_live(")]
    assert 'self.settings.live.stage = "small_test"' in body


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setenv("FEDR_DEFAULT_MODE", "simulation")
    monkeypatch.setenv("FEDR_GATEWAY_ENABLED", "false")
    monkeypatch.setenv("FEDR_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("FEDR_AUTH_TOKEN", "test-token-0123456789abcdef")
    monkeypatch.setenv("FEDR_LIVE_TRADING_ALLOWED", "true")
    from fedr.config import env as envmod

    envmod._env = None
    from fedr.main import create_app

    return create_app()


def test_full_live_promotion_requires_phrase_and_small_test_evidence(api):
    from tests.test_api import _run

    async def t(c):
        fedr = api.state.fedr
        # not live yet → refused regardless of phrase
        r = await c.post(
            "/api/system/live/full",
            json={"confirmation": "ACTIVATE FULL LIVE TRADING"},
            headers={"X-Fedr-Request": "1"},
        )
        assert r.status_code == 400 and "activate the small live test first" in r.text
        # simulate an activated small test
        fedr.settings.general.mode = TradingMode.LIVE
        fedr.settings.live.activated = True
        fedr.settings.live.stage = "small_test"
        r = await c.post(
            "/api/system/live/full", json={"confirmation": "wrong"}, headers={"X-Fedr-Request": "1"}
        )
        assert r.status_code == 400 and "exact confirmation phrase" in r.text
        r = await c.post(
            "/api/system/live/full",
            json={"confirmation": "ACTIVATE FULL LIVE TRADING"},
            headers={"X-Fedr-Request": "1"},
        )
        assert r.status_code == 400 and "no filled small-live-test trade" in r.text
        assert fedr.settings.live.stage == "small_test"  # nothing changed
        r = await c.post(
            "/api/system/live/full",
            json={"confirmation": "ACTIVATE FULL LIVE TRADING", "acknowledge_no_small_test": True},
            headers={"X-Fedr-Request": "1"},
        )
        assert r.status_code == 200 and r.json()["stage"] == "full"
        assert fedr.settings.live.stage == "full"
        st = (await c.get("/api/system/status")).json()
        assert st["live_stage"] == "full" and st["small_live_test"] is None
        # status shows the small-test box while in that stage
        fedr.settings.live.stage = "small_test"
        st = (await c.get("/api/system/status")).json()
        assert st["small_live_test"]["max_notional_usd"] == "25"

    _run(api, t)


def test_strategy_readiness_flags(api):
    from tests.test_api import _run

    async def t(c):
        r = await c.get("/api/system/readiness/strategies")
        assert r.status_code == 200
        d = r.json()
        assert set(d) == {
            "CEX_ARBITRAGE_READY",
            "CEX_DEX_READY",
            "DEX_ARB_READY",
            "FUNDING_READY",
            "BASIS_READY",
            "FLASH_LOAN_READY",
        }
        assert d["CEX_ARBITRAGE_READY"]["ready"] is True
        assert d["DEX_ARB_READY"]["ready"] is False  # disabled by default
        assert "disabled" in d["DEX_ARB_READY"]["blockers"][0]
        assert d["FLASH_LOAN_READY"]["ready"] is False
        assert any("contract" in b for b in d["FLASH_LOAN_READY"]["blockers"])
        for v in d.values():  # live verification is never claimed by the software itself
            assert v["live_verified"] is False and v["verification"]

    _run(api, t)
