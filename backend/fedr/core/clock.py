"""Clock discipline. All FEDR timestamps are UTC epoch milliseconds (``now_ms``); this module measures the
drift between the local clock and each exchange's server clock so signed requests and timestamp-based
market-data checks stay valid. Drift is *reported* and degrades venue health; FEDR never adjusts the clock."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from fedr.core.models import now_ms

WARN_DRIFT_MS = 1_000
UNHEALTHY_DRIFT_MS = 10_000


@dataclass(slots=True)
class DriftSample:
    venue: str
    drift_ms: (
        int | None
    )  # local - server (positive: local clock ahead); None when the venue has no time endpoint
    rtt_ms: int
    error: str | None = None


async def measure_drift(venue) -> DriftSample:
    """Round-trip to the venue's server-time endpoint (ccxt ``fetch_time``); half the RTT is compensated."""
    fetch = getattr(venue, "fetch_server_time_ms", None)
    if fetch is None:
        return DriftSample(venue.name, None, 0, "no server-time endpoint")
    t0 = now_ms()
    try:
        server = await asyncio.wait_for(fetch(), timeout=10)
    except Exception as exc:  # network / venue error: report, never raise into the health loop
        return DriftSample(venue.name, None, now_ms() - t0, str(exc)[:120])
    t1 = now_ms()
    if server is None:
        return DriftSample(venue.name, None, t1 - t0, "no server-time endpoint")
    rtt = t1 - t0
    local_mid = t0 + rtt // 2
    return DriftSample(venue.name, int(local_mid - int(server)), rtt)


def apply_drift(tracker, sample: DriftSample) -> None:
    """Record the sample on the venue's HealthTracker (drift feeds classify())."""
    tracker.clock_drift_ms = sample.drift_ms
    tracker.clock_checked_ms = now_ms()
