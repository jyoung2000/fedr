"""Backtest / replay engine.

Runs the *same* scanner, Profit Guard, Risk Engine and paper executor over a
deterministic replayed market (recorded JSONL order books, or the synthetic
generator with a seed) without any network access or wall-clock latency.
Results are labelled BACKTEST and never presented as future returns.
"""
from __future__ import annotations

import asyncio
import json
import random
from decimal import Decimal
from pathlib import Path
from typing import Any

from fedr.config.env import EnvSettings
from fedr.config.schema import AppSettings
from fedr.connectors.paper.ledger import PaperLedger
from fedr.core.enums import Chain, Decision, TradeStatus, TradingMode, VenueKind
from fedr.core.models import new_id, to_jsonable
from fedr.core.money import ZERO, D
from fedr.engine.circuit_breakers import CircuitBreakerManager
from fedr.engine.context import EngineContext
from fedr.engine.execution.executor import ExecutionEngine
from fedr.engine.execution.paper_executor import PaperExecutor
from fedr.engine.experience import ExperienceEngine
from fedr.engine.inventory import InventoryManager
from fedr.engine.opportunity import OpportunityEngine
from fedr.engine.rebalancer import Rebalancer
from fedr.marketdata.gas import GasOracle
from fedr.marketdata.hub import MarketDataHub
from fedr.marketdata.orderbook import OrderBook
from fedr.sim.synthetic import default_synthetic, dex_pairs_for
from fedr.sim.venues import SyntheticCexVenue, SyntheticDexVenue


async def _no_sleep(_: float) -> None:
    return None


