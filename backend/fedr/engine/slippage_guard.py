"""SLIPPAGE GUARD - asset / venue / route / size specific slippage limits."""

from __future__ import annotations

from decimal import Decimal

from fedr.config.schema import SlippageSettings
from fedr.core.models import ExecutionQuote
from fedr.core.money import D, fmt_pct


class SlippageGuard:
    def __init__(self, settings: SlippageSettings):
        self.settings = settings

    def limit_for(self, asset: str, venue: str, route: str, notional_usd: Decimal) -> Decimal:
        s = self.settings
        limit = s.default_max_pct
        if asset in s.per_asset_max_pct:
            limit = min(limit, D(s.per_asset_max_pct[asset]))
        if venue in s.per_venue_max_pct:
            limit = min(limit, D(s.per_venue_max_pct[venue]))
        if route in s.per_route_max_pct:
            limit = min(limit, D(s.per_route_max_pct[route]))
        # size tiers: the smallest tier whose threshold >= notional applies
        tier_limit = None
        for threshold, tier in sorted(s.size_tiers, key=lambda x: D(x[0])):
            if notional_usd <= D(threshold):
                tier_limit = D(tier)
                break
        if tier_limit is None and s.size_tiers:
            tier_limit = min(D(t[1]) for t in s.size_tiers)
        if tier_limit is not None:
            limit = min(limit, tier_limit)
        return limit

    def check(self, leg: ExecutionQuote, route: str, notional_usd: Decimal, asset: str) -> list[str]:
        limit = self.limit_for(asset, leg.venue, route, notional_usd)
        effective = leg.slippage_pct + leg.price_impact_pct
        if effective > limit:
            return [
                f"{leg.venue} {leg.side.value} slippage+impact {fmt_pct(effective)} exceeds limit {fmt_pct(limit)}"
            ]
        return []
