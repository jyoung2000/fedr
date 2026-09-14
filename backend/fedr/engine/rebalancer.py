"""Rebalancer: recommends (never auto-executes) inventory transfers when the
economics justify them. Balances differing is NOT a reason to rebalance."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from decimal import Decimal

from fedr.config.schema import RebalanceSettings
from fedr.core.money import ZERO
from fedr.engine.inventory import InventoryManager


@dataclass(slots=True)
class RebalanceRecommendation:
    from_venue: str
    to_venue: str
    asset: str
    amount: Decimal
    estimated_cost_usd: Decimal
    estimated_benefit_usd: Decimal
    reason: str
    method: str = "transfer"  # transfer (withdraw+deposit) | bridge | swap
    alternatives: list[dict] = None  # type: ignore[assignment]

    @property
    def net_benefit_usd(self) -> Decimal:
        return self.estimated_benefit_usd - self.estimated_cost_usd


# Documented conservative fallbacks (USD) for transfer costs when no live quote is available.
FALLBACK_TRANSFER_COST_USD = {
    "withdrawal": Decimal("3.00"),
    "network": Decimal("1.50"),
    "bridge": Decimal("8.00"),
    "swap": Decimal("2.00"),
}


class Rebalancer:
    def __init__(self, settings: RebalanceSettings, inventory: InventoryManager):
        self.settings = settings
        self.inventory = inventory
        # (venue, asset) -> [expected_net_usd of opportunities blocked by that shortfall]
        self.blocked_value: dict[tuple[str, str], list[Decimal]] = {}
        self.blocked_count: Counter = Counter()

    def note_blocked_by_inventory(self, venue: str, asset: str, expected_net_usd: Decimal) -> None:
        self.blocked_value.setdefault((venue, asset), []).append(max(ZERO, expected_net_usd))
        self.blocked_count[(venue, asset)] += 1
        if len(self.blocked_value[(venue, asset)]) > 500:
            self.blocked_value[(venue, asset)] = self.blocked_value[(venue, asset)][-500:]

    def method_costs(
        self,
        from_venue: str,
        to_venue: str,
        asset: str,
        amount_usd: Decimal,
        withdrawal_fee_usd: Decimal | None = None,
    ) -> list[dict]:
        """Compare the ways of moving value from one venue to another.

        Every method is a *recommendation*: FEDR never moves trading capital by itself. A method that FEDR
        cannot price or execute in this build is reported as ``available: False`` with the reason, never
        silently dropped, so the operator sees the full comparison.
        """
        from_line = self.inventory._lines.get((from_venue, asset))
        to_line = self.inventory._lines.get((to_venue, asset))
        from_chain = bool(from_line and from_line.kind == "chain")
        to_chain = bool(to_line and to_line.kind == "chain")
        wfee = (
            withdrawal_fee_usd if withdrawal_fee_usd is not None else FALLBACK_TRANSFER_COST_USD["withdrawal"]
        )
        out: list[dict] = []
        # 1. plain transfer: withdraw on one venue, deposit on the other (same asset, same network)
        transfer_cost = wfee + FALLBACK_TRANSFER_COST_USD["network"]
        out.append(
            {
                "method": "transfer",
                "available": not (from_chain and to_chain),
                "cost_usd": transfer_cost,
                "eta_minutes": 30,
                "manual": True,
                "reason": "withdraw on the source venue and deposit on the destination; same asset, same network"
                if not (from_chain and to_chain)
                else "both sides are on-chain wallets on different chains - a transfer needs a bridge",
            }
        )
        # 2. bridge: cross-chain move; never automated by FEDR (bridge risk is not modelled)
        out.append(
            {
                "method": "bridge",
                "available": False,
                "cost_usd": transfer_cost + FALLBACK_TRANSFER_COST_USD["bridge"],
                "eta_minutes": 20,
                "manual": True,
                "reason": "cross-chain bridging is never automated (bridge/contract risk); estimate only",
            }
        )
        # 3. swap: change the asset mix on the destination instead of moving funds
        swap_cost = amount_usd * Decimal("0.001") + FALLBACK_TRANSFER_COST_USD["swap"]
        out.append(
            {
                "method": "swap",
                "available": bool(to_line),
                "cost_usd": swap_cost,
                "eta_minutes": 1,
                "manual": True,
                "reason": f"sell another asset for {asset} on {to_venue} (0.10% + fees); no transfer needed"
                if to_line
                else f"{to_venue} has no inventory to swap from",
            }
        )
        return out

    def transfer_cost_usd(
        self,
        from_venue: str,
        to_venue: str,
        asset: str,
        amount_usd: Decimal,
        withdrawal_fee_usd: Decimal | None = None,
    ) -> Decimal:
        """Cheapest *available* method's cost (transfer / swap); bridging is never counted as available."""
        opts = [
            m
            for m in self.method_costs(from_venue, to_venue, asset, amount_usd, withdrawal_fee_usd)
            if m["available"]
        ]
        if not opts:
            return (
                FALLBACK_TRANSFER_COST_USD["withdrawal"]
                + FALLBACK_TRANSFER_COST_USD["network"]
                + FALLBACK_TRANSFER_COST_USD["bridge"]
            )
        return min(m["cost_usd"] for m in opts)

    def recommend(self) -> list[RebalanceRecommendation]:
        if not self.settings.recommendations_enabled:
            return []
        out: list[RebalanceRecommendation] = []
        for (venue, asset), values in self.blocked_value.items():
            if not values:
                continue
            benefit = sum(values, ZERO) * Decimal(
                "0.5"
            )  # assume half the blocked value would have been captured
            # find a donor venue with the most of this asset
            donors = [
                l for l in self.inventory.lines() if l.asset == asset and l.venue != venue and l.available > 0
            ]
            if not donors:
                continue
            donor = max(donors, key=lambda l: l.available)
            px = self.inventory.price_usd(asset) or Decimal("1")
            target_line = self.inventory._lines.get((venue, asset))
            shortfall_usd = max(
                Decimal("50"),
                (target_line.target - target_line.total) * px
                if target_line and target_line.target > 0
                else donor.available * px * Decimal("0.3"),
            )
            amount = min(donor.available * Decimal("0.5"), shortfall_usd / px)
            if amount <= 0:
                continue
            methods = self.method_costs(donor.venue, venue, asset, amount * px)
            available = [m for m in methods if m["available"]]
            if not available:
                continue
            best = min(available, key=lambda m: m["cost_usd"])
            rec = RebalanceRecommendation(
                donor.venue,
                venue,
                asset,
                amount,
                best["cost_usd"],
                benefit,
                f"{self.blocked_count[(venue, asset)]} opportunities blocked by missing {asset} on {venue} in the lookback window",
                method=best["method"],
                alternatives=[{**m, "cost_usd": str(m["cost_usd"])} for m in methods],
            )
            if rec.net_benefit_usd >= self.settings.min_net_benefit_usd:
                out.append(rec)
        return sorted(out, key=lambda r: -r.net_benefit_usd)

    def clear(self) -> None:
        self.blocked_value.clear()
        self.blocked_count.clear()
