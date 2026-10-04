"""Invariants E/F/G: upgrade from a populated schema-v2 database, flash-loan gating and realized-profit
decoding, shared nonce coordination, single-process data directory, withdrawal uncertainty."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from decimal import Decimal
from pathlib import Path

import pytest

from fedr.config.env import EnvSettings
from fedr.core.enums import Chain, OrderSide, OrderStatus, Strategy, TradeStatus, TradingMode
from fedr.core.models import OrderRequest, OrderResult, TradeRecord
from fedr.db.engine import Database
from fedr.db.migrations import LATEST
from fedr.db.repo import Repo
from fedr.engine.flashloan import CONTRACT_ABI, FlashLoanEngine
from fedr.wallets.nonce import NonceManager
from fedr.wallets.transfers import is_rpc_rejection
from tests.test_execution import Harness

D = Decimal
FIXTURE = Path(__file__).parent / "fixtures" / "schema_v2_populated.sql"


# ---------------------------------------------------------------------------------------------- migration
def test_upgrade_from_populated_v2_database_preserves_history_and_marks_legacy(tmp_path):
    """The fixture was produced by the audited release (schema v2) with real Repo calls."""
    path = tmp_path / "fedr.db"
    con = sqlite3.connect(path)
    con.executescript(FIXTURE.read_text())
    con.close()

    async def run():
        db = Database(f"sqlite+aiosqlite:///{path}")
        assert await db.init() == [3]
        repo = Repo(db)
        assert await repo.schema_version() == LATEST
        # a consistent backup was taken before the upgrade
        backups = list(tmp_path.glob("fedr.db.pre-v3-from-v2-*.bak"))
        assert len(backups) == 1
        assert (
            sqlite3.connect(backups[0]).execute("select max(version) from schema_version").fetchone()[0] == 2
        )
        # history preserved: trades, order ids, fills, provenance, settings, accounts
        spot = await repo.get_trade("trd_legacy_spot")
        assert spot.actual_net == "1.68" and spot.settlement == "legacy"
        orders = await repo.orders_for_trade("trd_legacy_spot")
        assert {o.id for o in orders} == {"trd_legacy_spot:v_kraken_buy", "trd_legacy_spot:v_coinbase_sell"}
        assert (await repo.get_settings_doc("app")) == {"general": {"mode": "paper"}}
        assert [a.id for a in await repo.list_exchange_accounts()] == ["exa_1"]
        # P&L not "corrected" - flagged as legacy instead
        summary = await repo.pnl_summary(TradingMode.PAPER)
        assert D(summary["total"]["net"]) == D("1.68") + D("7.2821234379517")
        assert summary["legacy_days"] == ["2026-09-13", "2026-09-14"]
        # the open carry position remembers what the old code booked at entry, so its close books the rest
        pos = await repo.get_position("pos_legacy")
        assert pos.payload["legacy"] and D(pos.payload["entry_booked_usd"]) == D("7.2821234379517")
        bals = await repo.load_paper_balances(TradingMode.PAPER)
        assert bals["kraken"]["USDC"][0] == D("4900")
        # new journal works on the upgraded schema; re-running is a no-op
        assert await repo.unresolved_trades() == []
        assert await db.init() == []
        await db.close()

    asyncio.run(run())


def test_fresh_install_gets_latest_schema(tmp_path):
    async def run():
        db = Database(f"sqlite+aiosqlite:///{tmp_path}/n.db")
        assert await db.init() == [LATEST]
        assert not list(tmp_path.glob("*.bak"))  # nothing to back up on a fresh install
        await db.close()

    asyncio.run(run())


# ---------------------------------------------------------------------------------------------- flash gating
def _engine(mode: TradingMode, **env) -> FlashLoanEngine:
    h = Harness()
    h.settings.general.mode = mode
    h.settings.flash_loan.enabled = True
    h.settings.flash_loan.chains = ["base"]
    ctx = type("Ctx", (), {"settings": h.settings, "mode": mode})()
    return FlashLoanEngine(ctx, None, EnvSettings(_env_file=None, **env))


def test_mainnet_flash_execution_requires_an_approved_code_hash():
    e = _engine(TradingMode.LIVE, flashloan_contract_base="0x" + "1" * 40, rpc_base="http://rpc")
    ok, why = e.available_on(Chain.BASE)
    assert not ok and "APPROVED_CODEHASH" in why
    e2 = _engine(
        TradingMode.LIVE,
        flashloan_contract_base="0x" + "1" * 40,
        rpc_base="http://rpc",
        flashloan_approved_codehash_base="0x" + "ab" * 32,
    )
    assert e2.available_on(Chain.BASE)[0]


def test_flash_loans_are_refused_in_testnet_mode():
    e = _engine(TradingMode.TESTNET, flashloan_contract_base="0x" + "1" * 40, rpc_base="http://rpc")
    ok, why = e.available_on(Chain.BASE)
    assert not ok and "TESTNET" in why


def test_realized_flash_profit_is_decoded_from_the_contract_event(tmp_path):
    """Run the (F01-fixed) contract on a real EVM and settle the trade from its receipt + event."""
    pytest.importorskip("eth_tester")
    from tests.test_contract_capital_preservation import _params, _setup

    w3, owner, asset, mid, ra, rb, arb, unit = _setup(6, 10_100, 10_100)
    asset.functions.mint(arb.address, 2_000 * unit).transact({"from": owner})  # pre-existing capital
    tx = arb.functions.executeArbitrage(asset.address, 10_000 * unit, _params(w3, mid, ra, rb, 0)).transact(
        {"from": owner}
    )
    receipt = w3.eth.wait_for_transaction_receipt(tx)

    async def run():
        h = await Harness(with_db=True).start(tmp_path)
        h.settings.general.mode = TradingMode.LIVE
        eng = FlashLoanEngine(h.ctx, None, EnvSettings(_env_file=None), executor=h.exec)
        req = lambda side: OrderRequest(  # noqa: E731
            venue="uniswap-base", symbol="ETH/USDC", side=side, amount=D("4"), limit_price=D("2500")
        )
        tr = TradeRecord(
            id="fltrd_x",
            mode=TradingMode.LIVE,
            strategy=Strategy.FLASH_LOAN,
            opportunity_id="o",
            pair="ETH/USDC",
            route="r",
            size_base=D("4"),
            status=TradeStatus.EXECUTING,
            estimated_gross=D("0"),
            estimated_net=D("0"),
            estimated_worst_case=D("0"),
            estimated_costs={},
            buy=OrderResult(request=req(OrderSide.BUY), order_id="h", status=OrderStatus.SUBMITTING),
            sell=OrderResult(request=req(OrderSide.SELL), order_id="h", status=OrderStatus.SUBMITTING),
            extra={"quote_decimals": 6, "quote_asset": "USDC"},
        )
        h.hub.prices.external = lambda a: {"USDC": D("1"), "ETH": D("2000")}.get(a)
        contract = w3.eth.contract(address=arb.address, abi=CONTRACT_ABI)
        await eng._apply_receipt(tr, receipt, contract, Chain.BASE)
        expected_profit = D(10_000 * 10_100 // 10_000 * 10_100 // 10_000 - 10_000) - D("5")  # 196
        assert tr.status is TradeStatus.FILLED
        gas_usd = tr.actual_gas
        assert tr.actual_net == expected_profit - gas_usd  # incremental profit, not the 2,196 balance
        pnl = await h.repo.pnl_summary(TradingMode.LIVE)
        assert D(pnl["total"]["net"]) == tr.actual_net

    asyncio.run(run())


def test_flash_receipt_without_event_is_unreconciled_not_fabricated(tmp_path):
    async def run():
        h = await Harness(with_db=True).start(tmp_path)
        h.settings.general.mode = TradingMode.LIVE
        eng = FlashLoanEngine(h.ctx, None, EnvSettings(_env_file=None), executor=h.exec)
        req = OrderRequest(
            venue="x", symbol="ETH/USDC", side=OrderSide.BUY, amount=D("1"), limit_price=D("1")
        )
        tr = TradeRecord(
            id="fltrd_y",
            mode=TradingMode.LIVE,
            strategy=Strategy.FLASH_LOAN,
            opportunity_id="o",
            pair="ETH/USDC",
            route="r",
            size_base=D("1"),
            status=TradeStatus.EXECUTING,
            estimated_gross=D("0"),
            estimated_net=D("5"),
            estimated_worst_case=D("0"),
            estimated_costs={},
            buy=OrderResult(request=req, order_id="h", status=OrderStatus.SUBMITTING),
            sell=OrderResult(request=req, order_id="h", status=OrderStatus.SUBMITTING),
        )

        class NoEvents:
            class events:  # noqa: N801
                @staticmethod
                def ArbitrageExecuted():  # noqa: N802
                    return type("E", (), {"process_receipt": staticmethod(lambda r: [])})()

        await eng._apply_receipt(
            tr, {"gasUsed": 21000, "effectiveGasPrice": 10**9, "status": 1}, NoEvents, Chain.BASE
        )
        assert tr.status is TradeStatus.UNRECONCILED and tr.actual_net is None
        assert (await h.repo.pnl_summary(TradingMode.LIVE))["trades"] == 0

    asyncio.run(run())


# ---------------------------------------------------------------------------------------------- nonces
class FakeEth:
    def __init__(self, pending: int):
        self.pending = pending

    async def get_transaction_count(self, addr, block):
        assert block == "pending"
        return self.pending


def test_nonce_manager_never_reuses_a_possibly_broadcast_nonce():
    async def run():
        nm = NonceManager()
        w3 = type("W3", (), {"eth": FakeEth(5)})()
        got = []

        async def send(mark):
            async with nm.lease(w3, "base", "0xAbC") as lease:
                got.append(lease.nonce)
                await asyncio.sleep(0.01)
                if mark:
                    lease.mark_used()

        await asyncio.gather(send(True), send(True))  # concurrent signing paths on one account
        assert sorted(got) == [5, 6]  # node still reports 5 pending: the manager advances itself
        await send(False)  # signed but refused before broadcast: nonce stays free
        await send(True)
        assert got[-2:] == [7, 7]

    asyncio.run(run())


def test_rpc_error_responses_are_rejections_transport_errors_are_not():
    assert is_rpc_rejection(ValueError({"code": -32000, "message": "nonce too low"}))
    assert not is_rpc_rejection(TimeoutError("read timeout"))
    assert not is_rpc_rejection(ConnectionResetError())


# ---------------------------------------------------------------------------------------------- single process
def test_second_process_on_the_same_data_directory_is_refused(tmp_path):
    from fedr.app import FedrApp

    env = EnvSettings(_env_file=None, data_dir=str(tmp_path))
    env.ensure_dirs()
    a, b = FedrApp(env), FedrApp(env)
    a._acquire_data_lock()
    try:
        with pytest.raises(RuntimeError, match="already using"):
            b._acquire_data_lock()
    finally:
        a._release_data_lock()
    b._acquire_data_lock()  # free again once the first instance stopped
    b._release_data_lock()


def test_fixture_is_valid_json_payloads():
    # guard against an accidentally edited fixture: payload columns must stay JSON
    con = sqlite3.connect(":memory:")
    con.executescript(FIXTURE.read_text())
    for (payload,) in con.execute("select payload from trades"):
        json.loads(payload)
