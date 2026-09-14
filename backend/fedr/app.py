"""Composition root: builds connectors for the active mode, wires engines and runs background loops."""

from __future__ import annotations

import asyncio
import contextlib
import random
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from fedr.app_utils import _deep_merge, _redact
from fedr.config.env import EnvSettings
from fedr.config.schema import AppSettings, RiskProfile, default_settings
from fedr.connectors.base import ConnectorError
from fedr.connectors.cex.ccxt_connector import CcxtConnector
from fedr.connectors.cex.registry import EXCHANGES
from fedr.connectors.dex.gateway_client import GatewayClient, GatewayError
from fedr.connectors.dex.gateway_connector import GatewayConnector
from fedr.connectors.dex.registry import DEFAULT_DEXES, DEXES
from fedr.connectors.paper.ledger import PaperLedger
from fedr.core.enums import Chain, CircuitBreakerReason, Decision, Strategy, TradingMode, VenueKind
from fedr.core.logging import configure_logging, get_logger
from fedr.core.models import TradeRecord, new_id, now_ms
from fedr.core.money import ZERO, D
from fedr.db.engine import Database
from fedr.db.models import ExchangeAccount, utcnow
from fedr.db.repo import Repo
from fedr.engine.circuit_breakers import Breaker, CircuitBreakerManager
from fedr.engine.context import EngineContext
from fedr.engine.emergency_stop import EmergencyStop
from fedr.engine.execution.executor import ExecutionEngine
from fedr.engine.execution.paper_executor import PaperExecutor
from fedr.engine.experience import ExperienceEngine
from fedr.engine.flashloan import FlashLoanEngine
from fedr.engine.inventory import InventoryManager
from fedr.engine.opportunity import OpportunityEngine
from fedr.engine.readiness import live_readiness, startup_health
from fedr.engine.rebalancer import Rebalancer
from fedr.engine.reconciliation import Reconciler
from fedr.engine.shadow import ShadowRecorder
from fedr.marketdata.gas import GasOracle
from fedr.marketdata.hub import MarketDataHub
from fedr.security.crypto import SecretBox, hash_phrase, load_or_create_master_key
from fedr.sim.synthetic import default_synthetic, dex_pairs_for
from fedr.sim.venues import SyntheticCexVenue, SyntheticDexVenue, SyntheticPerpVenue
from fedr.wallets.manager import WalletManager
from fedr.wallets.transfers import DepositMonitor, WithdrawalService

log = get_logger("app")

LIVE_CONFIRMATION_PHRASE = "ACTIVATE LIVE TRADING"


