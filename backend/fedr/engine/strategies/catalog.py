"""Strategy catalog: what each strategy is, its default state and how it builds route candidates."""
from __future__ import annotations

from dataclasses import dataclass, field

from fedr.connectors.base import VenueConnector
from fedr.core.enums import Strategy, VenueKind


@dataclass(slots=True)
class StrategyInfo:
    key: Strategy
    name: str
    description: str
    default_on: bool
    kinds: tuple[VenueKind, VenueKind]  # (buy leg kind, sell leg kind)
    same_chain_only: bool = False
    carry: bool = False  # position is held (perp/funding/basis) rather than closed instantly
    atomic: bool = False  # flash-loan / atomic on-chain
    risks: list[str] = field(default_factory=list)


CATALOG: dict[Strategy, StrategyInfo] = {
    Strategy.CEX_CEX: StrategyInfo(Strategy.CEX_CEX, "CEX ↔ CEX spot", "Buy on one exchange, sell on another using pre-funded inventory on both.", True, (VenueKind.CEX, VenueKind.CEX), risks=["one-leg fill", "exchange outage"]),
    Strategy.CEX_DEX: StrategyInfo(Strategy.CEX_DEX, "CEX ↔ DEX spot", "Exchange order book against an on-chain router quote (Jupiter, Uniswap, …).", True, (VenueKind.CEX, VenueKind.DEX), risks=["gas", "transaction failure", "MEV", "one-leg fill"]),
    Strategy.DEX_DEX: StrategyInfo(Strategy.DEX_DEX, "DEX ↔ DEX", "Two on-chain venues with pre-funded wallets (same chain preferred).", False, (VenueKind.DEX, VenueKind.DEX), risks=["gas", "two transactions", "MEV"]),
    Strategy.SPOT_PERP: StrategyInfo(Strategy.SPOT_PERP, "Spot ↔ Perpetual", "Long spot / short perpetual when the perp trades away from spot. Evaluation-only in this build (no automated position management).", False, (VenueKind.CEX, VenueKind.PERP), carry=True, risks=["funding reversal", "liquidation", "basis widening"]),
    Strategy.FUNDING: StrategyInfo(Strategy.FUNDING, "Funding-rate", "Delta-neutral spot + perp to collect funding payments. Evaluation-only in this build (no automated position management).", False, (VenueKind.CEX, VenueKind.PERP), carry=True, risks=["funding reversal", "liquidation"]),
    Strategy.BASIS: StrategyInfo(Strategy.BASIS, "Cash-and-carry / basis", "Capture the spread between spot and a derivative until convergence. Evaluation-only in this build (no automated position management).", False, (VenueKind.CEX, VenueKind.PERP), carry=True, risks=["basis widening", "margin"]),
    Strategy.FLASH_LOAN: StrategyInfo(Strategy.FLASH_LOAN, "Flash-loan atomic", "Borrow, swap on DEX A, swap on DEX B, repay - all in one reverting transaction.", False, (VenueKind.DEX, VenueKind.DEX), same_chain_only=True, atomic=True, risks=["smart contract", "gas on revert", "MEV", "simulation drift"]),
}


def strategy_enabled(settings, key: Strategy) -> bool:
    return bool(getattr(settings.strategies, key.value, False))


def candidate_pairs(strategy: StrategyInfo, venues: list[VenueConnector], pair: str) -> list[tuple[VenueConnector, VenueConnector]]:
    """All ordered (buy_venue, sell_venue) combinations for a pair under a strategy."""
    buy_kind, sell_kind = strategy.kinds
    buys = [v for v in venues if v.kind is buy_kind and v.supports_symbol(pair)]
    sells = [v for v in venues if v.kind is sell_kind and v.supports_symbol(_perp_symbol(pair) if sell_kind is VenueKind.PERP else pair)]
    out: list[tuple[VenueConnector, VenueConnector]] = []
    for b in buys:
        for s in sells:
            if b.name == s.name:
                continue
            if strategy.same_chain_only and (b.chain is None or b.chain is not s.chain):
                continue
            out.append((b, s))
            # spot arbitrage is symmetric; carry strategies are directional (buy spot, sell perp) only
            if not strategy.carry and buy_kind == sell_kind:
                continue
    if not strategy.carry and buy_kind != sell_kind:
        # add reverse direction (buy on sell-kind venue, sell on buy-kind venue)
        for s in sells:
            for b in buys:
                if b.name == s.name:
                    continue
                out.append((s, b))
    return out


def _perp_symbol(pair: str) -> str:
    base, quote = pair.split("/")
    return f"{base}/{quote}:{quote}"
