"""Market-data quality gate.

Every order book passes through ``validate_book`` before it reaches the hub cache:
timestamp sanity (future / too old), non-positive prices, crossed books, sequence
regressions (out-of-order), duplicates, absurd spreads and cross-source deviation
against the median mid of the other venues. Rejections are counted per venue and
feed venue health; a venue whose data keeps failing is marked MARKET DATA UNHEALTHY
and the strategy engine stops using it (the risk engine refuses unhealthy venues).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from statistics import median

from fedr.core.models import now_ms
from fedr.core.money import ZERO, pct
from fedr.marketdata.orderbook import OrderBook


@dataclass(slots=True)
class QualityPolicy:
    max_future_skew_ms: int = 5_000  # exchange timestamp may not be ahead of our clock by more than this
    max_age_ms: int = 60_000  # a snapshot older than this on arrival is rejected
    max_spread_pct: Decimal = Decimal("5")  # wider than this is treated as a broken book
    max_cross_source_deviation_pct: Decimal = Decimal("3")  # mid vs median of other venues
    min_other_sources: int = 2
    consecutive_rejections_unhealthy: int = 5


@dataclass(slots=True)
class VenueQuality:
    last_sequence: int | None = None
    last_ts_ms: int = 0
    rejected: int = 0
    accepted: int = 0
    consecutive_rejections: int = 0
    last_reason: str | None = None
    suspicious: bool = False  # cross-source deviation flagged on the last accepted book
    reasons: dict[str, int] = field(default_factory=dict)


class MarketDataQuality:
    def __init__(self, policy: QualityPolicy | None = None):
        self.policy = policy or QualityPolicy()
        self.venues: dict[str, VenueQuality] = {}

    def state(self, venue: str) -> VenueQuality:
        return self.venues.setdefault(venue, VenueQuality())

    def is_unhealthy(self, venue: str) -> bool:
        v = self.venues.get(venue)
        return bool(v and v.consecutive_rejections >= self.policy.consecutive_rejections_unhealthy)

    def validate(self, book: OrderBook, peers: dict[str, OrderBook] | None = None) -> str | None:
        """Return None when the book is acceptable, else the rejection reason."""
        st = self.state(book.venue)
        reason = self._check(book, st, peers or {})
        if reason is None:
            st.accepted += 1
            st.consecutive_rejections = 0
            st.last_reason = None
            st.last_sequence = book.sequence if book.sequence is not None else st.last_sequence
            st.last_ts_ms = max(st.last_ts_ms, book.ts_ms)
        else:
            st.rejected += 1
            st.consecutive_rejections += 1
            st.last_reason = reason
            st.reasons[reason.split(":")[0]] = st.reasons.get(reason.split(":")[0], 0) + 1
        return reason

    def _check(self, book: OrderBook, st: VenueQuality, peers: dict[str, OrderBook]) -> str | None:
        p = self.policy
        now = now_ms()
        if not book.bids or not book.asks:
            return "empty: one side of the book is empty"
        if book.is_crossed():
            return f"crossed: bid {book.best_bid} >= ask {book.best_ask}"
        if any(lv.price <= 0 or lv.amount <= 0 for lv in book.bids[:5] + book.asks[:5]):
            return "bad_price: non-positive price or size"
        if book.ts_ms > now + p.max_future_skew_ms:
            return f"future_timestamp: exchange ts {book.ts_ms - now} ms ahead of local clock"
        if now - book.ts_ms > p.max_age_ms:
            return f"stale_on_arrival: snapshot is {(now - book.ts_ms) // 1000}s old"
        if book.sequence is not None and st.last_sequence is not None:
            if book.sequence < st.last_sequence:
                return f"out_of_order: sequence {book.sequence} < {st.last_sequence}"
            if book.sequence == st.last_sequence:
                return "duplicate: same sequence as the previous snapshot"
        elif book.sequence is None and book.ts_ms and book.ts_ms < st.last_ts_ms - 1000:
            return f"out_of_order: timestamp {book.ts_ms} older than the last accepted {st.last_ts_ms}"
        spread = book.spread_pct
        if spread is not None and spread > p.max_spread_pct:
            return f"abnormal_spread: {spread:.2f}% wider than {p.max_spread_pct}%"
        # cross-source sanity: compare mid against the median of the other venues' mids for the same symbol
        others = [
            b.mid
            for v, b in peers.items()
            if v != book.venue
            and b.symbol == book.symbol
            and b.mid is not None
            and now - b.ts_ms < p.max_age_ms
        ]
        st.suspicious = False
        if len(others) >= p.min_other_sources and book.mid is not None:
            med = median(others)
            dev = abs(pct(book.mid - med, med)) if med > 0 else ZERO
            if dev > p.max_cross_source_deviation_pct:
                st.suspicious = True
                return f"cross_source: mid deviates {dev:.2f}% from the median of {len(others)} other venues"
        return None

    def summary(self) -> dict[str, dict]:
        return {
            v: {
                "accepted": s.accepted,
                "rejected": s.rejected,
                "consecutive_rejections": s.consecutive_rejections,
                "last_reason": s.last_reason,
                "unhealthy": self.is_unhealthy(v),
                "reasons": s.reasons,
            }
            for v, s in self.venues.items()
        }
