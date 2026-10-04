"""Connector-level regression tests for audit F02 (uncertain outcomes), F07 (derivative order semantics)
and F08 (authoritative fill values) against scripted fake venues - no network.

Each test reproduces the audited counterexample first and then asserts the repaired behaviour:
* a transport failure / failed cancel / empty status query never becomes CANCELLED or FAILED;
* the client order id is the lookup key, and one economic intent is sent once;
* a fill reported without trade details keeps its venue-reported cost (never quote value 0);
* reduce_only reaches the venue and swap amounts are converted base <-> contracts.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal

from ccxt.base.errors import NetworkError, OrderNotFound, RequestTimeout

from fedr.connectors.cex.ccxt_connector import CcxtConnector
from fedr.connectors.dex.gateway_client import GatewayError
from fedr.connectors.dex.gateway_connector import GatewayConnector
from fedr.connectors.dex.registry import DEXES
from fedr.core.enums import OrderSide, OrderStatus, Strategy, TradeStatus, TradingMode, VenueKind
from fedr.core.models import MarketInfo, OrderRequest, TradeRecord
from fedr.engine.accounting import compute_outcome

D = Decimal


class FakeExchange:
    """Minimal async ccxt surface with scripted behaviour."""

    def __init__(self):
        self.orders: dict[str, dict] = {}  # venue id -> order
        self.by_client: dict[str, str] = {}
        self.create_calls: list[tuple] = []
        self.cancel_calls = 0
        self.create_raises: Exception | None = None  # raised AFTER the order was accepted
        self.cancel_raises: Exception | None = None
        self.fetch_raises: Exception | None = None
        self.fetch_returns_none = False
        self.fill_on_create: dict | None = None  # fields merged into the accepted order
        self.trades: list[dict] = []
        self.has = {
            "fetchOpenOrders": True,
            "fetchClosedOrders": True,
            "fetchMyTrades": True,
            "fetchCanceledOrders": False,
        }

    async def create_order(self, symbol, type_, side, amount, price, params):
        cid = params.get("clientOrderId")
        self.create_calls.append((symbol, side, amount, price, dict(params)))
        oid = f"v{len(self.create_calls)}"
        o = {
            "id": oid,
            "clientOrderId": cid,
            "symbol": symbol,
            "side": side,
            "amount": amount,
            "price": price,
            "status": "open",
            "filled": 0,
            "remaining": amount,
            "trades": [],
        }
        o.update(self.fill_on_create or {})
        self.orders[oid] = o
        self.by_client[cid] = oid
        if self.create_raises is not None:
            raise self.create_raises  # the venue accepted it, but the response was lost
        return dict(o)

    async def fetch_order(self, oid, symbol, params=None):
        if self.fetch_raises is not None:
            raise self.fetch_raises
        if self.fetch_returns_none:
            raise OrderNotFound("not visible")
        if oid is None:
            oid = self.by_client.get((params or {}).get("clientOrderId"))
        if oid not in self.orders:
            raise OrderNotFound("unknown")
        return dict(self.orders[oid])

    async def cancel_order(self, oid, symbol, params=None):
        self.cancel_calls += 1
        if self.cancel_raises is not None:
            raise self.cancel_raises
        o = self.orders[oid]
        if o["status"] == "open":
            o["status"] = "canceled"
        return dict(o)

    async def fetch_open_orders(self, symbol=None):
        if self.fetch_raises is not None:
            raise self.fetch_raises
        return [dict(o) for o in self.orders.values() if o["status"] == "open"]

    async def fetch_closed_orders(self, symbol=None, since=None):
        if self.fetch_raises is not None:
            raise self.fetch_raises
        return [dict(o) for o in self.orders.values() if o["status"] != "open"]

    async def fetch_my_trades(self, symbol, since=None, limit=None):
        return list(self.trades)

    async def close(self):
        return None


def _connector(market_type="spot", contract_size=D("1")) -> tuple[CcxtConnector, FakeExchange]:
    c = CcxtConnector("kraken", TradingMode.TESTNET, {"apiKey": "k", "secret": "s"}, market_type=market_type)
    ex = FakeExchange()
    c.exchange = ex
    sym = "SOL/USDC:USDC" if market_type == "swap" else "SOL/USDC"
    c.markets[sym] = MarketInfo(
        venue="kraken",
        symbol=sym,
        base="SOL",
        quote="USDC",
        kind=VenueKind.PERP if market_type == "swap" else VenueKind.CEX,
        amount_step=D("0.001") if market_type == "spot" else D("1"),
        price_step=D("0.01"),
        min_amount=D("0.001"),
        min_cost=D("0"),
        contract_size=contract_size,
    )
    return c, ex


def _req(amount="1", side=OrderSide.BUY, symbol="SOL/USDC", reduce_only=False, deadline_ms=300):
    return OrderRequest(
        venue="kraken",
        symbol=symbol,
        side=side,
        amount=D(amount),
        limit_price=D("100"),
        deadline_ms=deadline_ms,
        reduce_only=reduce_only,
    )


def test_open_order_with_failed_cancel_and_empty_status_is_unknown_not_cancelled():
    """Audit F02 reproduction: order stays open, cancel raises a network error, the final status query
    returns nothing. The old adapter returned CANCELLED/terminal; the repaired one returns UNKNOWN."""

    async def run():
        c, ex = _connector()
        ex.cancel_raises = NetworkError("connection reset")

        async def fetch_none(oid, symbol, params=None):
            raise NetworkError("status endpoint down")

        orig_create = ex.create_order

        async def create(*a):
            r = await orig_create(*a)
            ex.fetch_order = fetch_none  # every status query fails from now on
            return r

        ex.create_order = create
        res = await c.place_order(_req())
        assert res.status is OrderStatus.UNKNOWN
        assert not res.is_terminal and res.outcome_unknown
        assert len(ex.create_calls) == 1

    asyncio.run(run())


def test_lost_response_after_acceptance_is_resolved_by_client_order_id_without_resubmitting():
    async def run():
        c, ex = _connector()
        ex.create_raises = RequestTimeout("read timeout")
        ex.fill_on_create = {
            "status": "closed",
            "filled": 1,
            "remaining": 0,
            "average": 100,
            "cost": 100,
            "trades": [{"id": "t1", "amount": 1, "price": 100, "fee": {"cost": 0.1, "currency": "USDC"}}],
        }
        req = _req()
        res = await c.place_order(req)
        assert len(ex.create_calls) == 1  # one economic intent, sent once
        assert ex.create_calls[0][4]["clientOrderId"] == req.client_order_id
        assert res.status is OrderStatus.FILLED and res.filled == D("1") and res.order_id == "v1"
        assert res.quote_value == D("100") and res.fee_known

    asyncio.run(run())


def test_lost_response_and_unreachable_venue_stays_unknown():
    async def run():
        c, ex = _connector()
        ex.create_raises = RequestTimeout("read timeout")
        ex.fetch_raises = NetworkError("down")
        res = await c.place_order(_req())
        assert res.status is OrderStatus.UNKNOWN and res.outcome_unknown
        # later, the venue is reachable again: resolution by client order id finds the real outcome
        ex.fetch_raises = None
        o = next(iter(ex.orders.values()))
        o.update({"status": "closed", "filled": 1, "remaining": 0, "average": 100, "cost": 100})
        ex.trades = [
            {
                "id": "t9",
                "order": o["id"],
                "amount": 1,
                "price": 100,
                "fee": {"cost": 0.1, "currency": "USDC"},
            }
        ]
        await c.resolve_order(res)
        assert res.status is OrderStatus.FILLED and res.quote_value == D("100") and res.fee_known

    asyncio.run(run())


def test_definitive_rejection_is_rejected():
    from ccxt.base.errors import InsufficientFunds

    async def run():
        c, ex = _connector()

        async def reject(*a, **k):
            raise InsufficientFunds("no balance")

        ex.create_order = reject
        res = await c.place_order(_req())
        assert res.status is OrderStatus.REJECTED and res.is_terminal and res.filled == 0

    asyncio.run(run())


def test_fill_without_trade_details_keeps_venue_cost_and_unknown_fee():
    """Audit F08 reproduction: closed order filled=1 average=100 cost=100 without trades used to give
    quote_amount 0. Now the venue cost is the value and the missing fee is UNKNOWN (P&L not booked)."""

    async def run():
        c, ex = _connector()
        ex.fill_on_create = {"status": "closed", "filled": 1, "remaining": 0, "average": 100, "cost": 100}
        res = await c.place_order(_req())
        assert res.status is OrderStatus.FILLED
        assert res.quote_value == D("100")  # was 0
        assert not res.fee_known  # no trade details, no order fee: unknown, not zero
        tr = TradeRecord(
            id="t",
            mode=TradingMode.TESTNET,
            strategy=Strategy.CEX_CEX,
            opportunity_id="o",
            pair="SOL/USDC",
            route="a",
            size_base=D("1"),
            status=TradeStatus.EXECUTING,
            estimated_gross=D("1"),
            estimated_net=D("1"),
            estimated_worst_case=D("0"),
            estimated_costs={},
            buy=res,
        )
        out = compute_outcome(tr, D("1"))
        assert not out.complete and any("fee" in m for m in out.missing)

    asyncio.run(run())


def test_order_level_fee_makes_fill_complete_and_repeated_trade_arrays_do_not_double_count():
    async def run():
        c, ex = _connector()
        trade = {"id": "t1", "amount": 0.5, "price": 100, "fee": {"cost": 0.05, "currency": "USDC"}}
        req = _req()
        from fedr.core.models import OrderResult

        res = OrderResult(request=req, order_id="v1", status=OrderStatus.SUBMITTING)
        c._apply_order(res, {"id": "v1", "status": "open", "filled": 0.5, "trades": [trade]})
        c._apply_order(res, {"id": "v1", "status": "open", "filled": 0.5, "trades": [trade]})  # replayed
        c._apply_order(
            res,
            {
                "id": "v1",
                "status": "closed",
                "filled": 1,
                "remaining": 0,
                "trades": [
                    trade,
                    {"id": "t2", "amount": 0.5, "price": 101, "fee": {"cost": 0.05, "currency": "USDC"}},
                ],
            },
        )
        assert len(res.fills) == 2 and res.filled == D("1")
        assert res.quote_value == D("100.5") and res.fee_quote == D("0.10") and res.fee_known

    asyncio.run(run())


def test_reduce_only_and_contract_size_reach_the_venue():
    """Audit F07 reproduction: reduce_only never reached create_order and contractSize was ignored."""

    async def run():
        c, ex = _connector(market_type="swap", contract_size=D("0.1"))
        ex.fill_on_create = {
            "status": "closed",
            "filled": 10,  # contracts
            "remaining": 0,
            "average": 100,
            "cost": 100,
            "trades": [{"id": "t1", "amount": 10, "price": 100, "fee": {"cost": 0.05, "currency": "USDC"}}],
        }
        res = await c.place_order(
            _req(amount="1", side=OrderSide.BUY, symbol="SOL/USDC:USDC", reduce_only=True)
        )
        _sym, _side, amount, _px, params = ex.create_calls[0]
        assert params.get("reduceOnly") is True
        assert amount == 10.0  # 1 SOL / 0.1 SOL per contract
        assert res.filled == D("1") and res.fills[0].amount == D("1")  # back in base units
        assert res.quote_value == D("100")

    asyncio.run(run())


def test_reduce_only_on_spot_is_refused():
    async def run():
        c, ex = _connector()
        try:
            await c.place_order(_req(reduce_only=True))
        except Exception as exc:
            assert "reduce_only" in str(exc)
        else:  # pragma: no cover
            raise AssertionError("expected rejection")
        assert ex.create_calls == []

    asyncio.run(run())


# ---------------------------------------------------------------------------------------------- DEX


class FakeGateway:
    def __init__(self):
        self.timeout_s = 5.0
        self.execute_response: dict | Exception = {"signature": "0x" + "ab" * 32, "status": 0}
        self.polls: list[dict] = []
        self.executed = 0

    async def execute_swap(self, *a, **k):
        self.executed += 1
        if isinstance(self.execute_response, Exception):
            raise self.execute_response
        return self.execute_response

    async def poll(self, chain, network, sig):
        return self.polls.pop(0) if self.polls else {"txStatus": 0}


def _dex(gw) -> GatewayConnector:
    c = GatewayConnector(DEXES["uniswap-base"], gw, TradingMode.TESTNET, "0xwallet", ["ETH/USDC"], D("1"))
    c.native_usd_price = lambda a: D("2000")
    return c


def _dex_req():
    return OrderRequest(
        venue="uniswap-base",
        symbol="ETH/USDC",
        side=OrderSide.SELL,
        amount=D("1"),
        limit_price=None,
        deadline_ms=1,
    )


def test_dex_poll_confirmation_uses_tx_data():
    """Audit F08 reproduction: the poll path passed txData that _apply_confirmed ignored -> price 0."""

    async def run():
        gw = FakeGateway()
        gw.polls = [
            {
                "txStatus": 1,
                "fee": "0.0001",
                "txData": {"baseTokenBalanceChange": "-1", "quoteTokenBalanceChange": "2000"},
            }
        ]
        c = _dex(gw)
        res = await asyncio.wait_for(c.place_order(_dex_req()), 30)
        assert res.status is OrderStatus.FILLED
        assert res.avg_price == D("2000") and res.quote_value == D("2000")
        assert res.gas_cost_usd == D("0.2000") and res.gas_known

    asyncio.run(run())


def test_dex_confirmed_without_amounts_has_unknown_value():
    async def run():
        gw = FakeGateway()
        gw.execute_response = {"signature": "0x" + "cd" * 32, "status": 1, "data": {"fee": "0"}}
        res = await _dex(gw).place_order(_dex_req())
        assert res.status is OrderStatus.FILLED and res.quote_value is None  # never price 0
        assert res.raw.get("amounts_unknown")

    asyncio.run(run())


def test_dex_transport_loss_is_unknown_and_confirmation_timeout_is_unknown():
    async def run():
        gw = FakeGateway()
        err = GatewayError("gateway unreachable: read timeout", status=503)
        err.transport_ambiguous = True
        gw.execute_response = err
        res = await _dex(gw).place_order(_dex_req())
        assert res.status is OrderStatus.UNKNOWN and gw.executed == 1
        gw2 = FakeGateway()  # broadcast, never confirmed within the window
        c2 = _dex(gw2)
        c2._confirmation_window_s = lambda req: 0.01
        res2 = await c2.place_order(_dex_req())
        assert res2.status is OrderStatus.UNKNOWN and res2.tx_hash and res2.outcome_unknown
        # later resolution by transaction hash
        gw2.polls = [
            {"txStatus": 1, "txData": {"baseTokenBalanceChange": "-1", "quoteTokenBalanceChange": "1990"}}
        ]
        await c2.resolve_order(res2)
        assert res2.status is OrderStatus.FILLED and res2.quote_value == D("1990")

    asyncio.run(run())


def test_dex_definitive_simulation_failure_is_failed():
    async def run():
        gw = FakeGateway()
        gw.execute_response = GatewayError("simulation failed", status=500, code="SIMULATION_FAILED")
        res = await _dex(gw).place_order(_dex_req())
        assert res.status is OrderStatus.FAILED and res.is_terminal

    asyncio.run(run())


def test_swap_order_book_is_converted_from_contracts_to_base_units():
    async def run():
        c, ex = _connector(market_type="swap", contract_size=D("0.1"))

        async def fetch_order_book(symbol, limit=None):
            return {"bids": [[99, 10]], "asks": [[100, 10], [101, 100]], "timestamp": 1}

        ex.fetch_order_book = fetch_order_book
        c.spec = None
        ob = await c.fetch_order_book("SOL/USDC:USDC")
        assert ob.asks[0].amount == D("1.0") and ob.bids[0].amount == D("1.0")  # 10 contracts = 1 SOL
        w = ob.walk(OrderSide.BUY, D("2"))
        assert w.fully_filled and w.levels_consumed == 2  # 1 SOL @100 + 1 SOL @101, not 2 contracts

    asyncio.run(run())
