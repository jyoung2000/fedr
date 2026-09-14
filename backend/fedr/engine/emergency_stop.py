"""Emergency stop: stop new trades, cancel cancellable orders, block on-chain execution, reconcile."""

from __future__ import annotations

from dataclasses import dataclass, field

from fedr.connectors.base import ConnectorError
from fedr.core.enums import CircuitBreakerReason, VenueKind
from fedr.core.logging import get_logger
from fedr.core.models import now_ms

log = get_logger("estop")


@dataclass(slots=True)
class StopReport:
    activated_at_ms: int = field(default_factory=now_ms)
    cancelled_orders: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    reconciliation: dict | None = None

    def as_dict(self) -> dict:
        return {
            "activated_at_ms": self.activated_at_ms,
            "cancelled_orders": self.cancelled_orders,
            "errors": self.errors,
            "reconciliation": self.reconciliation,
        }


class EmergencyStop:
    def __init__(self, ctx, reconciler, repo_key: str = "system"):
        self.ctx = ctx
        self.reconciler = reconciler
        self.report: StopReport | None = None
        self.reason: str | None = None

    async def activate(self, reason: str, actor: str = "user") -> StopReport:
        ctx = self.ctx
        ctx.emergency_stop = True
        self.reason = reason
        rep = StopReport()
        await ctx.breakers.trip(CircuitBreakerReason.MANUAL, f"emergency stop: {reason}", auto=False)
        for c in ctx.connectors.values():
            if (
                c.kind not in (VenueKind.CEX, VenueKind.PERP)
                or not c.connected
                or not getattr(c, "credentials", None)
            ):
                continue
            try:
                for o in await c.fetch_open_orders():
                    try:
                        await c.cancel_order(str(o.get("id")), str(o.get("symbol")))
                        rep.cancelled_orders.append(f"{c.name}:{o.get('id')}")
                    except ConnectorError as exc:
                        rep.errors.append(f"{c.name}:{o.get('id')}: {exc}")
            except ConnectorError as exc:
                rep.errors.append(f"{c.name}: could not list open orders ({exc})")
        try:
            r = await self.reconciler.run_once()
            rep.reconciliation = r.as_dict()
        except Exception as exc:  # pragma: no cover
            rep.errors.append(f"reconciliation failed: {exc}")
        self.report = rep
        if ctx.repo:
            await ctx.repo.audit(
                ctx.mode, "emergency_stop", f"EMERGENCY STOP activated: {reason}", rep.as_dict(), actor=actor
            )
            await ctx.repo.save_settings_doc(
                {"active": True, "reason": reason, "at_ms": rep.activated_at_ms}, key="emergency_stop"
            )
        return rep

    async def release(self, actor: str = "user") -> None:
        ctx = self.ctx
        ctx.emergency_stop = False
        self.reason = None
        await ctx.breakers.reset(CircuitBreakerReason.MANUAL)
        if ctx.repo:
            await ctx.repo.audit(ctx.mode, "emergency_stop", "Emergency stop released", {}, actor=actor)
            await ctx.repo.save_settings_doc({"active": False}, key="emergency_stop")

    async def restore(self) -> None:
        if self.ctx.repo:
            doc = await self.ctx.repo.get_settings_doc("emergency_stop")
            if doc and doc.get("active"):
                self.ctx.emergency_stop = True
                self.reason = doc.get("reason")
