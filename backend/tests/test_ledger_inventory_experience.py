import asyncio
from decimal import Decimal

import pytest

from fedr.config.schema import RebalanceSettings
from fedr.connectors.paper.ledger import PaperLedger
from fedr.core.enums import OrderSide, Strategy, TradingMode
from fedr.core.models import Balance
from fedr.engine.experience import ExperienceEngine, route_key, size_bucket
from fedr.engine.inventory import InventoryManager
from fedr.engine.rebalancer import Rebalancer

D = Decimal


def test_paper_ledger_reserve_settle_release():
    async def run():
        l = PaperLedger()
        l.seed({"kraken": {"USDC": D("1000"), "SOL": D("5")}})
        await l.reserve("kraken", "USDC", D("300"))
        assert l.get("kraken", "USDC").free == D("700") and l.get("kraken", "USDC").used == D("300")
        await l.settle_fill("kraken", "SOL", "USDC", OrderSide.BUY, D("2"), D("290"), D("1"), reserved=True)
        assert l.get("kraken", "SOL").free == D("7")
        assert l.get("kraken", "USDC").used == D("9")  # 300 reserved - 291 spent
        await l.release("kraken", "USDC", D("9"))
        assert l.get("kraken", "USDC").free == D("709") and l.get("kraken", "USDC").used == 0
        with pytest.raises(ValueError):
            await l.reserve("kraken", "SOL", D("100"))
        with pytest.raises(ValueError):
            await l.adjust("kraken", "USDC", D("-5000"))
        dump = l.dump()
        l2 = PaperLedger()
        l2.load(dump)
        assert l2.get("kraken", "USDC").free == D("709")

    asyncio.run(run())


def test_inventory_reservations_and_exposure():
    async def run():
        inv = InventoryManager(lambda a: {"SOL": D("100"), "USDC": D("1")}.get(a))
        inv.update_balances("kraken", {"SOL": Balance("SOL", D("10")), "USDC": Balance("USDC", D("500"))})
        inv.update_balances("solana", {"SOL": Balance("SOL", D("4"))}, kind="chain")
        assert inv.available("kraken", "SOL") == D("10")
        await inv.reserve("t1", [("kraken", "SOL", D("6"))])
        assert inv.available("kraken", "SOL") == D("4")
        with pytest.raises(ValueError):
            await inv.reserve("t2", [("kraken", "SOL", D("5"))])
        await inv.release("t1")
        assert inv.available("kraken", "SOL") == D("10")
        assert inv.total_usd() == D("1900")  # 10*100 + 500 + 4*100
        exp = inv.exposure_by(lambda l: l.venue)
        assert exp == {"kraken": D("1000"), "solana": D("400")}  # stables excluded

    asyncio.run(run())


def test_rebalancer_recommends_only_when_economic():
    inv = InventoryManager(lambda a: D("1"))
    inv.update_balances("kraken", {"USDC": Balance("USDC", D("5000"))})
    inv.update_balances("coinbase", {"USDC": Balance("USDC", D("0"))})
    rb = Rebalancer(RebalanceSettings(min_net_benefit_usd=D("5")), inv)
    assert rb.recommend() == []  # balances differ, but no economic reason
    for _ in range(3):
        rb.note_blocked_by_inventory("coinbase", "USDC", D("2"))
    assert rb.recommend() == []  # benefit (3 usd) < cost
    for _ in range(40):
        rb.note_blocked_by_inventory("coinbase", "USDC", D("4"))
    recs = rb.recommend()
    assert (
        recs
        and recs[0].from_venue == "kraken"
        and recs[0].to_venue == "coinbase"
        and recs[0].net_benefit_usd >= 5
    )


def test_experience_engine_learns_extra_buffer_and_caps():
    async def run():
        e = ExperienceEngine(None, max_extra_buffer_pct=D("0.25"))
        k = route_key(TradingMode.PAPER, Strategy.CEX_CEX, "kraken", "coinbase", "SOL/USDC", D("250"))
        assert k.endswith("|s") and size_bucket(D("50")) == "xs"
        assert e.extra_buffer_pct(k) == 0
        for _ in range(5):  # route repeatedly costs 0.04% more than predicted
            await e.record(
                k,
                notional_usd=D("1000"),
                prediction_error_usd=D("0.4"),
                slippage_variance_usd=D("0.3"),
                fee_variance_usd=D("0.1"),
                gas_variance_usd=D("0"),
                filled=True,
                latency_ms=300,
            )
        assert D("0.03") < e.extra_buffer_pct(k) <= D("0.05")
        for _ in range(10):
            await e.record(
                k,
                notional_usd=D("1000"),
                prediction_error_usd=D("50"),
                slippage_variance_usd=D("0"),
                fee_variance_usd=D("0"),
                gas_variance_usd=D("0"),
                filled=False,
                latency_ms=300,
            )
        assert e.extra_buffer_pct(k) == D("0.25")  # capped - learning never becomes the authority
        e.enabled = False
        assert e.extra_buffer_pct(k) == 0

    asyncio.run(run())
