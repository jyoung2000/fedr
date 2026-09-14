"""Synthetic venue connectors (SIMULATION mode only). They expose the same
VenueConnector interface as real connectors but never touch the network."""
from __future__ import annotations

from decimal import Decimal

from fedr.connectors.base import VenueConnector
from fedr.core.enums import OrderSide, TradingMode, VenueKind, VerificationLevel
from fedr.core.models import Balance, ExecutionQuote, FeeSchedule, MarketInfo, OrderRequest, OrderResult, now_ms
from fedr.core.money import ZERO
from fedr.marketdata.orderbook import OrderBook
from fedr.sim.synthetic import SyntheticMarket


class SyntheticCexVenue(VenueConnector):
    def __init__(self, market: SyntheticMarket, name: str, pairs: list[str], taker_fee_pct: Decimal):
        super().__init__(name, VenueKind.CEX, TradingMode.SIMULATION, display_name=f"{name.capitalize()} (simulated)")
        self.market = market
        self.pairs = pairs
        self.taker_fee_pct = taker_fee_pct
        self.capabilities.update({"orderbook", "synthetic"})
        self.verification = VerificationLevel.NOT_VERIFIED_LIVE

    async def connect(self) -> None:
        await self.load_markets()
        self.connected = True

    async def close(self) -> None:
        self.connected = False

    async def load_markets(self) -> dict[str, MarketInfo]:
        self.markets = {
            p: MarketInfo(venue=self.name, symbol=p, base=p.split("/")[0], quote=p.split("/")[1], kind=VenueKind.CEX, amount_step=Decimal("0.0001"), price_step=Decimal("0.01"), min_amount=Decimal("0.0001"), min_cost=Decimal("5"), taker_fee_pct=self.taker_fee_pct, maker_fee_pct=self.taker_fee_pct / 2)
            for p in self.pairs
        }
        return self.markets

    async def fetch_order_book(self, symbol: str, depth: int = 50) -> OrderBook:
        ob = self.market.order_book(self.name, symbol, levels=min(depth, 40))
        self.health_tracker.last_market_data_ms = now_ms()
        self.health_tracker.record_latency(20.0)
        return ob

    async def get_quote(self, symbol: str, side: OrderSide, base_amount: Decimal, *, order_book: OrderBook | None = None) -> ExecutionQuote:
        ob = order_book or await self.fetch_order_book(symbol)
        w = ob.walk(side, base_amount)
        return ExecutionQuote(
            venue=self.name, kind=VenueKind.CEX, symbol=symbol, side=side, base_amount=base_amount, quote_amount=w.quote_amount, avg_price=w.avg_price, reference_price=w.reference_price, slippage_pct=w.slippage_pct, price_impact_pct=ZERO, fully_fillable=w.fully_filled, market_ts_ms=ob.ts_ms, fee_pct=self.taker_fee_pct, fee_source="simulated_schedule", route=f"{self.display_name} book ({w.levels_consumed} levels)", levels_consumed=w.levels_consumed, depth_available_base=w.depth_available_base, extra={"limit_price": str(w.last_price), "taker_fee_pct": str(self.taker_fee_pct)},
        )

    async def fetch_fees(self, symbol: str) -> FeeSchedule:
        return FeeSchedule(self.taker_fee_pct, self.taker_fee_pct / 2, "simulated_schedule")

    async def fetch_balances(self) -> dict[str, Balance]:
        return {}

    async def place_order(self, req: OrderRequest) -> OrderResult:
        raise RuntimeError("synthetic venues never execute directly - use the paper executor")


class SyntheticDexVenue(VenueConnector):
    def __init__(self, market: SyntheticMarket, name: str, pairs: list[str], chain, gas_limit: int = 300_000):
        super().__init__(name, VenueKind.DEX, TradingMode.SIMULATION, display_name=f"{name} (simulated)")
        self.market = market
        self.pairs = pairs
        self.chain = chain
        self.gas_limit = gas_limit
        self.capabilities.update({"quote", "swap", "gas", "synthetic"})

    async def connect(self) -> None:
        await self.load_markets()
        self.connected = True

    async def close(self) -> None:
        self.connected = False

    async def load_markets(self) -> dict[str, MarketInfo]:
        fee = self.market.venues[self.name].pool_fee_pct
        self.markets = {p: MarketInfo(venue=self.name, symbol=p, base=p.split("/")[0], quote=p.split("/")[1], kind=VenueKind.DEX, amount_step=Decimal("0.000001"), taker_fee_pct=fee, chain=self.chain) for p in self.pairs}
        return self.markets

    async def get_quote(self, symbol: str, side: OrderSide, base_amount: Decimal, *, order_book: OrderBook | None = None) -> ExecutionQuote:
        q = self.market.dex_quote(self.name, symbol, side, base_amount)
        self.health_tracker.last_market_data_ms = now_ms()
        self.health_tracker.record_latency(120.0)
        if not q["ok"]:
            return ExecutionQuote(venue=self.name, kind=VenueKind.DEX, symbol=symbol, side=side, base_amount=base_amount, quote_amount=ZERO, avg_price=ZERO, reference_price=ZERO, slippage_pct=ZERO, price_impact_pct=ZERO, fully_fillable=False, market_ts_ms=now_ms(), chain=self.chain, gas_limit=self.gas_limit, route=q["reason"])
        return ExecutionQuote(
            venue=self.name, kind=VenueKind.DEX, symbol=symbol, side=side, base_amount=base_amount, quote_amount=q["quote_amount"], avg_price=q["avg_price"], reference_price=q["reference"], slippage_pct=ZERO, price_impact_pct=q["impact_pct"], fully_fillable=True, market_ts_ms=now_ms(), fee_pct=q["fee_pct"], fee_source="simulated_pool", gas_limit=self.gas_limit, chain=self.chain, route=f"{self.display_name} pool", quote_id=f"sim_{now_ms()}",
            min_received=q["quote_amount"] * Decimal("0.995") if side is OrderSide.SELL else None,
        )

    async def fetch_fees(self, symbol: str) -> FeeSchedule:
        return FeeSchedule(self.market.venues[self.name].pool_fee_pct, None, "simulated_pool")

    async def fetch_balances(self) -> dict[str, Balance]:
        return {}

    async def place_order(self, req: OrderRequest) -> OrderResult:
        raise RuntimeError("synthetic venues never execute directly - use the paper executor")
