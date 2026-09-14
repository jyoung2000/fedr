"""Market-data quality gate + venue health / breaker transitions."""

from __future__ import annotations

import asyncio
from decimal import Decimal

from fedr.connectors.base import HealthTracker
from fedr.core.enums import CircuitBreakerReason, VenueHealth
from fedr.core.models import now_ms
from fedr.engine.circuit_breakers import CircuitBreakerManager
from fedr.marketdata.hub import MarketDataHub
from fedr.marketdata.orderbook import OrderBook
from fedr.marketdata.quality import MarketDataQuality, QualityPolicy

D = Decimal


def book(venue="kraken", mid=100, ts=None, seq=None, spread=D("0.04"), symbol="SOL/USDC"):
    m = D(mid)
    return OrderBook.from_raw(
        venue,
        symbol,
        bids=[[m - spread / 2, 5], [m - spread, 10]],
        asks=[[m + spread / 2, 5], [m + spread, 10]],
        ts_ms=ts or now_ms(),
        sequence=seq,
    )


def test_rejects_crossed_empty_bad_price_future_and_stale():
    q = MarketDataQuality()
    assert q.validate(OrderBook.from_raw("x", "S", [[101, 1]], [[100, 1]])).startswith("crossed")
    assert q.validate(OrderBook.from_raw("x", "S", [], [[100, 1]])).startswith("empty")
    assert q.validate(OrderBook.from_raw("x", "S", [[0, 1]], [[100, 1]])).startswith("bad_price")
    assert q.validate(book(ts=now_ms() + 60_000)).startswith("future_timestamp")
    assert q.validate(book(ts=now_ms() - 120_000)).startswith("stale_on_arrival")
    assert q.validate(book(spread=D("20"))).startswith("abnormal_spread")


def test_sequence_gap_duplicate_and_out_of_order():
    q = MarketDataQuality()
    assert q.validate(book(seq=10)) is None
    assert q.validate(book(seq=10)).startswith("duplicate")
    assert q.validate(book(seq=9)).startswith("out_of_order")
    assert q.validate(book(seq=12)) is None  # gaps forward are accepted (snapshot semantics)
    # timestamp-based ordering when no sequence numbers exist
    q2 = MarketDataQuality()
    t = now_ms()
    assert q2.validate(book(ts=t)) is None
    assert q2.validate(book(ts=t - 5000)).startswith("out_of_order")


def test_cross_source_sanity_check():
    q = MarketDataQuality(QualityPolicy(max_cross_source_deviation_pct=D("2")))
    peers = {"coinbase": book("coinbase", 100), "binance": book("binance", 100.2)}
    assert q.validate(book("kraken", 100.1), peers) is None
    reason = q.validate(book("kraken", 110), peers)
    assert reason and reason.startswith("cross_source") and q.state("kraken").suspicious
    # with only one peer the check is not applied (not enough independent sources)
    assert q.validate(book("kraken", 110), {"coinbase": book("coinbase", 100)}) is None


def test_repeated_rejections_mark_venue_unhealthy_and_recover():
    q = MarketDataQuality(QualityPolicy(consecutive_rejections_unhealthy=3))
    for _ in range(3):
        q.validate(OrderBook.from_raw("kraken", "S", [[101, 1]], [[100, 1]]))
    assert q.is_unhealthy("kraken")
    assert q.validate(book("kraken")) is None
    assert not q.is_unhealthy("kraken")  # one clean book resets the streak


def test_hub_rejects_and_notifies_and_health_reflects_data_state():
    async def run():
        hub = MarketDataHub()
        rejected = []

        async def on_reject(venue, reason):
            rejected.append((venue, reason))

        hub.on_reject = on_reject
        await hub.ingest(book("kraken"))
        assert hub.get_book("kraken", "SOL/USDC") is not None
        await hub.ingest(OrderBook.from_raw("kraken", "SOL/USDC", [[101, 1]], [[100, 1]]))
        assert rejected and rejected[0][0] == "kraken" and hub.rejections["kraken"] == 1
        assert hub.get_book("kraken", "SOL/USDC").best_ask == D(
            "100.02"
        )  # bad snapshot did not replace the good one
        tracker = HealthTracker()
        tracker.last_market_data_ms = now_ms()
        assert tracker.classify("kraken").health is VenueHealth.HEALTHY
        tracker.data_unhealthy = True
        tracker.data_reason = "crossed"
        rep = tracker.classify("kraken")
        assert rep.health is VenueHealth.UNHEALTHY and any("MARKET DATA UNHEALTHY" in r for r in rep.reasons)

    asyncio.run(run())


def test_health_state_transitions():
    t = HealthTracker()
    assert t.classify("v").health is VenueHealth.UNHEALTHY  # no data yet
    t.last_market_data_ms = now_ms()
    assert t.classify("v").health is VenueHealth.HEALTHY
    t.rate_limited_until = __import__("time").monotonic() + 30
    assert t.classify("v").health is VenueHealth.DEGRADED
    t.rate_limited_until = 0
    for _ in range(3):
        t.record_error()
    assert t.classify("v").health is VenueHealth.DEGRADED
    for _ in range(10):
        t.record_error()
    assert t.classify("v").health is VenueHealth.UNHEALTHY
    t.errors.clear()
    t.last_market_data_ms = now_ms() - 70_000
    assert t.classify("v", market_data_max_age_ms=15_000).health is VenueHealth.UNHEALTHY
    t.last_market_data_ms = now_ms() - 20_000
    assert t.classify("v", market_data_max_age_ms=15_000).health is VenueHealth.DEGRADED
    t.last_market_data_ms = now_ms()
    t.maintenance = True
    assert t.classify("v").health is VenueHealth.BLOCKED
    t.maintenance = False
    t.ws_connected = False
    assert t.classify("v", expect_ws=True).health is VenueHealth.DEGRADED
    for _ in range(3):
        t.record_order(False)
    assert t.classify("v").health is VenueHealth.DEGRADED


def test_every_breaker_reason_has_a_scope_and_blocks_or_scopes_correctly():
    async def run():
        cb = CircuitBreakerManager()
        for reason in CircuitBreakerReason:
            await cb.trip(
                reason,
                f"test {reason.value}",
                scope="global"
                if reason
                in (
                    CircuitBreakerReason.DAILY_LOSS_LIMIT,
                    CircuitBreakerReason.BALANCE_DISCREPANCY,
                    CircuitBreakerReason.MANUAL,
                )
                else f"venue:{reason.value}",
            )
        assert cb.is_tripped()
        assert len(cb.active) == len(list(CircuitBreakerReason))
        await cb.reset()
        assert not cb.active
        # venue-scoped breakers block only that venue
        await cb.trip(CircuitBreakerReason.MARKET_DATA_FAILURE, "bad feed", scope="venue:kraken")
        assert cb.blocks(venue="kraken") and not cb.blocks(venue="coinbase") and not cb.is_tripped()

    asyncio.run(run())
