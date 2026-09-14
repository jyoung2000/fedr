from decimal import Decimal

import pytest

from fedr.core.enums import OrderSide
from fedr.marketdata.orderbook import OrderBook
from tests.conftest import make_book


def test_walk_buy_vwap_and_slippage():
    ob = OrderBook.from_raw("x", "SOL/USDC", bids=[[100, 5], [99.9, 10]], asks=[[100.1, 2], [100.2, 3], [100.5, 10]])
    w = ob.walk(OrderSide.BUY, Decimal("4"))
    assert w.fully_filled and w.levels_consumed == 2
    assert w.quote_amount == Decimal("100.1") * 2 + Decimal("100.2") * 2
    assert w.avg_price == w.quote_amount / 4
    assert w.slippage_pct > 0 and w.last_price == Decimal("100.2")


def test_walk_sell_consumes_bids_descending():
    ob = OrderBook.from_raw("x", "SOL/USDC", bids=[[99.9, 10], [100, 5]], asks=[[100.1, 2]])
    w = ob.walk(OrderSide.SELL, Decimal("6"))
    assert w.avg_price == (Decimal(100) * 5 + Decimal("99.9") * 1) / 6
    assert w.reference_price == Decimal(100)


def test_walk_insufficient_depth_reports_partial():
    ob = OrderBook.from_raw("x", "SOL/USDC", bids=[[100, 1]], asks=[[101, 1]])
    w = ob.walk(OrderSide.BUY, Decimal("5"))
    assert not w.fully_filled and w.filled_base == 1 and w.depth_available_base == 1


def test_walk_quote_amount():
    ob = OrderBook.from_raw("x", "SOL/USDC", bids=[[100, 1]], asks=[[100, 1], [110, 1]])
    w = ob.walk_quote(OrderSide.BUY, Decimal("155"))
    assert w.fully_filled and w.filled_base == Decimal(1) + Decimal(55) / Decimal(110)


def test_max_size_within_slippage():
    ob = make_book("x", "SOL/USDC", Decimal("100"), spread_bps=Decimal("2"), step_bps=Decimal("10"), size=Decimal("1"))
    cap = ob.max_size_within_slippage(OrderSide.BUY, Decimal("0.05"))
    assert Decimal(0) < cap < ob.depth_base(OrderSide.BUY)


def test_crossed_book_invalid_and_stress_thins_depth():
    crossed = OrderBook.from_raw("x", "SOL/USDC", bids=[[101, 1]], asks=[[100, 1]])
    assert crossed.is_crossed() and not crossed.is_valid()
    ob = make_book("x", "SOL/USDC", Decimal("100"))
    thin = ob.apply_stress(Decimal("2"))
    assert thin.depth_base(OrderSide.BUY) == ob.depth_base(OrderSide.BUY) / 2


def test_consume_mutates_book():
    ob = OrderBook.from_raw("x", "SOL/USDC", bids=[[100, 1]], asks=[[100, 1], [101, 2]])
    ob.consume(OrderSide.BUY, Decimal("2"))
    assert ob.best_ask == Decimal(101) and ob.asks[0].amount == 1


def test_walk_rejects_non_positive():
    ob = make_book("x", "SOL/USDC", Decimal("100"))
    with pytest.raises(ValueError):
        ob.walk(OrderSide.BUY, Decimal("0"))
