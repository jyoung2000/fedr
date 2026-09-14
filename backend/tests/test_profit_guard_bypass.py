"""Profit Guard bypass attempts. Every path that can submit an order must go through a *fresh*
evaluation (OpportunityEngine.evaluate → ProfitGuard/GasGuard/RiskEngine → Decision) immediately before
submission; a caller-supplied Opportunity object is never trusted. See docs/PROFIT_GUARD_CALL_GRAPH.md."""

from __future__ import annotations

import asyncio
import re
from decimal import Decimal
from pathlib import Path

import pytest

from fedr.core.enums import Decision, Strategy, TradeStatus
from tests.test_execution import Harness

D = Decimal
SRC = Path(__file__).resolve().parents[1] / "fedr"


def _forge_safe(o):
    """Tamper with an opportunity the way a buggy caller or UI could: claim it is safe and hugely profitable."""
    o.decision = Decision.SAFE_TO_EXECUTE
    o.block_reasons = []
    o.profit.expected_net_profit = D("1000")
    o.profit.worst_case_profit = D("900")
    o.profit.gross_profit = D("1200")
    return o


def test_forged_decision_on_a_blocked_route_is_not_executed():
    async def run():
        h = await Harness(dislocation_bps=0.0).start()  # no dislocation: every route is blocked
        opps = await h.opps.scan()
        blocked = [o for o in opps if o.decision is Decision.BLOCKED and o.strategy is Strategy.CEX_CEX]
        assert blocked, "expected blocked CEX-CEX routes without a dislocation"
        o = _forge_safe(blocked[0])
        tr = await h.exec.execute(o, trigger="test")
        assert tr.status is TradeStatus.ABORTED and "re-validation failed" in tr.explanation
        assert tr.buy is None and tr.sell is None  # nothing was submitted
        assert tr.estimated_net != D("1000")  # the record carries the fresh assessment, not the forged one
        for v in ("kraken", "coinbase"):
            assert h.ledger.get(v, "USDC").used == 0 and h.ledger.get(v, "SOL").used == 0

    asyncio.run(run())


def test_tampered_profit_fields_are_replaced_by_the_fresh_assessment():
    async def run():
        h = await Harness().start()
        o = await h.best()
        assert o.is_executable
        real_net = o.profit.expected_net_profit
        o.profit.expected_net_profit = D("1000")
        o.profit.worst_case_profit = D("999")
        tr = await h.exec.execute(o, trigger="test")
        assert tr.status is TradeStatus.FILLED
        assert (
            tr.estimated_net < D("100") and abs(tr.estimated_net - real_net) < real_net
        )  # re-quoted, not copied
        assert tr.estimated_worst_case <= tr.estimated_net

    asyncio.run(run())


def test_position_manager_cannot_open_a_forged_carry_opportunity():
    async def run():
        from tests.test_positions import carry_harness

        h, perp = await carry_harness(premium="1.001")  # basis too thin: blocked by Profit Guard
        opps = await h.opps.scan()
        carry = [o for o in opps if o.strategy is Strategy.SPOT_PERP]
        assert carry and not carry[0].is_executable
        tr, pos = await h.pm.open_from_opportunity(_forge_safe(carry[0]), trigger="test")
        assert pos is None and tr.status is TradeStatus.ABORTED and "re-validation failed" in tr.explanation
        assert h.ledger.get("binance-perp", "USDC:collateral").free == 0

    asyncio.run(run())


def test_flash_loan_engine_revalidates_before_broadcast():
    """Covered end-to-end by tests/test_execution.py::test_flash_loan_paper_path_aborts_when_unprofitable_after_refresh;
    here we pin the code-level guarantee: FlashLoanEngine.execute re-evaluates and aborts on any non-SAFE decision."""
    src = (SRC / "engine" / "flashloan.py").read_text()
    body = src[src.index("async def execute(") : src.index("async def _abort(")]
    assert "opportunity_engine.evaluate(" in body
    assert "fresh.decision is not Decision.SAFE_TO_EXECUTE" in body
    assert body.index("evaluate(") < body.index("_broadcast") if "_broadcast" in body else True


def test_single_leg_submission_is_not_reachable_from_the_api_or_strategies():
    """submit_leg / _submit (the only order-placing calls) are used solely by the executor itself and the
    position manager's close-out path (risk-reducing, no Profit Guard by design)."""
    users = {}
    for p in SRC.rglob("*.py"):
        text = p.read_text()
        if re.search(r"\.submit_leg\(|\._submit\(", text):
            users[p.relative_to(SRC).as_posix()] = len(re.findall(r"\.submit_leg\(|\._submit\(", text))
    assert set(users) == {"engine/execution/executor.py", "engine/positions.py"}, users
    # no API route exposes leg submission or a raw place_order
    api_text = "\n".join(p.read_text() for p in (SRC / "api").rglob("*.py"))
    assert "place_order(" not in api_text and "submit_leg(" not in api_text and "_submit(" not in api_text
    # place_order (venue connectors) is called only by the executor and the emergency hedge / e-stop plumbing
    callers = sorted(
        p.relative_to(SRC).as_posix()
        for p in SRC.rglob("*.py")
        if re.search(r"\.place_order\(", p.read_text())
    )
    assert callers == ["engine/execution/emergency_hedge.py", "engine/execution/executor.py"], callers


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setenv("FEDR_DEFAULT_MODE", "simulation")
    monkeypatch.setenv("FEDR_GATEWAY_ENABLED", "false")
    monkeypatch.setenv("FEDR_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("FEDR_AUTH_TOKEN", "test-token-0123456789abcdef")
    monkeypatch.setenv("FEDR_LIVE_TRADING_ALLOWED", "true")  # the env flag alone must not open live
    from fedr.config import env as envmod

    envmod._env = None
    from fedr.main import create_app

    return create_app()


def test_api_cannot_execute_forged_ids_or_activate_live_from_env_alone(api):
    from tests.test_api import _run

    async def t(c):
        r = await c.post("/api/opportunities/opp_doesnotexist/execute")
        assert r.status_code == 404
        r = await c.post("/api/trading/mode", json={"mode": "live"})
        assert r.status_code == 400
        r = await c.post("/api/system/live/activate", json={"confirmation": "I UNDERSTAND THE RISKS"})
        assert r.status_code in (400, 403, 409), r.text  # readiness checklist not satisfied
        st = (await c.get("/api/trading/state")).json()
        assert st["mode"] != "live"

    _run(api, t)
