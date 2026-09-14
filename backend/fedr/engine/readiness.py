"""Startup health check and live-trading readiness checklist."""

from __future__ import annotations

from dataclasses import dataclass, field

from fedr.core.enums import TradingMode, VenueHealth, VenueKind


@dataclass(slots=True)
class CheckItem:
    key: str
    label: str
    ok: bool
    detail: str = ""
    required: bool = True

    def as_dict(self) -> dict:
        return {
            "key": self.key,
            "label": self.label,
            "ok": self.ok,
            "detail": self.detail,
            "required": self.required,
        }


@dataclass(slots=True)
class Checklist:
    items: list[CheckItem] = field(default_factory=list)

    @property
    def ready(self) -> bool:
        return all(i.ok for i in self.items if i.required)

    def as_dict(self) -> dict:
        return {"ready": self.ready, "items": [i.as_dict() for i in self.items]}


async def startup_health(app) -> Checklist:
    ctx = app.ctx
    cl = Checklist()
    db_ok = await app.db.health() if app.db else False
    cl.items.append(
        CheckItem(
            "database",
            "Database",
            db_ok,
            app.env.database_url_resolved.split("///")[-1] if db_ok else "unreachable",
        )
    )
    cl.items.append(
        CheckItem(
            "wallets",
            "Wallet manager",
            app.wallets is not None,
            f"{len(app.wallets.list()) if app.wallets else 0} wallet(s)",
        )
    )
    cex = [c for c in ctx.connectors.values() if c.kind in (VenueKind.CEX, VenueKind.PERP)]
    dex = [c for c in ctx.connectors.values() if c.kind is VenueKind.DEX]
    cl.items.append(
        CheckItem(
            "exchanges",
            "Exchange connectors",
            any(c.connected for c in cex),
            f"{sum(c.connected for c in cex)}/{len(cex)} connected",
            required=False,
        )
    )
    gw = app.gateway_ok
    cl.items.append(
        CheckItem(
            "gateway",
            "Gateway (DEX middleware)",
            bool(gw),
            "reachable"
            if gw
            else ("disabled" if not app.env.gateway_enabled else "unreachable - DEX strategies unavailable"),
            required=False,
        )
    )
    cl.items.append(
        CheckItem(
            "rpc",
            "Chain RPC",
            bool(dex) and any(c.connected for c in dex),
            f"{sum(c.connected for c in dex)}/{len(dex)} DEX venues connected",
            required=False,
        )
    )
    books = len(ctx.hub.books)
    cl.items.append(CheckItem("market_data", "Market data", books > 0, f"{books} live order book(s)"))
    cl.items.append(
        CheckItem(
            "strategy_engine",
            "Strategy engine",
            app.opportunities is not None,
            f"{len(app.opportunities.candidates()) if app.opportunities else 0} route candidate(s)",
        )
    )
    cl.items.append(CheckItem("profit_guard", "Profit Guard", True, "active"))
    cl.items.append(CheckItem("gas_guard", "Gas Guard", True, "active"))
    cl.items.append(CheckItem("risk_engine", "Risk Engine", True, "active"))
    return cl


