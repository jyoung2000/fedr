"""Shared EVM nonce coordination (invariant E).

Every signing path for the same chain + account (flash-loan broadcasts, withdrawals) takes its nonce from
one :class:`NonceManager`, under one lock per (chain, address). The next nonce is the maximum of the node's
``pending`` count and the last nonce this process used + 1, so two sends in quick succession can never reuse
a nonce while the first is still in the mempool.

Scope: ONE process. Coordination across processes is not implemented; ``FedrApp`` therefore takes an
exclusive lock on the data directory at startup and refuses to start a second instance on it.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager


class NonceLease:
    def __init__(self, nonce: int):
        self.nonce = nonce
        self.used = False

    def mark_used(self) -> None:
        """Call as soon as a transaction carrying this nonce MAY have been broadcast (even if the send
        call then failed): the nonce is never handed out again by this process."""
        self.used = True


class NonceManager:
    def __init__(self) -> None:
        self._locks: dict[tuple[str, str], asyncio.Lock] = defaultdict(asyncio.Lock)
        self._next: dict[tuple[str, str], int] = {}

    @asynccontextmanager
    async def lease(self, w3, chain: str, address: str) -> AsyncIterator[NonceLease]:
        key = (chain, address.lower())
        async with self._locks[key]:
            pending = int(await w3.eth.get_transaction_count(address, "pending"))
            lease = NonceLease(max(pending, self._next.get(key, 0)))
            try:
                yield lease
            finally:
                if lease.used:
                    self._next[key] = lease.nonce + 1
