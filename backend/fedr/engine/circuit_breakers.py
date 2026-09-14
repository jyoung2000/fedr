"""CIRCUIT BREAKERS - pause trading on anomalies; manual reset after investigation."""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict, deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from fedr.core.enums import CircuitBreakerReason
from fedr.core.models import now_ms


@dataclass(slots=True)
class Breaker:
    reason: CircuitBreakerReason
    detail: str
    tripped_at_ms: int = field(default_factory=now_ms)
    scope: str = "global"  # global | venue:<name> | chain:<name> | strategy:<name>
    auto: bool = True

    def as_dict(self) -> dict:
        return {
            "reason": self.reason.value,
            "detail": self.detail,
            "tripped_at_ms": self.tripped_at_ms,
            "scope": self.scope,
            "auto": self.auto,
        }


class CircuitBreakerManager:
    """Tracks active breakers and rolling failure counters.

    Persistence is delegated to an optional async ``on_change`` callback so the
    manager stays usable in tests without a database.
    """

    def __init__(self, on_change: Callable[[list[Breaker]], Awaitable[None]] | None = None):
        self._active: dict[str, Breaker] = {}
        self._counters: dict[str, deque[float]] = defaultdict(deque)
        self._on_change = on_change
        self._lock = asyncio.Lock()
        self.history: deque[Breaker] = deque(maxlen=200)

    # ---- state --------------------------------------------------------------------
    @property
    def active(self) -> list[Breaker]:
        return list(self._active.values())

    def is_tripped(self, scope: str = "global") -> bool:
        if "global" in {b.scope for b in self._active.values()}:
            return True
        return any(b.scope == scope for b in self._active.values())

    def blocks(
        self,
        venue: str | None = None,
        chain: str | None = None,
        strategy: str | None = None,
        symbol: str | None = None,
    ) -> list[str]:
        out = []
        for b in self._active.values():
            if b.scope == "global":
                out.append(f"circuit breaker active: {b.reason.value} ({b.detail})")
            elif venue and b.scope == f"venue:{venue}":
                out.append(f"circuit breaker on {venue}: {b.reason.value} ({b.detail})")
            elif chain and b.scope == f"chain:{chain}":
                out.append(f"circuit breaker on chain {chain}: {b.reason.value} ({b.detail})")
            elif strategy and b.scope == f"strategy:{strategy}":
                out.append(f"circuit breaker on strategy {strategy}: {b.reason.value}")
            elif symbol and b.scope == f"symbol:{symbol}":
                out.append(f"circuit breaker on {symbol}: {b.reason.value} ({b.detail})")
        return out

    async def trip(
        self, reason: CircuitBreakerReason, detail: str, scope: str = "global", auto: bool = True
    ) -> Breaker:
        key = f"{scope}|{reason.value}"
        async with self._lock:
            b = self._active.get(key)
            if b is None:
                b = Breaker(reason=reason, detail=detail, scope=scope, auto=auto)
                self._active[key] = b
                self.history.append(b)
            else:
                b.detail = detail
            if self._on_change:
                await self._on_change(self.active)
            return b

    async def reset(self, reason: CircuitBreakerReason | None = None, scope: str | None = None) -> int:
        async with self._lock:
            before = len(self._active)
            for key in list(self._active):
                b = self._active[key]
                if (reason is None or b.reason is reason) and (scope is None or b.scope == scope):
                    del self._active[key]
            if self._on_change:
                await self._on_change(self.active)
            return before - len(self._active)

    def restore(self, breakers: list[Breaker]) -> None:
        for b in breakers:
            self._active[f"{b.scope}|{b.reason.value}"] = b

    # ---- rolling counters ----------------------------------------------------------
    def record(self, counter: str, window_s: float = 3600.0) -> int:
        dq = self._counters[counter]
        now = time.monotonic()
        dq.append(now)
        while dq and now - dq[0] > window_s:
            dq.popleft()
        return len(dq)

    def count(self, counter: str, window_s: float = 3600.0) -> int:
        dq = self._counters[counter]
        now = time.monotonic()
        while dq and now - dq[0] > window_s:
            dq.popleft()
        return len(dq)

    async def record_and_check(
        self,
        counter: str,
        threshold: int,
        reason: CircuitBreakerReason,
        detail: str,
        scope: str = "global",
        window_s: float = 3600.0,
    ) -> bool:
        n = self.record(counter, window_s)
        if n >= threshold:
            await self.trip(reason, f"{detail} ({n} in window)", scope=scope)
            return True
        return False
