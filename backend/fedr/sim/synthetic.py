"""Synthetic market generator - SIMULATION MODE ONLY.

Produces deterministic, plausible multi-venue order books, DEX pool quotes and
gas conditions so the whole pipeline (scanner -> Profit Guard -> execution ->
reconciliation) can be exercised without network access. It is never used in
PAPER, TESTNET or LIVE modes and every quote it produces is tagged
``source="synthetic"``.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from decimal import Decimal

from fedr.core.enums import Chain, OrderSide
from fedr.core.models import now_ms
from fedr.core.money import D
from fedr.marketdata.orderbook import Level, OrderBook

BASE_PRICES = {"BTC": Decimal("64000"), "ETH": Decimal("3200"), "SOL": Decimal("150"), "USDC": Decimal("1"), "USDT": Decimal("1"), "USD": Decimal("1")}
QUOTE_USD = {"USDC": Decimal("1"), "USDT": Decimal("0.9998"), "USD": Decimal("1")}


@dataclass
class VenueProfile:
    name: str
    kind: str  # cex | dex
    offset_bps: float = 0.0  # persistent price offset vs fair mid
    spread_bps: float = 4.0
    depth_scale: float = 1.0  # multiplier on depth
    vol_bps: float = 3.0  # per-tick offset noise
    dislocation_prob: float = 0.03  # chance per tick of a temporary dislocation
    dislocation_bps: float = 35.0
    chain: Chain | None = None
    pool_fee_pct: Decimal = Decimal("0.30")
    pool_liquidity_usd: float = 2_000_000.0
    state_offset: float = field(default=0.0)
    dislocation_ttl: int = 0


@dataclass
class ChainProfile:
    chain: Chain
    base_gas: float  # gwei or lamports/CU
    priority: float
    native_usd: Decimal
    spike_prob: float = 0.02
    current: float = 0.0
    spike_ttl: int = 0


class SyntheticMarket:
    def __init__(self, pairs: list[str], venues: list[VenueProfile], chains: list[ChainProfile], seed: int = 42):
        self.rng = random.Random(seed)
        self.pairs = pairs
        self.venues = {v.name: v for v in venues}
        self.chains = {c.chain: c for c in chains}
        self.fair: dict[str, float] = {}
        for p in pairs:
            base = p.split("/")[0]
            self.fair[p] = float(BASE_PRICES.get(base, Decimal("10")))
        for c in self.chains.values():
            c.current = c.base_gas
        self.tick_count = 0

    # ------------------------------------------------------------------ evolution
    def tick(self) -> None:
        self.tick_count += 1
        for p in self.pairs:
            self.fair[p] *= math.exp(self.rng.gauss(0, 0.0004))
        for v in self.venues.values():
            v.state_offset = v.state_offset * 0.85 + self.rng.gauss(0, v.vol_bps)
            if v.dislocation_ttl > 0:
                v.dislocation_ttl -= 1
            elif self.rng.random() < v.dislocation_prob:
                v.dislocation_ttl = self.rng.randint(2, 6)
                v.state_offset += self.rng.choice([-1, 1]) * v.dislocation_bps
        for c in self.chains.values():
            if c.spike_ttl > 0:
                c.spike_ttl -= 1
                c.current = c.base_gas * self.rng.uniform(2.5, 5.0)
            elif self.rng.random() < c.spike_prob:
                c.spike_ttl = self.rng.randint(3, 8)
            else:
                c.current = c.current * 0.8 + c.base_gas * self.rng.uniform(0.8, 1.3) * 0.2

    # ------------------------------------------------------------------ books
    def mid(self, venue: str, pair: str) -> float:
        v = self.venues[venue]
        return self.fair[pair] * (1 + (v.offset_bps + v.state_offset) / 10_000)

    def order_book(self, venue: str, pair: str, levels: int = 25) -> OrderBook:
        v = self.venues[venue]
        mid = self.mid(venue, pair)
        half = mid * v.spread_bps / 20_000
        base = pair.split("/")[0]
        unit = {"BTC": 0.02, "ETH": 0.4, "SOL": 8.0}.get(base, 100.0) * v.depth_scale
        bids, asks = [], []
        for i in range(levels):
            step = mid * 0.00015 * (i + 1) ** 1.15
            size = unit * (1 + i * 0.6) * self.rng.uniform(0.7, 1.3)
            bids.append(Level(D(round(mid - half - step, 6)), D(round(size, 6))))
            asks.append(Level(D(round(mid + half + step, 6)), D(round(size, 6))))
        ob = OrderBook(venue=venue, symbol=pair, bids=bids, asks=asks, ts_ms=now_ms(), source="synthetic")
        return ob

    def dex_quote(self, venue: str, pair: str, side: OrderSide, base_amount: Decimal) -> dict:
        """Constant-product style quote with fee + price impact for a synthetic pool."""
        v = self.venues[venue]
        spot = self.mid(venue, pair)
        liq = v.pool_liquidity_usd
        reserve_quote = liq / 2
        reserve_base = reserve_quote / spot
        fee = float(v.pool_fee_pct) / 100
        amt = float(base_amount)
        if side is OrderSide.BUY:
            # need `amt` base out: quote_in = rq * amt / (rb - amt) / (1 - fee)
            if amt >= reserve_base * 0.5:
                return {"ok": False, "reason": "insufficient pool liquidity"}
            quote_in = reserve_quote * amt / (reserve_base - amt) / (1 - fee)
            eff = quote_in / amt
            impact = (eff * (1 - fee) - spot) / spot * 100
            return {"ok": True, "quote_amount": D(round(quote_in, 8)), "avg_price": D(round(eff, 8)), "reference": D(round(spot, 8)), "impact_pct": D(round(max(0.0, impact), 6)), "fee_pct": v.pool_fee_pct}
        base_in_after_fee = amt * (1 - fee)
        quote_out = reserve_quote * base_in_after_fee / (reserve_base + base_in_after_fee)
        eff = quote_out / amt
        impact = (spot - eff / (1 - fee)) / spot * 100
        return {"ok": True, "quote_amount": D(round(quote_out, 8)), "avg_price": D(round(eff, 8)), "reference": D(round(spot, 8)), "impact_pct": D(round(max(0.0, impact), 6)), "fee_pct": v.pool_fee_pct}

    def gas(self, chain: Chain) -> dict:
        c = self.chains[chain]
        return {"gas_price": D(round(c.current, 6)), "priority": D(round(c.priority, 6)), "native_usd": c.native_usd, "baseline": D(round(c.base_gas, 6))}

    def price_usd(self, asset: str) -> Decimal | None:
        if asset in QUOTE_USD:
            return QUOTE_USD[asset]
        for p, px in self.fair.items():
            if p.split("/")[0] == asset:
                return D(round(px, 6))
        return None


def default_synthetic(pairs: list[str], seed: int = 42) -> SyntheticMarket:
    venues = [
        VenueProfile("kraken", "cex", offset_bps=-2.0, spread_bps=5.0, depth_scale=1.2, dislocation_prob=0.04, dislocation_bps=30),
        VenueProfile("coinbase", "cex", offset_bps=3.0, spread_bps=4.0, depth_scale=1.5, dislocation_prob=0.04, dislocation_bps=30),
        VenueProfile("binance", "cex", offset_bps=0.0, spread_bps=2.0, depth_scale=3.0, dislocation_prob=0.02, dislocation_bps=20),
        VenueProfile("jupiter", "dex", offset_bps=6.0, spread_bps=0.0, vol_bps=6.0, dislocation_prob=0.06, dislocation_bps=45, chain=Chain.SOLANA, pool_fee_pct=Decimal("0.25"), pool_liquidity_usd=3_000_000),
        VenueProfile("uniswap-base", "dex", offset_bps=-4.0, spread_bps=0.0, vol_bps=6.0, dislocation_prob=0.06, dislocation_bps=45, chain=Chain.BASE, pool_fee_pct=Decimal("0.30"), pool_liquidity_usd=1_500_000),
        VenueProfile("uniswap-arbitrum", "dex", offset_bps=2.0, spread_bps=0.0, vol_bps=6.0, dislocation_prob=0.05, dislocation_bps=40, chain=Chain.ARBITRUM, pool_fee_pct=Decimal("0.05"), pool_liquidity_usd=2_500_000),
    ]
    chains = [
        ChainProfile(Chain.SOLANA, base_gas=0.2, priority=0.2, native_usd=BASE_PRICES["SOL"]),  # lamports per CU
        ChainProfile(Chain.BASE, base_gas=0.05, priority=0.001, native_usd=BASE_PRICES["ETH"]),  # gwei
        ChainProfile(Chain.ARBITRUM, base_gas=0.1, priority=0.001, native_usd=BASE_PRICES["ETH"]),
        ChainProfile(Chain.ETHEREUM, base_gas=12.0, priority=1.0, native_usd=BASE_PRICES["ETH"], spike_prob=0.04),
    ]
    return SyntheticMarket(pairs, venues, chains, seed=seed)
