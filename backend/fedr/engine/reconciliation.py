"""Reconciliation: expected (durable ledger) vs observed balances, orders and transactions.

Real venues (TESTNET / LIVE), audit F05 / invariant D:

* EXPECTED balances come from the durable ``ledger_entries`` table: a documented opening checkpoint per
  account (the first observation of that account, recorded explicitly), confirmed fills, fees, gas,
  deposits / withdrawals and explicit operator adjustments. A fresh venue snapshot NEVER overwrites the
  expectation.
* The comparison covers the UNION of expected and observed assets: an expected asset that is missing from
  the venue's report is compared against zero (a disappearance is a discrepancy, not "nothing to check").
* An observation that cannot be made (balance or open-order query failed) is reported as INCOMPLETE for
  that account, never as OK, and the venue is paused until it can be observed again.
* Accounts with work in flight (reserved capital, unknown orders, unreconciled fills, pending withdrawals)
  are DEFERRED rather than compared mid-movement.
* Corrections are explicit ``adjustment`` rows with actor and note - history is never rewritten.

PAPER / SIMULATION: the paper ledger is the book of record; it is checked for internal consistency
(no negative balances, no reservation without an owning trade) and persisted.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from fedr.connectors.base import ConnectorError
from fedr.core.enums import CircuitBreakerReason, OrderSide, TradeStatus, VenueKind
from fedr.core.logging import get_logger
from fedr.core.models import TradeRecord, now_ms
from fedr.core.money import ZERO, D
from fedr.engine.context import EngineContext

log = get_logger("reconcile")

BALANCE_TOLERANCE_PCT = Decimal("0.5")
BALANCE_TOLERANCE_ABS_USD = Decimal("1.00")
# without a USD price an absolute tolerance in units is meaningless: compare with this relative tolerance
UNPRICED_TOLERANCE_PCT = Decimal("0.1")


@dataclass(slots=True)
class ReconciliationReport:
    ok: bool
    checked_venues: list[str] = field(default_factory=list)
    discrepancies: list[str] = field(default_factory=list)
    unresolved_orders: list[str] = field(default_factory=list)
    incomplete: list[str] = field(default_factory=list)  # observations that could not be made
    deferred: list[str] = field(default_factory=list)  # accounts with work in flight (not compared)
    checkpoints: list[str] = field(default_factory=list)  # opening checkpoints recorded in this run
    deposits: list[dict] = field(default_factory=list)  # increases on idle accounts booked as transfers
    ts_ms: int = field(default_factory=now_ms)

    @property
    def status(self) -> str:
        if self.discrepancies:
            return "discrepancy"
        if self.incomplete:
            return "incomplete"
        return "ok"

    def as_dict(self) -> dict:
        return {
            "ok": self.ok,
            "status": self.status,
            "checked_venues": self.checked_venues,
            "discrepancies": self.discrepancies,
            "unresolved_orders": self.unresolved_orders,
            "incomplete": self.incomplete,
            "deferred": self.deferred,
            "checkpoints": self.checkpoints,
            "deposits": self.deposits,
            "ts_ms": self.ts_ms,
        }


class Reconciler:
    def __init__(self, ctx: EngineContext, executor=None):
        self.ctx = ctx
        self.executor = executor
        self.last: ReconciliationReport | None = None
        self.unreconciled_accounts: dict[str, set[str]] = {}  # trade id -> accounts awaiting values
        self.pending_withdrawal_accounts: set[str] = set()
        self._incomplete_venues: set[str] = set()
        # called with every deposit booked from an unexplained increase on an idle account
        self.on_deposit = None

    # ------------------------------------------------------------------ ledger recording
    def _account_for(self, venue_name: str) -> str | None:
        c = self.ctx.connectors.get(venue_name)
        if c is None:
            return None
        if c.kind is VenueKind.PERP:
            return None  # derivative accounts are reconciled by position, not by spot balance
        return self.ctx.ledger_venue_for(c)

    def ledger_entries_for_trade(self, tr: TradeRecord) -> tuple[list[dict], set[str]]:
        """Ledger rows for every confirmed leg of a settled trade, and the accounts that cannot be booked
        because a value is missing (those stay deferred until the trade is resolved)."""
        entries: list[dict] = []
        incomplete: set[str] = set()
        for role, leg in tr.legs():
            if leg.filled == 0:
                continue
            account = self._account_for(leg.request.venue)
            if account is None:
                continue
            base, quote = leg.request.symbol.split(":")[0].split("/")
            qv = leg.quote_value
            if qv is None or leg.outcome_unknown or not leg.fee_known:
                incomplete.add(account)
                continue
            key = f"{tr.id}:{leg.request.client_order_id}"
            sign = 1 if leg.request.side is OrderSide.BUY else -1
            entries.append(
                {
                    "account": account,
                    "asset": base,
                    "delta": sign * leg.filled,
                    "kind": "fill",
                    "ref": key,
                    "dedupe_key": f"{key}:base",
                }
            )
            entries.append(
                {
                    "account": account,
                    "asset": quote,
                    "delta": -sign * qv,
                    "kind": "fill",
                    "ref": key,
                    "dedupe_key": f"{key}:quote",
                }
            )
            fees: list[tuple[Decimal, str]] = (
                [(f.fee_amount, f.fee_asset) for f in leg.fills]
                if leg.fills_cover_filled and not leg.fee_items
                else list(leg.fee_items)
            )
            for i, (amt, asset) in enumerate(fees):
                if amt:
                    entries.append(
                        {
                            "account": account,
                            "asset": asset or quote,
                            "delta": -amt,
                            "kind": "fee",
                            "ref": key,
                            "dedupe_key": f"{key}:fee:{i}",
                        }
                    )
            c = self.ctx.connectors.get(leg.request.venue)
            gas_native = D(leg.raw.get("gas_native") or 0)
            if c is not None and c.kind is VenueKind.DEX and c.chain is not None and gas_native:
                entries.append(
                    {
                        "account": account,
                        "asset": c.chain.native_token,
                        "delta": -gas_native,
                        "kind": "gas",
                        "ref": key,
                        "dedupe_key": f"{key}:gas",
                    }
                )
            del role
        return entries, incomplete

    async def record_trade(self, tr: TradeRecord) -> None:
        """Executor hook: append the authoritative ledger rows of a settled real-venue trade."""
        ctx = self.ctx
        if ctx.is_simulated_execution or ctx.repo is None:
            return
        entries, incomplete = self.ledger_entries_for_trade(tr)
        if tr.status is TradeStatus.UNRECONCILED or incomplete:
            self.unreconciled_accounts[tr.id] = incomplete or {
                a for _r, leg in tr.legs() if (a := self._account_for(leg.request.venue))
            }
        else:
            self.unreconciled_accounts.pop(tr.id, None)
        if entries:
            await ctx.repo.add_ledger_entries(ctx.mode, entries)

    async def load(self) -> None:
        """Rebuild the deferred-account set from persisted unreconciled trades (restart)."""
        from fedr.core.models import trade_from_dict

        if self.ctx.repo is None:
            return
        for row in await self.ctx.repo.trades_with_status(self.ctx.mode, ["unreconciled"]):
            try:
                tr = trade_from_dict(row.payload)
            except Exception:
                continue
            accts = {a for _r, leg in tr.legs() if (a := self._account_for(leg.request.venue))}
            self.unreconciled_accounts[tr.id] = accts or {"*"}

    async def record_transfer(
        self, account: str, asset: str, delta: Decimal, ref: str, note: str, dedupe_key: str | None = None
    ) -> None:
        if self.ctx.repo is None or self.ctx.is_simulated_execution:
            return
        await self.ctx.repo.add_ledger_entries(
            self.ctx.mode,
            [
                {
                    "account": account,
                    "asset": asset,
                    "delta": delta,
                    "kind": "transfer",
                    "ref": ref,
                    "note": note,
                    "dedupe_key": dedupe_key or f"transfer:{ref}:{asset}",
                }
            ],
        )

    async def adjust(
        self, account: str, asset: str, *, observed: Decimal, note: str, actor: str = "user"
    ) -> Decimal:
        """Operator reconciliation: record an explicit adjustment that brings the expectation to an observed,
        investigated balance. Returns the delta. Never rewrites history."""
        ctx = self.ctx
        if ctx.repo is None or ctx.is_simulated_execution:
            raise ValueError("adjustments apply to the real-venue ledger (testnet/live) only")
        if not note.strip():
            raise ValueError("an adjustment needs a note explaining the investigation")
        expected = (await ctx.repo.ledger_balances(ctx.mode)).get((account, asset), ZERO)
        delta = observed - expected
        await ctx.repo.add_ledger_entries(
            ctx.mode,
            [
                {
                    "account": account,
                    "asset": asset,
                    "delta": delta,
                    "kind": "adjustment",
                    "ref": f"adjust:{now_ms()}",
                    "actor": actor,
                    "note": note[:500],
                }
            ],
        )
        await ctx.repo.audit(
            ctx.mode,
            "reconciliation",
            f"Reconciliation adjustment {account} {asset}: {expected} -> {observed} (delta {delta})",
            {
                "account": account,
                "asset": asset,
                "expected": str(expected),
                "observed": str(observed),
                "note": note[:500],
            },
            actor=actor,
        )
        return delta

    # ------------------------------------------------------------------ run
    async def run_once(self) -> ReconciliationReport:
        ctx = self.ctx
        rep = ReconciliationReport(ok=True)
        if ctx.is_simulated_execution:
            await self._reconcile_paper(rep)
        else:
            await self._reconcile_real(rep)
        rep.ok = not rep.discrepancies and not rep.incomplete
        self.last = rep
        if rep.discrepancies:
            await ctx.breakers.trip(
                CircuitBreakerReason.BALANCE_DISCREPANCY, "; ".join(rep.discrepancies)[:250]
            )
            if ctx.repo:
                await ctx.repo.add_risk_event(
                    ctx.mode, "critical", "reconciliation", "; ".join(rep.discrepancies), rep.as_dict()
                )
                await ctx.repo.audit(
                    ctx.mode,
                    "reconciliation",
                    "Reconciliation found discrepancies - trading paused",
                    rep.as_dict(),
                )
        return rep

    async def _reconcile_paper(self, rep: ReconciliationReport) -> None:
        ctx = self.ctx
        if ctx.ledger is None:
            return
        for venue, assets in ctx.ledger.dump().items():
            rep.checked_venues.append(venue)
            for asset, (free, used) in assets.items():
                if free < 0:
                    rep.discrepancies.append(f"paper ledger negative balance: {asset} on {venue} = {free}")
                if used > 0 and not ctx.open_trade_ids:
                    # a reservation survived its trade -> release it (self-healing, but logged)
                    await ctx.ledger.release(venue, asset, used)
                    log.warning(
                        "released stale paper reservation", venue=venue, asset=asset, amount=str(used)
                    )
        if ctx.repo:
            await ctx.repo.save_paper_balances(ctx.mode, ctx.ledger.dump())

    def _busy_accounts(self) -> dict[str, str]:
        ctx = self.ctx
        busy: dict[str, str] = {}
        for tid in ctx.open_trade_ids:
            for v, _a, _x in ctx.inventory.reservation(tid):
                busy[v] = f"trade {tid} in flight"
        if self.executor is not None:
            for tr in self.executor.unresolved.values():
                for _r, leg in tr.legs():
                    c = ctx.connectors.get(leg.request.venue)
                    if c is not None:
                        busy[ctx.ledger_venue_for(c)] = f"trade {tr.id} has an unknown order outcome"
        for tid, accts in self.unreconciled_accounts.items():
            for a in accts:
                busy[a] = f"trade {tid} awaits authoritative fill values"
        for a in self.pending_withdrawal_accounts:
            busy[a] = "withdrawal awaiting confirmation"
        return busy

    async def _reconcile_real(self, rep: ReconciliationReport) -> None:
        ctx = self.ctx
        expected = await ctx.repo.ledger_balances(ctx.mode) if ctx.repo else {}
        accounts_with_history = {a for a, _ in expected}
        opened = await ctx.repo.ledger_opening_accounts(ctx.mode) if ctx.repo else set()
        busy = self._busy_accounts()
        if "*" in busy:
            rep.deferred.append("all accounts: a trade with unknown venues awaits settlement values")
        known_ids = await ctx.repo.known_client_order_ids(ctx.mode) if ctx.repo else set()
        seen_accounts: set[str] = set()
        for c in list(ctx.connectors.values()):
            if not c.connected:
                continue
            account = ctx.ledger_venue_for(c)
            if c.kind is VenueKind.PERP or account in seen_accounts:
                continue
            if not getattr(c, "credentials", None) and not getattr(c, "wallet_address", None):
                continue  # public-data-only venue: there is no account to reconcile
            seen_accounts.add(account)
            try:
                observed_bal = await c.fetch_balances()
            except ConnectorError as exc:
                rep.incomplete.append(f"{c.name}: balances could not be observed ({str(exc)[:120]})")
                await self._mark_incomplete(c.name, "balances could not be observed")
                continue
            rep.checked_venues.append(c.name)
            observed = {a: b.total for a, b in observed_bal.items()}
            if account in busy or "*" in busy:
                rep.deferred.append(f"{account}: {busy.get(account) or busy.get('*')}")
            elif account not in accounts_with_history:
                await self._checkpoint(account, observed, rep)
            elif account not in opened:
                # activity was booked before any baseline existed: never invent one silently
                rep.incomplete.append(
                    f"{account}: ledger activity without an opening checkpoint - reconcile with an explicit "
                    "adjustment after checking the venue history"
                )
                await self._mark_incomplete(c.name, "no opening checkpoint")
            else:
                for dep in self._compare(c.name, account, expected, observed, rep):
                    await self._book_deposit(dep, rep)
            if c.kind is VenueKind.CEX:
                try:
                    open_orders = await c.fetch_open_orders()
                except ConnectorError as exc:
                    rep.incomplete.append(f"{c.name}: open orders could not be observed ({str(exc)[:120]})")
                    await self._mark_incomplete(c.name, "open orders could not be observed")
                    continue
                for o in open_orders:
                    oid, cid = str(o.get("id") or ""), str(o.get("clientOrderId") or "")
                    rep.unresolved_orders.append(f"{c.name}:{oid}")
                    if oid not in known_ids and cid not in known_ids:
                        rep.discrepancies.append(
                            f"{c.name}: untracked open order {oid or cid} ({o.get('symbol')}) - not in the journal"
                        )
            if c.name in self._incomplete_venues and not any(
                i.startswith(f"{c.name}:") for i in rep.incomplete
            ):
                self._incomplete_venues.discard(c.name)
                await ctx.breakers.reset(CircuitBreakerReason.BALANCE_DISCREPANCY, scope=f"venue:{c.name}")
        if self.executor is not None:
            for tr in self.executor.unresolved.values():
                for _r, leg in tr.unresolved_legs:
                    rep.unresolved_orders.append(
                        f"{leg.request.venue}:{leg.tx_hash or leg.order_id or leg.request.client_order_id}"
                    )

    async def _mark_incomplete(self, venue: str, why: str) -> None:
        self._incomplete_venues.add(venue)
        await self.ctx.breakers.trip(
            CircuitBreakerReason.BALANCE_DISCREPANCY,
            f"reconciliation incomplete on {venue}: {why}",
            scope=f"venue:{venue}",
        )

    async def ensure_checkpoint(self, account: str, observed: dict[str, Decimal]) -> bool:
        """Called with the first balance observation of an account after (re)connecting: records the
        opening checkpoint before any trade can touch the account. No-op when one exists or work is busy."""
        ctx = self.ctx
        if ctx.repo is None or ctx.is_simulated_execution:
            return False
        if account in await ctx.repo.ledger_opening_accounts(ctx.mode):
            return False
        if account in self._busy_accounts():
            return False
        expected = await ctx.repo.ledger_balances(ctx.mode)
        if any(a == account for a, _ in expected):
            return False  # activity without a baseline: left for an explicit operator adjustment
        rep = ReconciliationReport(ok=True)
        await self._checkpoint(account, observed, rep)
        return True

    async def _checkpoint(
        self, account: str, observed: dict[str, Decimal], rep: ReconciliationReport
    ) -> None:
        """Documented opening checkpoint: the first observation of an account becomes its baseline."""
        ctx = self.ctx
        if ctx.repo is None:
            return
        ts = now_ms()
        rows = [
            {
                "account": account,
                "asset": a,
                "delta": x,
                "kind": "opening",
                "ref": f"opening:{ts}",
                "dedupe_key": f"opening:{ctx.mode.value}:{account}:{a}",
                "note": f"opening checkpoint from the first venue observation at {ts}",
            }
            for a, x in observed.items()
        ] or [
            {
                "account": account,
                "asset": "_none",
                "delta": ZERO,
                "kind": "opening",
                "ref": f"opening:{ts}",
                "dedupe_key": f"opening:{ctx.mode.value}:{account}:_none",
                "note": "opening checkpoint: account observed empty",
            }
        ]
        await ctx.repo.add_ledger_entries(ctx.mode, rows)
        rep.checkpoints.append(account)
        await ctx.repo.audit(
            ctx.mode,
            "reconciliation",
            f"Opening checkpoint recorded for {account} ({len(observed)} assets)",
            {"account": account, "balances": {a: str(x) for a, x in observed.items()}},
        )

    async def _book_deposit(self, dep: dict, rep: ReconciliationReport) -> None:
        """An increase above the ledger on an IDLE account (no trade, withdrawal or unknown order touching
        it) is booked as an incoming transfer: explicit, audited, visible. Decreases are never auto-explained."""
        ts = now_ms()
        await self.record_transfer(
            dep["account"],
            dep["asset"],
            dep["amount"],
            ref=f"deposit:{ts}",
            note="deposit detected: balance above the ledger on an idle account (source not verified)",
            dedupe_key=f"deposit:{dep['account']}:{dep['asset']}:{dep['observed']}:{ts}",
        )
        rep.deposits.append({k: str(v) for k, v in dep.items()})
        if self.ctx.repo:
            await self.ctx.repo.audit(
                self.ctx.mode,
                "wallet",
                f"Deposit detected: {dep['amount']} {dep['asset']} on {dep['account']}",
                {k: str(v) for k, v in dep.items()},
            )
        if self.on_deposit is not None:
            await self.on_deposit(dep)

    def _compare(
        self,
        venue: str,
        account: str,
        expected: dict[tuple[str, str], Decimal],
        observed: dict[str, Decimal],
        rep: ReconciliationReport,
    ) -> list[dict]:
        """Compare the union of expected and observed assets. Returns increases (deposit candidates)."""
        increases: list[dict] = []
        exp_assets = {a: x for (acct, a), x in expected.items() if acct == account and a != "_none"}
        for asset in sorted(set(exp_assets) | set(observed)):
            exp = exp_assets.get(asset, ZERO)
            obs = observed.get(asset)
            if obs is None:
                obs = ZERO
                missing = exp != 0
            else:
                missing = False
            diff = abs(obs - exp)
            if diff == 0:
                continue
            px = self.ctx.price_usd(asset)
            if px is not None:
                bad = diff * px > BALANCE_TOLERANCE_ABS_USD and (
                    exp == 0 or diff / abs(exp) * 100 > BALANCE_TOLERANCE_PCT
                )
            else:
                bad = exp == 0 or diff / abs(exp) * 100 > UNPRICED_TOLERANCE_PCT
            if not bad:
                continue
            if obs > exp:
                increases.append(
                    {
                        "account": account,
                        "asset": asset,
                        "amount": obs - exp,
                        "observed": obs,
                        "expected": exp,
                    }
                )
                continue
            rep.discrepancies.append(
                f"{venue}: {asset} expected {exp} but "
                + ("the venue no longer reports it" if missing else f"the venue reports {obs}")
            )
        return increases