class FedrApp:
    def __init__(self, env: EnvSettings | None = None):
        self.env = env or EnvSettings()
        self.db: Database | None = None
        self.repo: Repo | None = None
        self.box: SecretBox | None = None
        self.settings: AppSettings = default_settings(self.env.default_mode)
        self.wallets: WalletManager | None = None
        self.exchange_accounts: list[ExchangeAccount] = []
        self.gateway: GatewayClient | None = None
        self.gateway_ok = False
        self.ctx: EngineContext | None = None
        self.opportunities: OpportunityEngine | None = None
        self.executor: ExecutionEngine | None = None
        self.flashloan: FlashLoanEngine | None = None
        self.reconciler: Reconciler | None = None
        self.estop: EmergencyStop | None = None
        self.shadow: ShadowRecorder | None = None
        self.deposits = DepositMonitor()
        self.withdrawals: WithdrawalService | None = None
        self.synthetic = None
        self._tasks: list[asyncio.Task] = []
        self._rebuild_lock = asyncio.Lock()
        self.started_at_ms = 0
        self.startup_check = None
        self.events: list[dict] = []  # recent UI events (trades, breakers, deposits)
        self.status_message = "starting"
        self._subscribers: set[asyncio.Queue] = set()

    # ------------------------------------------------------------------ lifecycle
    async def start(self) -> None:
        configure_logging(self.env.log_level, self.env.log_json)
        self.env.ensure_dirs()
        self.db = Database(self.env.database_url_resolved)
        await self.db.init()
        self.repo = Repo(self.db)
        self.box = SecretBox(load_or_create_master_key(self.env.master_key, self.env.data_dir))
        doc = await self.repo.get_settings_doc("app")
        if doc:
            try:
                self.settings = AppSettings.model_validate(doc)
            except Exception as exc:
                log.error("settings document invalid - using defaults", error=str(exc))
                self.settings = default_settings(self.env.default_mode)
        if self.settings.general.mode is TradingMode.LIVE and not (
            self.settings.live.activated and self.env.live_trading_allowed
        ):
            log.warning("live mode found in settings without a valid activation - reverting to PAPER")
            self.settings.general.mode = TradingMode.PAPER
            self.settings.live.activated = False
        await self.repo.save_settings_doc(self.settings.model_dump(mode="json"), "app")
        self.wallets = WalletManager(self.repo, self.box)
        await self.wallets.load()
        self.exchange_accounts = await self.repo.list_exchange_accounts()
        if self.env.gateway_enabled:
            self.gateway = GatewayClient(
                self.env.gateway_url, self.env.gateway_api_key, timeout_s=self.env.gateway_timeout_s
            )
            self.gateway_ok = await self.gateway.ping()
            if not self.gateway_ok:
                log.warning(
                    "gateway unreachable - DEX venues unavailable until it comes up", url=self.env.gateway_url
                )
        await self._build_context()
        await self.rebuild_connectors()
        self.estop = EmergencyStop(self.ctx, self.reconciler)
        await self.estop.restore()
        rows = await self.repo.load_active_breakers()
        self.ctx.breakers.restore(
            [
                Breaker(
                    reason=CircuitBreakerReason(r.reason),
                    detail=r.detail,
                    scope=r.scope,
                    auto=r.auto,
                    tripped_at_ms=int(r.tripped_at.timestamp() * 1000),
                )
                for r in rows
            ]
        )
        await self._load_daily_stats()
        self.started_at_ms = now_ms()
        self._tasks = [
            asyncio.create_task(self._scan_loop(), name="scan"),
            asyncio.create_task(self._health_loop(), name="health"),
            asyncio.create_task(self._gas_loop(), name="gas"),
            asyncio.create_task(self._balance_loop(), name="balances"),
            asyncio.create_task(self._reconcile_loop(), name="reconcile"),
            asyncio.create_task(self._housekeeping_loop(), name="housekeeping"),
        ]
        await asyncio.sleep(0.5)
        self.startup_check = await startup_health(self)
        self.status_message = "SYSTEM READY" if self.startup_check.ready else "issues detected"
        log.info(
            "startup", status=self.status_message, mode=self.settings.general.mode.value, port=self.env.port
        )
        await self.repo.audit(
            self.mode, "system", f"Startup: {self.status_message}", self.startup_check.as_dict()
        )

    async def stop(self) -> None:
        for t in self._tasks:
            t.cancel()
        for t in self._tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await t
        if self.ctx:
            await self.ctx.hub.stop()
            for c in self.ctx.connectors.values():
                with contextlib.suppress(Exception):
                    await c.close()
            if self.ctx.ledger is not None and self.repo is not None:
                await self.repo.save_paper_balances(self.mode, self.ctx.ledger.dump())
        if self.gateway:
            await self.gateway.close()
        if self.db:
            await self.db.close()

    @property
    def mode(self) -> TradingMode:
        return self.settings.general.mode

    # ------------------------------------------------------------------ context / connectors
    async def _build_context(self) -> None:
        hub = MarketDataHub(on_update=None)
        hub.ws_enabled = self.settings.advanced.websocket_market_data
        hub.rest_interval_ms = self.settings.advanced.rest_poll_interval_ms
        hub.depth = self.settings.advanced.orderbook_depth
        inventory = InventoryManager(hub.prices.get)
        breakers = CircuitBreakerManager(on_change=self._persist_breakers)
        experience = ExperienceEngine(
            self.repo,
            self.settings.advanced.experience_max_extra_buffer_pct,
            self.settings.advanced.experience_learning_enabled,
        )
        await experience.load()
        rebalancer = Rebalancer(self.settings.rebalance, inventory)
        gas_oracle = GasOracle(hub.prices.get, self.gateway if self.gateway_ok else None)
        self.ctx = EngineContext(
            env=self.env,
            settings=self.settings,
            repo=self.repo,
            hub=hub,
            gas_oracle=gas_oracle,
            breakers=breakers,
            inventory=inventory,
            experience=experience,
            rebalancer=rebalancer,
        )
        self.opportunities = OpportunityEngine(self.ctx)
        self.executor = ExecutionEngine(self.ctx, self.opportunities, on_trade=self._on_trade)
        self.flashloan = FlashLoanEngine(self.ctx, self.wallets, self.env)
        self.ctx.flash_loan_simulator = self.flashloan.simulate
        self.reconciler = Reconciler(self.ctx)
        self.shadow = ShadowRecorder(self.ctx)
        self.withdrawals = WithdrawalService(self.wallets, self.env, hub.prices.get)

    async def _persist_breakers(self, breakers: list[Breaker]) -> None:
        if self.repo:
            await self.repo.save_breakers(breakers)
        self._push_event("breakers", {"active": [b.as_dict() for b in breakers]})

    async def rebuild_connectors(self) -> None:
        """(Re)create venue connectors for the current mode and settings."""
        async with self._rebuild_lock:
            ctx = self.ctx
            await ctx.hub.stop()
            for c in ctx.connectors.values():
                with contextlib.suppress(Exception):
                    await c.close()
            ctx.connectors.clear()
            ctx.health.clear()
            ctx.hub.books.clear()
            mode = self.mode
            s = self.settings
            pairs = s.general.pairs
            ctx.ledger = None
            ctx.paper_executor = None
            self.synthetic = None
            if mode is TradingMode.SIMULATION:
                self.synthetic = default_synthetic(pairs, seed=self.env.demo_seed or 42)
                ctx.hub.prices.external = self.synthetic.price_usd
                ctx.gas_oracle.synthetic = self.synthetic
                for name, fee in (
                    ("kraken", Decimal("0.26")),
                    ("coinbase", Decimal("0.60")),
                    ("binance", Decimal("0.10")),
                ):
                    ctx.connectors[name] = SyntheticCexVenue(self.synthetic, name, pairs, fee)
                for name, chain in (
                    ("jupiter", Chain.SOLANA),
                    ("uniswap-base", Chain.BASE),
                    ("uniswap-arbitrum", Chain.ARBITRUM),
                ):
                    ctx.connectors[name] = SyntheticDexVenue(
                        self.synthetic, name, dex_pairs_for(chain, pairs), chain
                    )
                ctx.connectors["binance-perp"] = SyntheticPerpVenue(
                    self.synthetic, "binance-perp", pairs, Decimal("0.05")
                )
            else:
                ctx.hub.prices.external = None
                ctx.gas_oracle.synthetic = None
                ctx.gas_oracle.testnet = mode is TradingMode.TESTNET
                await self._build_cex_connectors(mode, pairs)
                await self._build_dex_connectors(mode, pairs)

            # connect everything (tolerating failures - a venue that fails stays SUPPORTED, not CONNECTED)
            async def _connect(c):
                try:
                    await asyncio.wait_for(c.connect(), timeout=max(10.0, s.advanced.connector_timeout_s * 3))
                except Exception as exc:
                    c.last_error = str(exc)[:200]
                    log.warning("connector failed to connect", venue=c.name, error=str(exc)[:200])

            await asyncio.gather(*(_connect(c) for c in list(ctx.connectors.values())))
            if mode in (TradingMode.SIMULATION, TradingMode.PAPER):
                ctx.ledger = PaperLedger()
                saved = await self.repo.load_paper_balances(mode) if self.repo else {}
                if saved:
                    ctx.ledger.load(saved)
                else:
                    ctx.ledger.seed(s.paper.starting_balances)
                    if self.repo:
                        await self.repo.save_paper_balances(mode, ctx.ledger.dump())
                ctx.paper_executor = PaperExecutor(
                    ctx.ledger,
                    lambda: self.settings.paper,
                    ctx.gas_oracle.snapshot,
                    ctx.gas_guard,
                    rng=random.Random(self.env.demo_seed),
                )
            await self._refresh_balances()
            for chain in self._chains_in_use():
                await ctx.gas_oracle.refresh(chain)
            await ctx.hub.start(
                [c for c in ctx.connectors.values() if c.connected],
                lambda c: (
                    [p for p in pairs if c.supports_symbol(p)]
                    + (
                        [f"{p.split('/')[0]}/{p.split('/')[1]}:{p.split('/')[1]}" for p in pairs]
                        if c.kind is VenueKind.PERP
                        else []
                    )
                ),
            )
            if mode is TradingMode.SIMULATION:
                # prime the books so the first scan has data
                for c in ctx.connectors.values():
                    if c.kind in (VenueKind.CEX, VenueKind.PERP):
                        for sym in c.markets:
                            with contextlib.suppress(Exception):
                                await ctx.hub.ingest(await c.fetch_order_book(sym))
            await self._health_check_all()
            log.info(
                "connectors rebuilt",
                mode=mode.value,
                venues=[c.name for c in ctx.connectors.values() if c.connected],
            )

    async def _build_cex_connectors(self, mode: TradingMode, pairs: list[str]) -> None:
        ctx = self.ctx
        s = self.settings
        accounts = [a for a in self.exchange_accounts if a.enabled and a.mode == mode.value]
        wanted: dict[str, dict | None] = {}
        if mode is TradingMode.PAPER:
            for venue in s.paper.starting_balances:
                if venue in EXCHANGES:
                    wanted[venue] = None  # public market data only
        for a in accounts:
            wanted[a.exchange_id] = self._decrypt_credentials(a)
        for exchange_id, creds in wanted.items():
            spec = EXCHANGES.get(exchange_id)
            if spec is None:
                continue
            sandbox = mode is TradingMode.TESTNET
            if sandbox and not spec.sandbox:
                log.warning("exchange has no sandbox - skipped in TESTNET", exchange=exchange_id)
                continue
            c = CcxtConnector(
                exchange_id, mode, creds, sandbox=sandbox, timeout_s=s.advanced.connector_timeout_s
            )
            c.trading_enabled = bool(creds) and mode in (TradingMode.TESTNET, TradingMode.LIVE)
            ctx.connectors[exchange_id] = c
            if spec.perps and (s.strategies.spot_perp or s.strategies.funding or s.strategies.basis):
                pid = spec.perp_id or exchange_id
                pc = CcxtConnector(
                    pid,
                    mode,
                    creds,
                    sandbox=sandbox,
                    market_type="swap",
                    name=f"{exchange_id}-perp",
                    timeout_s=s.advanced.connector_timeout_s,
                )
                pc.trading_enabled = c.trading_enabled
                ctx.connectors[pc.name] = pc

    async def _build_dex_connectors(self, mode: TradingMode, pairs: list[str]) -> None:
        ctx = self.ctx
        s = self.settings
        if not (self.gateway and self.gateway_ok):
            return
        enabled = self.settings.general.dexes or DEFAULT_DEXES
        testnet = mode is TradingMode.TESTNET
        for name in enabled:
            spec = DEXES.get(name)
            if spec is None:
                continue
            if testnet and not spec.testnet_network:
                continue
            wallet_mode = "testnet" if testnet else "live"
            address = (
                self.wallets.address_for_chain(spec.chain, wallet_mode)
                if mode in (TradingMode.TESTNET, TradingMode.LIVE)
                else None
            )
            c = GatewayConnector(
                spec,
                self.gateway,
                mode,
                address,
                pairs,
                s.slippage.dex_slippage_tolerance_pct,
                native_usd_price=ctx.hub.prices.get,
                testnet=testnet,
            )
            ctx.connectors[name] = c

    def _decrypt_credentials(self, a: ExchangeAccount) -> dict[str, str] | None:
        if not a.credentials_enc or self.box is None:
            return None
        import json

        try:
            return json.loads(self.box.decrypt_str(a.credentials_enc, aad=a.id))
        except Exception as exc:
            log.error("cannot decrypt credentials", account=a.id, error=str(exc))
            return None

    def _chains_in_use(self) -> set[Chain]:
        return {c.chain for c in self.ctx.connectors.values() if c.chain is not None}

    # ------------------------------------------------------------------ loops
    async def _scan_loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(max(0.3, self.settings.general.scan_interval_ms / 1000))
                if self.synthetic is not None:
                    self.synthetic.tick()
                    for c in self.ctx.connectors.values():
                        if c.kind in (VenueKind.CEX, VenueKind.PERP):
                            for sym in c.markets:
                                await self.ctx.hub.ingest(await c.fetch_order_book(sym))
                opps = await self.opportunities.scan()
                if self.settings.general.shadow_mode and self.shadow:
                    for o in opps:
                        await self.shadow.record(o)
                    continue
                if (
                    not self.settings.general.bot_enabled
                    or not self.settings.trading.auto_execute
                    or self.ctx.emergency_stop
                ):
                    continue
                for o in opps:
                    if o.decision is not Decision.SAFE_TO_EXECUTE:
                        break
                    if len(self.ctx.open_trade_ids) >= self.settings.risk.max_concurrent_trades:
                        break
                    if o.strategy is Strategy.FLASH_LOAN:
                        await self.flashloan.execute(o, self.opportunities, trigger="auto")
                    else:
                        await self.executor.execute(o, trigger="auto")
                    break  # one execution per scan: the next tick re-evaluates everything with fresh quotes
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.error("scan loop error", error=str(exc))
                await asyncio.sleep(2)

    async def _health_loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(self.settings.advanced.health_check_interval_s)
                await self._health_check_all()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.error("health loop error", error=str(exc))

    async def _health_check_all(self) -> None:
        for c in list(self.ctx.connectors.values()):
            if not c.connected:
                continue
            try:
                self.ctx.health[c.name] = await asyncio.wait_for(c.health_check(), timeout=15)
            except Exception as exc:
                log.warning("health check failed", venue=c.name, error=str(exc)[:120])
        if self.gateway:
            self.gateway_ok = await self.gateway.ping()

    async def _gas_loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(15)
                for chain in self._chains_in_use():
                    await self.ctx.gas_oracle.refresh(chain)
                    snap = self.ctx.gas_oracle.snapshot(chain)
                    if snap is not None:
                        regime = self.ctx.gas_guard.regime(chain, snap.gas_price_native)
                        if regime.value == "extreme":
                            await self.ctx.breakers.trip(
                                CircuitBreakerReason.GAS_SPIKE,
                                f"gas on {chain.value} is {snap.gas_price_native} (extreme regime)",
                                scope=f"chain:{chain.value}",
                            )
                        else:
                            await self.ctx.breakers.reset(
                                CircuitBreakerReason.GAS_SPIKE, scope=f"chain:{chain.value}"
                            )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.error("gas loop error", error=str(exc))

    async def _balance_loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(20)
                await self._refresh_balances()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.error("balance loop error", error=str(exc))

    async def _refresh_balances(self) -> None:
        ctx = self.ctx
        if ctx.ledger is not None:
            for venue in ctx.ledger.venues():
                kind = "chain" if venue in {c.value for c in Chain} else "cex"
                ctx.inventory.update_balances(venue, ctx.ledger.balances(venue), kind=kind)
            return
        seen_chains: set[str] = set()
        for c in list(ctx.connectors.values()):
            if not c.connected:
                continue
            lv = ctx.ledger_venue_for(c)
            if c.kind is VenueKind.DEX and lv in seen_chains:
                continue
            try:
                bals = await c.fetch_balances()
            except ConnectorError as exc:
                log.warning("balance fetch failed", venue=c.name, error=str(exc)[:120])
                continue
            if c.kind is VenueKind.DEX:
                seen_chains.add(lv)
                for ev in self.deposits.observe(lv, {a: b.total for a, b in bals.items()}):
                    self._push_event(
                        "deposit", {"chain": ev.chain, "asset": ev.asset, "amount": str(ev.amount)}
                    )
                    if self.repo:
                        await self.repo.audit(
                            self.mode,
                            "wallet",
                            f"Deposit detected: {ev.amount} {ev.asset} on {ev.chain}",
                            {"chain": ev.chain, "asset": ev.asset, "amount": str(ev.amount)},
                        )
            ctx.inventory.update_balances(lv, bals, kind="chain" if c.kind is VenueKind.DEX else "cex")
            for a, b in bals.items():
                self.reconciler.set_expected(lv, a, b.total)
            if self.repo:
                await self.repo.snapshot_balances(
                    self.mode,
                    lv,
                    {
                        a: (b.free, b.used, (b.total * px) if (px := ctx.price_usd(a)) else None)
                        for a, b in bals.items()
                    },
                )

    async def _reconcile_loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(self.settings.advanced.reconciliation_interval_s)
                self.ctx.extra["recent_trades"] = self.executor.recent[-20:] if self.executor else []
                await self.reconciler.run_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.error("reconcile loop error", error=str(exc))

    async def _housekeeping_loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(300)
                if self.repo:
                    await self.repo.prune_opportunities(self.settings.advanced.max_opportunities_kept * 10)
                    await self.repo.prune_balance_snapshots()
                await self._load_daily_stats()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.error("housekeeping error", error=str(exc))

    async def _load_daily_stats(self) -> None:
        if not self.repo:
            return
        summary = await self.repo.pnl_summary(self.mode)
        self.ctx.daily_pnl_usd = D(summary["today_net"])
        self.ctx.failed_trades_last_hour = await self.repo.count_failed_trades_since(
            self.mode, utcnow() - timedelta(hours=1)
        )

    # ------------------------------------------------------------------ events
    def _push_event(self, kind: str, payload: dict) -> None:
        ev = {"ts": now_ms(), "kind": kind, **payload}
        self.events.append(ev)
        self.events = self.events[-200:]
        for q in list(self._subscribers):
            with contextlib.suppress(asyncio.QueueFull):
                q.put_nowait(ev)

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=200)
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subscribers.discard(q)

    async def _on_trade(self, tr: TradeRecord) -> None:
        self._push_event(
            "trade",
            {
                "id": tr.id,
                "status": tr.status.value,
                "pair": tr.pair,
                "route": tr.route,
                "estimated_net": str(tr.estimated_net),
                "actual_net": str(tr.actual_net) if tr.actual_net is not None else None,
                "explanation": tr.explanation,
            },
        )

    # ------------------------------------------------------------------ settings
    async def update_settings(self, patch: dict[str, Any], actor: str = "user") -> AppSettings:
        current = self.settings.model_dump(mode="json")
        merged = _deep_merge(current, patch)
        new = AppSettings.model_validate(merged)
        if "risk_profile" in patch.get("general", {}) and new.general.risk_profile is not RiskProfile.CUSTOM:
            new.apply_risk_profile(new.general.risk_profile)
        elif any(k in patch for k in ("trading", "risk", "gas")):
            new.general.risk_profile = RiskProfile.CUSTOM
        if new.general.mode is TradingMode.LIVE and not (
            self.settings.live.activated and self.env.live_trading_allowed
        ):
            raise ValueError("LIVE mode can only be entered through the live activation workflow")
        mode_changed = new.general.mode is not self.settings.general.mode
        if mode_changed and self.settings.general.mode is TradingMode.LIVE:
            new.live.activated = False  # leaving live always deactivates
        structural = (
            mode_changed
            or new.general.pairs != self.settings.general.pairs
            or new.general.dexes != self.settings.general.dexes
            or new.strategies != self.settings.strategies
            or new.paper.starting_balances != self.settings.paper.starting_balances
            and self.mode in (TradingMode.PAPER, TradingMode.SIMULATION)
            and not (await self.repo.load_paper_balances(new.general.mode) if self.repo else {})
        )
        if (
            self.mode in (TradingMode.PAPER, TradingMode.SIMULATION)
            and self.ctx.ledger is not None
            and self.repo
        ):
            await self.repo.save_paper_balances(self.mode, self.ctx.ledger.dump())
        old_mode = self.mode
        self.settings = new
        self.ctx.settings = new
        self.ctx.rebalancer.settings = new.rebalance
        self.ctx.experience.max_extra = new.advanced.experience_max_extra_buffer_pct
        self.ctx.experience.enabled = new.advanced.experience_learning_enabled
        if self.repo:
            await self.repo.save_settings_doc(new.model_dump(mode="json"), "app")
            await self.repo.audit(
                new.general.mode,
                "mode_change" if mode_changed else "settings_change",
                f"Settings updated ({', '.join(patch.keys())})"
                if not mode_changed
                else f"Mode changed {old_mode.value} -> {new.general.mode.value}",
                {"patch": _redact(patch)},
                actor=actor,
            )
        if structural:
            await self.rebuild_connectors()
            await self._load_daily_stats()
        return new

    # ------------------------------------------------------------------ live activation
    async def activate_live(self, confirmation: str, actor: str = "user") -> dict:
        checklist = await live_readiness(self)
        if confirmation.strip() != LIVE_CONFIRMATION_PHRASE:
            raise ValueError(f"type the exact confirmation phrase: {LIVE_CONFIRMATION_PHRASE}")
        if not checklist.ready:
            failed = [i.label for i in checklist.items if i.required and not i.ok]
            raise ValueError("readiness checklist not satisfied: " + ", ".join(failed))
        self.settings.live.activated = True
        self.settings.live.activated_at = datetime.now(UTC).isoformat()
        self.settings.live.activated_by = actor
        self.settings.live.confirmation_phrase_hash = hash_phrase(confirmation)
        self.settings.general.mode = TradingMode.LIVE
        self.settings.general.shadow_mode = (
            True  # live starts in shadow mode: one more explicit step to submit orders
        )
        self.settings.trading.auto_execute = False
        self.ctx.settings = self.settings
        if self.repo:
            await self.repo.save_settings_doc(self.settings.model_dump(mode="json"), "app")
            await self.repo.audit(
                TradingMode.LIVE,
                "live_activation",
                "LIVE TRADING ACTIVATED (shadow mode on, auto-execute off)",
                checklist.as_dict(),
                actor=actor,
            )
        await self.rebuild_connectors()
        return checklist.as_dict()

    async def deactivate_live(self, actor: str = "user") -> None:
        await self.update_settings(
            {"general": {"mode": TradingMode.PAPER.value}, "live": {"activated": False}}, actor=actor
        )

    # ------------------------------------------------------------------ exchange accounts
    async def add_exchange_account(
        self,
        exchange_id: str,
        label: str,
        credentials: dict[str, str],
        mode: str,
        sandbox: bool | None = None,
    ) -> dict:
        import json

        spec = EXCHANGES.get(exchange_id)
        if spec is None:
            raise ValueError(f"unsupported exchange '{exchange_id}'")
        if mode not in ("testnet", "live"):
            raise ValueError("credential scope must be testnet or live")
        creds = {k: v for k, v in credentials.items() if v}
        sandbox = mode == "testnet" if sandbox is None else sandbox
        if sandbox and not spec.sandbox:
            raise ValueError(f"{spec.display_name} has no sandbox/testnet in ccxt: {spec.sandbox_note}")
        acct = ExchangeAccount(
            id=new_id("exa"),
            exchange_id=exchange_id,
            label=label or spec.display_name,
            mode=mode,
            enabled=True,
            sandbox=sandbox,
            credentials_enc=self.box.encrypt(json.dumps(creds), aad=""),
            permissions={},
            status="supported",
        )
        acct.credentials_enc = self.box.encrypt(json.dumps(creds), aad=acct.id)
        result = await self._test_credentials(exchange_id, creds, sandbox)
        acct.status = "connected" if result["ok"] else "supported"
        acct.permissions = result.get("permissions", {})
        acct.last_error = None if result["ok"] else result.get("error")
        acct.last_verified_at = utcnow() if result["ok"] else None
        await self.repo.upsert_exchange_account(acct)
        self.exchange_accounts = await self.repo.list_exchange_accounts()
        await self.repo.audit(
            self.mode,
            "exchange",
            f"Exchange account added: {spec.display_name} ({mode})",
            {"exchange": exchange_id, "ok": result["ok"], "permissions": result.get("permissions")},
        )
        if result["ok"] and self.mode.value == mode:
            await self.rebuild_connectors()
        return {"id": acct.id, **result}

    async def _test_credentials(self, exchange_id: str, creds: dict[str, str], sandbox: bool) -> dict:
        c = CcxtConnector(
            exchange_id,
            TradingMode.TESTNET if sandbox else TradingMode.LIVE,
            creds,
            sandbox=sandbox,
            timeout_s=self.settings.advanced.connector_timeout_s,
        )
        try:
            await asyncio.wait_for(c.connect(), timeout=30)
            return {"ok": True, "permissions": c.permissions, "markets": len(c.markets)}
        except Exception as exc:
            return {"ok": False, "error": str(exc)[:300]}
        finally:
            await c.close()

    async def test_exchange_account(self, account_id: str) -> dict:
        acct = next((a for a in self.exchange_accounts if a.id == account_id), None)
        if acct is None:
            raise ValueError("unknown account")
        creds = self._decrypt_credentials(acct) or {}
        result = await self._test_credentials(acct.exchange_id, creds, acct.sandbox)
        await self.repo.update_exchange_account(
            account_id,
            status="connected" if result["ok"] else "supported",
            last_error=None if result["ok"] else result.get("error"),
            permissions=result.get("permissions", {}),
            last_verified_at=utcnow() if result["ok"] else acct.last_verified_at,
        )
        self.exchange_accounts = await self.repo.list_exchange_accounts()
        return result

    async def remove_exchange_account(self, account_id: str) -> None:
        await self.repo.delete_exchange_account(account_id)
        self.exchange_accounts = await self.repo.list_exchange_accounts()
        await self.repo.audit(self.mode, "exchange", f"Exchange account removed: {account_id}", {})
        await self.rebuild_connectors()

    async def set_exchange_enabled(self, account_id: str, enabled: bool) -> None:
        await self.repo.update_exchange_account(account_id, enabled=enabled)
        self.exchange_accounts = await self.repo.list_exchange_accounts()
        await self.rebuild_connectors()

    # ------------------------------------------------------------------ wallets
    async def register_wallet_with_gateway(self, wallet_id: str) -> str | None:
        if not (self.gateway and self.gateway_ok):
            return None
        w = next((x for x in self.wallets.list() if x.id == wallet_id), None)
        if w is None or w.kind != "bot":
            return None
        chain = "solana" if w.family == "solana" else "ethereum"
        try:
            addr = await self.gateway.add_wallet(chain, self.wallets.signer_secret_for_gateway(wallet_id))
        except GatewayError as exc:
            log.warning("gateway wallet registration failed", error=str(exc))
            return None
        if self.repo:
            await self.repo.audit(
                self.mode, "wallet", f"Bot wallet registered with Gateway ({chain})", {"address": addr}
            )
        return addr

    # ------------------------------------------------------------------ paper account
    async def reset_paper(self) -> None:
        if self.mode not in (TradingMode.PAPER, TradingMode.SIMULATION) or self.ctx.ledger is None:
            raise ValueError("reset is only available in paper/simulation mode")
        self.ctx.ledger.seed(self.settings.paper.starting_balances)
        await self.repo.purge_mode_results(self.mode)
        await self.repo.save_paper_balances(self.mode, self.ctx.ledger.dump())
        self.ctx.daily_pnl_usd = ZERO
        self.ctx.failed_trades_last_hour = 0
        self.executor.recent.clear()
        self.opportunities.latest.clear()
        await self.ctx.breakers.reset()
        await self._refresh_balances()
        await self.repo.audit(self.mode, "settings_change", "Paper account reset", {})

    async def adjust_paper_funds(self, venue: str, asset: str, amount: Decimal) -> None:
        if self.ctx.ledger is None:
            raise ValueError("paper ledger not active")
        await self.ctx.ledger.adjust(venue, asset, D(amount))
        await self.repo.save_paper_balances(self.mode, self.ctx.ledger.dump())
        await self._refresh_balances()
        await self.repo.audit(
            self.mode, "settings_change", f"Paper funds adjusted: {amount} {asset} on {venue}", {}
        )

    # ------------------------------------------------------------------ state for the UI
    def capital_summary(self) -> dict[str, str]:
        inv = self.ctx.inventory
        gas_usd = ZERO
        for chain in Chain:
            reserve = self.settings.gas.gas_reserve.get(chain.value)
            px = self.ctx.price_usd(chain.native_token)
            if reserve and px:
                have = inv.available(chain.value, chain.native_token)
                gas_usd += min(have, D(reserve)) * px
        at_risk = (
            sum((D(t.estimated_costs.get("total", "0")) for t in self.executor.active.values()), ZERO)
            if self.executor
            else ZERO
        )
        cb = self.ctx.risk_engine.capital_breakdown(inv.total_usd(), gas_usd, at_risk)
        return {k: str(v.quantize(Decimal("0.01"))) for k, v in cb.items()}