async def run_backtest(settings: AppSettings, *, ticks: int = 300, seed: int = 7, pairs: list[str] | None = None, recorded: Path | None = None, label: str = "backtest") -> dict[str, Any]:
    s = settings.model_copy(deep=True)
    s.general.mode = TradingMode.SIMULATION
    s.general.shadow_mode = False
    s.general.bot_enabled = True
    pairs = pairs or s.general.pairs
    hub = MarketDataHub()
    inventory = InventoryManager(hub.prices.get)
    ctx = EngineContext(env=EnvSettings(), settings=s, repo=None, hub=hub, gas_oracle=GasOracle(hub.prices.get), breakers=CircuitBreakerManager(), inventory=inventory, experience=ExperienceEngine(None, enabled=False), rebalancer=Rebalancer(s.rebalance, inventory))
    market = default_synthetic(pairs, seed=seed)
    hub.prices.external = market.price_usd
    ctx.gas_oracle.synthetic = market
    for name, fee in (("kraken", Decimal("0.26")), ("coinbase", Decimal("0.60")), ("binance", Decimal("0.10"))):
        ctx.connectors[name] = SyntheticCexVenue(market, name, pairs, fee)
    for name, chain in (("jupiter", Chain.SOLANA), ("uniswap-base", Chain.BASE), ("uniswap-arbitrum", Chain.ARBITRUM)):
        ctx.connectors[name] = SyntheticDexVenue(market, name, dex_pairs_for(chain, pairs), chain)
    for c in ctx.connectors.values():
        await c.connect()
    ledger = PaperLedger()
    ledger.seed(s.paper.starting_balances)
    ctx.ledger = ledger
    ctx.paper_executor = PaperExecutor(ledger, lambda: s.paper, ctx.gas_oracle.snapshot, ctx.gas_guard, rng=random.Random(seed), sleep=_no_sleep)
    opps = OpportunityEngine(ctx)
    execu = ExecutionEngine(ctx, opps)
    recorded_books = _load_recorded(recorded) if recorded else None
    start_value = _portfolio_value(ctx)
    stats = {"ticks": 0, "evaluated": 0, "executable": 0, "blocked": 0, "trades": 0, "filled": 0, "failed": 0, "hedged": 0, "aborted": 0, "gross": ZERO, "fees": ZERO, "gas": ZERO, "net": ZERO, "estimated_net": ZERO, "block_reasons": {}, "wins": 0}
    for t in range(ticks):
        if recorded_books is not None:
            if t >= len(recorded_books):
                break
            for book in recorded_books[t]:
                await hub.ingest(book)
        else:
            market.tick()
            for c in ctx.connectors.values():
                if c.kind is VenueKind.CEX:
                    for sym in c.markets:
                        await hub.ingest(await c.fetch_order_book(sym))
        for chain in (Chain.SOLANA, Chain.BASE, Chain.ARBITRUM):
            await ctx.gas_oracle.refresh(chain)
        for venue in ledger.venues():
            inventory.update_balances(venue, ledger.balances(venue), kind="chain" if venue in {c.value for c in Chain} else "cex")
        ranked = await opps.scan()
        stats["ticks"] += 1
        stats["evaluated"] += len(ranked)
        for o in ranked:
            if o.decision is Decision.SAFE_TO_EXECUTE:
                stats["executable"] += 1
            else:
                stats["blocked"] += 1
                for r in o.block_reasons[:1]:
                    key = r.split("(")[0].strip()[:60]
                    stats["block_reasons"][key] = stats["block_reasons"].get(key, 0) + 1
        for o in ranked:
            if o.decision is not Decision.SAFE_TO_EXECUTE or o.strategy.value == "flash_loan":
                continue
            tr = await execu.execute(o, trigger="backtest")
            stats["trades"] += 1
            stats[{TradeStatus.FILLED: "filled", TradeStatus.FAILED: "failed", TradeStatus.HEDGED: "hedged", TradeStatus.ABORTED: "aborted"}.get(tr.status, "failed")] += 1
            if tr.actual_net is not None:
                stats["net"] += tr.actual_net
                stats["gross"] += tr.actual_gross or ZERO
                stats["fees"] += tr.actual_fees or ZERO
                stats["gas"] += tr.actual_gas or ZERO
                stats["estimated_net"] += tr.estimated_net
                stats["wins"] += 1 if tr.actual_net > 0 else 0
            break  # one trade per tick keeps the replay deterministic
    end_value = _portfolio_value(ctx)
    return {
        "id": new_id("bt"),
        "label": label,
        "kind": "BACKTEST",
        "disclaimer": "Backtest on replayed/synthetic data with simulated execution. Not a prediction of future returns.",
        "params": {"ticks": ticks, "seed": seed, "pairs": pairs, "recorded": str(recorded) if recorded else None, "risk_profile": s.general.risk_profile.value, "min_profit_usd": str(s.trading.min_profit_usd), "stress_multiplier": str(s.paper.stress_multiplier)},
        "results": {**{k: (str(v) if isinstance(v, Decimal) else v) for k, v in stats.items()}, "start_value_usd": str(start_value), "end_value_usd": str(end_value), "prediction_error_total": str(stats["estimated_net"] - stats["net"])},
    }


def _portfolio_value(ctx: EngineContext) -> Decimal:
    return ctx.ledger.total_usd(ctx.hub.prices.get) if ctx.ledger else ZERO


def _load_recorded(path: Path) -> list[list[OrderBook]]:
    """JSONL: one line per tick: {"books": [{"venue","symbol","bids","asks","ts_ms"}...]}"""
    ticks: list[list[OrderBook]] = []
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            ticks.append([OrderBook.from_raw(b["venue"], b["symbol"], b["bids"], b["asks"], ts_ms=b.get("ts_ms"), source="replay") for b in row.get("books", [])])
    return ticks


def record_books_line(books: list[OrderBook]) -> str:
    return json.dumps({"books": [{"venue": b.venue, "symbol": b.symbol, "ts_ms": b.ts_ms, "bids": [[str(l.price), str(l.amount)] for l in b.bids[:25]], "asks": [[str(l.price), str(l.amount)] for l in b.asks[:25]]} for b in books]})


__all__ = ["run_backtest", "record_books_line", "asyncio", "to_jsonable", "D"]