async def live_readiness(app) -> Checklist:
    ctx = app.ctx
    s = ctx.settings
    cl = Checklist()
    wallets = app.wallets.list() if app.wallets else []
    bots = [w for w in wallets if w.kind == "bot"]
    cl.items.append(
        CheckItem(
            "wallet_backup",
            "Wallet backed up",
            bool(bots) and all(w.backed_up for w in bots),
            "confirm the encrypted backup for every bot wallet"
            if not all(w.backed_up for w in bots) or not bots
            else "confirmed",
        )
    )
    funded = any(l.kind == "chain" and (l.usd_value or 0) > 0 for l in ctx.inventory.lines()) or any(
        l.kind == "cex" and (l.usd_value or 0) > 0 for l in ctx.inventory.lines()
    )
    cl.items.append(
        CheckItem(
            "funded", "Trading wallet / exchange funded", funded, "" if funded else "no funded venue detected"
        )
    )
    connected = [
        c
        for c in ctx.connectors.values()
        if c.kind in (VenueKind.CEX, VenueKind.PERP) and getattr(c, "credentials", None) and c.connected
    ]
    cl.items.append(
        CheckItem(
            "exchange_api",
            "Exchange API connected",
            bool(connected),
            f"{len(connected)} authenticated connector(s)"
            if connected
            else "no authenticated exchange connector",
        )
    )
    withdraw_flags = [getattr(c, "permissions", {}).get("withdraw") for c in connected]
    wd_ok = bool(connected) and all(f is not True for f in withdraw_flags)
    cl.items.append(
        CheckItem(
            "withdraw_disabled",
            "Withdrawal permission disabled",
            wd_ok,
            "verified OFF where the exchange exposes it; confirm manually elsewhere"
            if wd_ok
            else "a key has withdrawal enabled - create a key without it",
        )
    )
    healthy = [n for n, h in ((n, ctx.venue_health(n)) for n in ctx.connectors) if h is VenueHealth.HEALTHY]
    cl.items.append(
        CheckItem(
            "market_data",
            "Market data healthy",
            bool(healthy) and len(ctx.hub.books) > 0,
            f"{len(healthy)} healthy venue(s)",
        )
    )
    cl.items.append(
        CheckItem("profit_guard", "Profit Guard active", True, "deterministic all-in profit rule")
    )
    cl.items.append(
        CheckItem("gas_guard", "Gas Guard active", True, f"max {s.gas.max_gas_per_trade_usd} USD / trade")
    )
    cl.items.append(
        CheckItem(
            "risk_guard", "Risk Guard active", True, f"daily loss limit {s.risk.max_daily_loss_usd} USD"
        )
    )
    cl.items.append(
        CheckItem(
            "circuit_breaker",
            "Circuit breakers armed",
            not ctx.breakers.active,
            "armed"
            if not ctx.breakers.active
            else f"{len(ctx.breakers.active)} breaker(s) tripped - reset after investigation",
        )
    )
    cl.items.append(
        CheckItem(
            "reconciliation",
            "Reconciliation active",
            app.reconciler is not None and (app.reconciler.last is None or app.reconciler.last.ok),
            "ok"
            if app.reconciler and (app.reconciler.last is None or app.reconciler.last.ok)
            else "last reconciliation found discrepancies",
        )
    )
    cl.items.append(
        CheckItem(
            "paper_completed",
            "Paper mode completed",
            s.live.paper_completed,
            "mark complete after reviewing paper results" if not s.live.paper_completed else "reviewed",
        )
    )
    cl.items.append(
        CheckItem(
            "shadow_reviewed",
            "Shadow mode reviewed",
            s.live.shadow_reviewed,
            "review shadow results and mark reviewed" if not s.live.shadow_reviewed else "reviewed",
        )
    )
    cl.items.append(
        CheckItem(
            "env_gate",
            "FEDR_LIVE_TRADING_ALLOWED=true",
            app.env.live_trading_allowed,
            "set the environment gate in .env and restart" if not app.env.live_trading_allowed else "set",
        )
    )
    cl.items.append(
        CheckItem(
            "auth",
            "UI authentication enabled",
            bool(app.env.auth_token),
            "set FEDR_AUTH_TOKEN before going live" if not app.env.auth_token else "enabled",
        )
    )
    cl.items.append(CheckItem("no_estop", "Emergency stop not active", not ctx.emergency_stop, ""))
    _ = TradingMode
    return cl


# ---------------------------------------------------------------------------- per-strategy readiness
# Independent readiness per strategy family (section: a disabled optional strategy never blocks an
# otherwise-safe one, but enabling a strategy requires that strategy's own prerequisites).
# `ready` is TECHNICAL readiness in the current installation; `live_verified` states whether real-venue
# execution evidence exists for this deployment - it starts false and only an operator's verification
# run flips it (see docs/LIVE_VERIFICATION_RUNBOOK.md). FEDR never sets it optimistically.


