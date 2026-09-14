"""Clock drift measurement degrades venue health; timestamps are UTC epoch ms everywhere."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from fedr.connectors.base import HealthTracker, VenueHealth
from fedr.core.clock import apply_drift, measure_drift
from fedr.core.models import now_ms
from fedr.db.models import utcnow


class _Venue:
    name = "v"

    def __init__(self, offset_ms):
        self.offset = offset_ms

    async def fetch_server_time_ms(self):
        return now_ms() - self.offset


def _healthy_tracker():
    t = HealthTracker()
    t.last_market_data_ms = now_ms()
    return t


def test_drift_is_measured_and_classified():
    async def run():
        for offset, expect in (
            (0, VenueHealth.HEALTHY),
            (3_000, VenueHealth.DEGRADED),
            (20_000, VenueHealth.UNHEALTHY),
        ):
            s = await measure_drift(_Venue(offset))
            assert abs(s.drift_ms - offset) < 200, s
            t = _healthy_tracker()
            apply_drift(t, s)
            rep = t.classify("v")
            assert rep.health is expect, (offset, rep)
            if offset:
                assert any("clock drift" in r for r in rep.reasons)

    asyncio.run(run())


def test_missing_endpoint_and_errors_do_not_degrade():
    class NoTime:
        name = "n"

    class Broken:
        name = "b"

        async def fetch_server_time_ms(self):
            raise RuntimeError("boom")

    async def run():
        s = await measure_drift(NoTime())
        assert s.drift_ms is None and "no server-time" in s.error
        s = await measure_drift(Broken())
        assert s.drift_ms is None and "boom" in s.error
        t = _healthy_tracker()
        apply_drift(t, s)
        assert t.classify("b").health is VenueHealth.HEALTHY

    asyncio.run(run())


def test_all_timestamps_are_utc():
    assert utcnow().tzinfo is UTC or utcnow().utcoffset().total_seconds() == 0
    assert abs(now_ms() - int(datetime.now(UTC).timestamp() * 1000)) < 1000
