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

    @property
    def net_benefit_usd(self) -> Decimal:
        return self.estimated_benefit_usd - self.estimated_cost_usd


# Documented conservative fallbacks (USD) for transfer costs when no live quote is available.
FALLBACK_TRANSFER_COST_USD = {"withdrawal": Decimal("3.00"), "network": Decimal("1.50"), "bridge": Decimal("8.00"), "swap": Decimal("2.00")}


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

    def transfer_cost_usd(self, from_venue: str, to_venue: str, asset: str, amount_usd: Decimal, withdrawal_fee_usd: Decimal | None = None) -> Decimal:
        cost = withdrawal_fee_usd if withdrawal_fee_usd is not None else FALLBACK_TRANSFER_COST_USD["withdrawal"]
        cost += FALLBACK_TRANSFER_COST_USD["network"]
        from_chain = self.inventory._lines.get((from_venue, asset))
        to_chain = self.inventory._lines.get((to_venue, asset))
        if from_chain and to_chain and from_chain.kind == "chain" and to_chain.kind == "chain":
            cost += FALLBACK_TRANSFER_COST_USD["bridge"]
        return cost

    def recommend(self) -> list[RebalanceRecommendation]:
        if not self.settings.recommendations_enabled:
            return []
        out: list[RebalanceRecommendation] = []
        for (venue, asset), values in self.blocked_value.items():
            if not values:
                continue
            benefit = sum(values, ZERO) * Decimal("0.5")  # assume half the blocked value would have been captured
            # find a donor venue with the most of this asset
            donors = [l for l in self.inventory.lines() if l.asset == asset and l.venue != venue and l.available > 0]
            if not donors:
                continue
            donor = max(donors, key=lambda l: l.available)
            px = self.inventory.price_usd(asset) or Decimal("1")
            target_line = self.inventory._lines.get((venue, asset))
            shortfall_usd = max(Decimal("50"), (target_line.target - target_line.total) * px if target_line and target_line.target > 0 else donor.available * px * Decimal("0.3"))
            amount = min(donor.available * Decimal("0.5"), shortfall_usd / px)
            if amount <= 0:
                continue
            cost = self.transfer_cost_usd(donor.venue, venue, asset, amount * px)
            rec = RebalanceRecommendation(donor.venue, venue, asset, amount, cost, benefit, f"{self.blocked_count[(venue, asset)]} opportunities blocked by missing {asset} on {venue} in the lookback window")
            if rec.net_benefit_usd >= self.settings.min_net_benefit_usd:
                out.append(rec)
        return sorted(out, key=lambda r: -r.net_benefit_usd)

    def clear(self) -> None:
        self.blocked_value.clear()
        self.blocked_count.clear()