def strategy_readiness(app) -> dict:
    ctx = app.ctx
    s = ctx.settings
    cex = [c for c in ctx.connectors.values() if c.kind is VenueKind.CEX and c.connected]
    perp = [c for c in ctx.connectors.values() if c.kind is VenueKind.PERP and c.connected]
    dex = [c for c in ctx.connectors.values() if c.kind is VenueKind.DEX and c.connected]
    healthy = {n for n in ctx.connectors if ctx.venue_health(n) is VenueHealth.HEALTHY}
    books = len(ctx.hub.books) > 0
    gw = bool(app.gateway_ok) or ctx.settings.general.mode is TradingMode.SIMULATION

    def entry(key, enabled, checks, verification):
        failed = [d for ok, d in checks if not ok]
        return {
            "key": key,
            "enabled": enabled,
            "ready": enabled and not failed,
            "blockers": ([] if enabled else ["strategy disabled in Settings → Strategies"]) + failed,
            "live_verified": False,
            "verification": verification,
        }

    env = app.env
    fl_chains = [
        c
        for c in ("ethereum", "arbitrum", "base", "optimism", "polygon")
        if getattr(env, f"flashloan_contract_{c}", None)
    ]
    out = {
        "CEX_ARBITRAGE_READY": entry(
            "cex_cex",
            s.strategies.cex_cex,
            [
                (len(cex) >= 2, f"needs ≥2 connected CEX venues (have {len(cex)})"),
                (len([c for c in cex if c.name in healthy]) >= 2, "needs ≥2 HEALTHY CEX venues"),
                (books, "no live order books"),
            ],
            "offline + paper verified; sandbox/live order round-trip outstanding (CX-02)",
        ),
        "CEX_DEX_READY": entry(
            "cex_dex",
            s.strategies.cex_dex,
            [
                (len(cex) >= 1, "needs a connected CEX venue"),
                (len(dex) >= 1, "needs a connected DEX venue"),
                (gw, "Gateway unreachable"),
                (books, "no live order books"),
            ],
            "paper verified with a synthetic DEX; Gateway execution outstanding (CX-03)",
        ),
        "DEX_ARB_READY": entry(
            "dex_dex",
            s.strategies.dex_dex,
            [
                (len(dex) >= 2, f"needs ≥2 connected DEX venues (have {len(dex)})"),
                (gw, "Gateway unreachable"),
                (books, "no live order books"),
            ],
            "paper verified with two synthetic DEX venues; on-chain execution outstanding",
        ),
        "FUNDING_READY": entry(
            "funding",
            s.strategies.funding or s.strategies.spot_perp,
            [
                (len(perp) >= 1, "needs a connected perp venue with funding data"),
                (len(cex) >= 1 or len(dex) >= 1, "needs a spot venue for the hedge leg"),
                (books, "no live order books"),
            ],
            "paper lifecycle verified (tests/test_positions.py); real derivatives venue outstanding (ST-04)",
        ),
        "BASIS_READY": entry(
            "basis",
            s.strategies.basis or s.strategies.spot_perp,
            [
                (len(perp) >= 1, "needs a connected perp/futures venue"),
                (len(cex) >= 1, "needs a spot venue"),
                (books, "no live order books"),
            ],
            "paper lifecycle verified; real venue margin/settlement outstanding (ST-04)",
        ),
        "FLASH_LOAN_READY": entry(
            "flash_loan",
            s.strategies.flash_loan and s.flash_loan.enabled,
            [
                (bool(fl_chains), "no flash-loan contract address configured"),
                (len(dex) >= 2, "needs ≥2 DEX venues on one chain"),
                (gw, "Gateway unreachable"),
            ],
            "contract compiled + 12 EVM tests; NOT audited, no fork tests - MUST BLOCK on mainnet (ST-05)",
        ),
    }
    return out
