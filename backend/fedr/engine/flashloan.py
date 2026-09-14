"""Flash-loan engine (optional, OFF by default).

Only atomically executable same-chain DEX -> DEX routes are eligible. Every
execution goes: fresh quotes -> Profit Guard (with flash-loan terms) ->
simulation (eth_call + estimateGas) -> abort-before-broadcast if anything
changed -> broadcast (private RPC when configured) -> receipt -> accounting.

In SIMULATION/PAPER the atomic outcome is computed from the executable quotes
and the paper ledger is settled; nothing is broadcast.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from fedr.core.enums import Chain, Decision, OrderSide, OrderStatus, Strategy, TradeStatus, TradingMode
from fedr.core.logging import get_logger
from fedr.core.models import Fill, Opportunity, OrderRequest, OrderResult, TradeRecord, new_id, now_ms
from fedr.core.money import ZERO, D, fmt_money
from fedr.engine.accounting import apply_outcome, compute_outcome

log = get_logger("flashloan")

AAVE_V3_POOLS: dict[Chain, str] = {
    Chain.ETHEREUM: "0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2",
    Chain.ARBITRUM: "0x794a61358D6845594F94dc1DB02A252b5b4814aD",
    Chain.OPTIMISM: "0x794a61358D6845594F94dc1DB02A252b5b4814aD",
    Chain.POLYGON: "0x794a61358D6845594F94dc1DB02A252b5b4814aD",
    Chain.AVALANCHE: "0x794a61358D6845594F94dc1DB02A252b5b4814aD",
    Chain.BASE: "0xA238Dd80C259a72e81d7e4664a9801593F98d1c5",
    Chain.BSC: "0x6807dc923806fE8Fd134338EABCA509979a7e0cB",
}

CONTRACT_ABI: list[dict[str, Any]] = [
    {
        "type": "function",
        "name": "executeArbitrage",
        "stateMutability": "nonpayable",
        "inputs": [
            {"name": "asset", "type": "address"},
            {"name": "amount", "type": "uint256"},
            {
                "name": "p",
                "type": "tuple",
                "components": [
                    {"name": "intermediate", "type": "address"},
                    {
                        "name": "legA",
                        "type": "tuple",
                        "components": [
                            {"name": "router", "type": "address"},
                            {"name": "kind", "type": "uint8"},
                            {"name": "fee", "type": "uint24"},
                            {"name": "minAmountOut", "type": "uint256"},
                        ],
                    },
                    {
                        "name": "legB",
                        "type": "tuple",
                        "components": [
                            {"name": "router", "type": "address"},
                            {"name": "kind", "type": "uint8"},
                            {"name": "fee", "type": "uint24"},
                            {"name": "minAmountOut", "type": "uint256"},
                        ],
                    },
                    {"name": "minProfit", "type": "uint256"},
                    {"name": "deadline", "type": "uint256"},
                ],
            },
        ],
        "outputs": [],
    },
    {
        "type": "function",
        "name": "paused",
        "stateMutability": "view",
        "inputs": [],
        "outputs": [{"type": "bool"}],
    },
    {
        "type": "function",
        "name": "allowedRouters",
        "stateMutability": "view",
        "inputs": [{"type": "address"}],
        "outputs": [{"type": "bool"}],
    },
]


@dataclass(slots=True)
class SimulationResult:
    ok: bool
    reason: str = ""
    gas_estimate: int | None = None
    expected_profit_asset: Decimal | None = None


class FlashLoanEngine:
    def __init__(self, ctx, wallets, env):
        self.ctx = ctx
        self.wallets = wallets
        self.env = env
        self.last_simulation: dict[str, SimulationResult] = {}

    # ------------------------------------------------------------------ availability
    def available_on(self, chain: Chain) -> tuple[bool, str]:
        s = self.ctx.settings.flash_loan
        if not s.enabled:
            return False, "flash loans disabled"
        if chain.value not in s.chains:
            return False, f"chain {chain.value} not enabled for flash loans"
        if self.ctx.mode in (TradingMode.SIMULATION, TradingMode.PAPER):
            return True, "simulated atomic execution"
        if not self.env.flashloan_contract_for(chain.value):
            return (
                False,
                f"no FlashLoanArbitrage contract configured for {chain.value} (FEDR_FLASHLOAN_CONTRACT_{chain.value.upper()})",
            )
        if not self.env.rpc_for(chain.value):
            return False, f"no RPC configured for {chain.value}"
        if chain not in AAVE_V3_POOLS:
            return False, f"no Aave V3 pool known for {chain.value}"
        return True, "contract + RPC configured"

    # ------------------------------------------------------------------ simulation
    async def simulate(self, cand, buy_q, sell_q, size: Decimal) -> bool:
        """Called by the opportunity engine for FLASH_LOAN candidates."""
        chain = buy_q.chain
        ok, reason = self.available_on(chain) if chain else (False, "no chain")
        key = f"{cand.pair}|{cand.buy.name}->{cand.sell.name}"
        if not ok:
            self.last_simulation[key] = SimulationResult(False, reason)
            return False
        s = self.ctx.settings.flash_loan
        if self.ctx.mode in (TradingMode.SIMULATION, TradingMode.PAPER):
            # atomic outcome from executable quotes: borrow quote asset, buy base on A, sell base on B, repay
            loan = buy_q.quote_amount
            fee = loan * Decimal("0.0005")
            proceeds = sell_q.quote_amount
            profit = proceeds - loan - fee
            min_profit_quote = s.min_net_profit_usd  # quote assumed USD-stable for these routes
            res = SimulationResult(
                profit >= min_profit_quote,
                ""
                if profit >= min_profit_quote
                else f"would revert: net {fmt_money(profit)} below minProfit {fmt_money(min_profit_quote)}",
                gas_estimate=420_000,
                expected_profit_asset=profit,
            )
            self.last_simulation[key] = res
            return res.ok
        try:
            res = await self._simulate_onchain(chain, cand, buy_q, sell_q, size)
        except Exception as exc:
            res = SimulationResult(False, f"simulation error: {exc}")
        self.last_simulation[key] = res
        return res.ok

    async def _simulate_onchain(self, chain: Chain, cand, buy_q, sell_q, size: Decimal) -> SimulationResult:
        from web3 import AsyncHTTPProvider, AsyncWeb3

        rpc = self.env.rpc_for(chain.value)
        contract_addr = self.env.flashloan_contract_for(chain.value)
        wallet = self.wallets.bot_wallet("evm")
        if wallet is None:
            return SimulationResult(False, "no EVM bot wallet")
        w3 = AsyncWeb3(AsyncHTTPProvider(rpc))
        contract = w3.eth.contract(address=w3.to_checksum_address(contract_addr), abi=CONTRACT_ABI)
        params = self._build_params(cand, buy_q, sell_q)
        if params is None:
            return SimulationResult(
                False,
                "route cannot be encoded: DEX venues must expose router/token addresses via Gateway token metadata",
            )
        asset, amount, p = params
        fn = contract.functions.executeArbitrage(asset, amount, p)
        tx = {"from": w3.to_checksum_address(wallet.address)}
        try:
            await fn.call(tx)  # eth_call: reverts if the route is unprofitable / minOut violated
            gas = await fn.estimate_gas(tx)
        except Exception as exc:
            return SimulationResult(False, f"eth_call reverted: {str(exc)[:200]}")
        return SimulationResult(True, "eth_call ok", gas_estimate=int(gas))

    def _build_params(self, cand, buy_q, sell_q):
        """Encode the two legs. Requires router + token addresses from the DEX connector metadata."""
        m_buy = cand.buy.market(cand.pair)
        m_sell = cand.sell.market(cand.pair)
        if not m_buy or not m_sell:
            return None
        base_addr = m_buy.extra.get("base_address")
        quote_addr = m_buy.extra.get("quote_address")
        router_a = getattr(cand.buy.spec, "router_address", None) if hasattr(cand.buy, "spec") else None
        router_b = getattr(cand.sell.spec, "router_address", None) if hasattr(cand.sell, "spec") else None
        if not (base_addr and quote_addr and router_a and router_b):
            return None
        quote_dec = int(m_buy.extra.get("quote_decimals", 6))
        base_dec = int(m_buy.extra.get("base_decimals", 18))
        s = self.ctx.settings.flash_loan
        slip = Decimal(s.max_slippage_bps) / Decimal(10_000)
        amount = int(buy_q.quote_amount * Decimal(10**quote_dec))
        min_a = int(buy_q.base_amount * (1 - slip) * Decimal(10**base_dec))
        min_b = int(sell_q.quote_amount * (1 - slip) * Decimal(10**quote_dec))
        min_profit = int(s.min_net_profit_usd * Decimal(10**quote_dec))
        deadline = int(now_ms() / 1000) + 90
        p = (
            quote_addr,
            (router_a, 0, int(getattr(cand.buy.spec, "fee_tier", 3000)), min_a),
            (router_b, 0, int(getattr(cand.sell.spec, "fee_tier", 3000)), min_b),
            min_profit,
            deadline,
        )
        return quote_addr, amount, p

    # ------------------------------------------------------------------ execution
    async def execute(self, opp: Opportunity, opportunity_engine, trigger: str = "auto") -> TradeRecord:
        ctx = self.ctx
        s = ctx.settings
        tr = TradeRecord(
            id=new_id("fltrd"),
            mode=ctx.mode,
            strategy=Strategy.FLASH_LOAN,
            opportunity_id=opp.id,
            pair=opp.pair,
            route=opp.route,
            size_base=opp.size_base,
            status=TradeStatus.PENDING,
            estimated_gross=opp.profit.gross_profit,
            estimated_net=opp.profit.expected_net_profit,
            estimated_worst_case=opp.profit.worst_case_profit,
            estimated_costs=opp.profit.expected_costs.as_dict(),
        )
        tr.log("start", trigger=trigger)
        if ctx.emergency_stop:
            return await self._abort(tr, "EMERGENCY STOP is active")
        if not s.flash_loan.enabled:
            return await self._abort(tr, "flash loans are disabled")
        cand = opportunity_engine.candidate_for(opp)
        if cand is None:
            return await self._abort(tr, "venue no longer available")
        fresh = await opportunity_engine.evaluate(cand, size=opp.size_base)  # re-quote + re-simulate
        if fresh is None or fresh.decision is not Decision.SAFE_TO_EXECUTE:
            reasons = fresh.block_reasons[:3] if fresh else ["could not re-quote"]
            return await self._abort(
                tr, "route no longer profitable after quote refresh: " + "; ".join(reasons)
            )
        tr.estimated_gross, tr.estimated_net, tr.estimated_worst_case = (
            fresh.profit.gross_profit,
            fresh.profit.expected_net_profit,
            fresh.profit.worst_case_profit,
        )
        tr.estimated_costs = fresh.profit.expected_costs.as_dict()
        ctx.open_trade_ids.add(tr.id)
        tr.status = TradeStatus.EXECUTING
        try:
            if ctx.mode in (TradingMode.SIMULATION, TradingMode.PAPER):
                await self._settle_paper(tr, fresh)
            else:
                await self._broadcast(tr, fresh, cand)
            outcome = compute_outcome(tr, ctx.price_usd(fresh.quote) or Decimal("1"), tr.estimated_costs)
            apply_outcome(tr, outcome)
            if tr.status is TradeStatus.FILLED:
                tr.explanation = f"Atomic flash-loan arbitrage executed: estimated net {fmt_money(tr.estimated_net)}, realized net {fmt_money(outcome.actual_net)} after gas {fmt_money(outcome.gas)}."
            if ctx.repo:
                await ctx.repo.save_trade(tr)
                if tr.actual_net is not None and tr.status is TradeStatus.FILLED:
                    await ctx.repo.add_pnl(
                        ctx.mode,
                        gross=outcome.actual_gross,
                        trading_fees=outcome.trading_fees,
                        gas=outcome.gas,
                        net=outcome.actual_net,
                        win=outcome.actual_net > 0,
                    )
                await ctx.repo.audit(
                    ctx.mode, "execution", tr.explanation, {"trade_id": tr.id, "status": tr.status.value}
                )
            if tr.actual_net is not None:
                ctx.daily_pnl_usd += tr.actual_net
        finally:
            tr.completed_at_ms = now_ms()
            ctx.open_trade_ids.discard(tr.id)
        return tr

    async def _settle_paper(self, tr: TradeRecord, fresh: Opportunity) -> None:
        ctx = self.ctx
        chain = fresh.buy.chain
        loan = fresh.buy.quote_amount
        fee = loan * Decimal("0.0005")
        proceeds = fresh.sell.quote_amount
        gas_usd = fresh.gas.expected_gas_cost_usd if fresh.gas else ZERO
        profit = proceeds - loan - fee
        base, quote = fresh.pair.split("/")
        buy_req = OrderRequest(
            venue=fresh.buy.venue,
            symbol=fresh.pair,
            side=OrderSide.BUY,
            amount=fresh.size_base,
            limit_price=fresh.buy.avg_price,
            extra={"quoted_price": str(fresh.buy.avg_price), "flash_loan": True},
        )
        sell_req = OrderRequest(
            venue=fresh.sell.venue,
            symbol=fresh.pair,
            side=OrderSide.SELL,
            amount=fresh.size_base,
            limit_price=fresh.sell.avg_price,
            extra={"quoted_price": str(fresh.sell.avg_price), "flash_loan": True},
        )
        tr.buy = OrderResult(
            request=buy_req,
            order_id=new_id("fl"),
            status=OrderStatus.FILLED,
            filled=fresh.size_base,
            avg_price=fresh.buy.avg_price,
            fee_quote=fee,
            gas_cost_usd=gas_usd,
            tx_hash=f"paper-flash-{tr.id}",
        )
        tr.buy.fills.append(
            Fill(
                order_id=tr.buy.order_id,
                venue=fresh.buy.venue,
                symbol=fresh.pair,
                side=OrderSide.BUY,
                amount=fresh.size_base,
                price=fresh.buy.avg_price,
                fee_amount=fee,
                fee_asset=quote,
                gas_cost_usd=gas_usd,
            )
        )
        tr.sell = OrderResult(
            request=sell_req,
            order_id=new_id("fl"),
            status=OrderStatus.FILLED,
            filled=fresh.size_base,
            avg_price=fresh.sell.avg_price,
            tx_hash=f"paper-flash-{tr.id}",
        )
        tr.sell.fills.append(
            Fill(
                order_id=tr.sell.order_id,
                venue=fresh.sell.venue,
                symbol=fresh.pair,
                side=OrderSide.SELL,
                amount=fresh.size_base,
                price=fresh.sell.avg_price,
                fee_amount=ZERO,
                fee_asset=quote,
            )
        )
        tr.status = TradeStatus.FILLED
        if ctx.ledger is not None and chain is not None:
            await ctx.ledger.adjust(chain.value, quote, profit, allow_negative=True)
            px = ctx.price_usd(chain.native_token)
            if px:
                await ctx.ledger.charge_gas(chain.value, chain.native_token, gas_usd / px)
        tr.log("paper_atomic_settled", loan=loan, fee=fee, proceeds=proceeds, profit=profit, gas_usd=gas_usd)

    async def _broadcast(self, tr: TradeRecord, fresh: Opportunity, cand) -> None:
        from web3 import AsyncHTTPProvider, AsyncWeb3

        chain = fresh.buy.chain
        s = self.ctx.settings
        rpc = self.env.rpc_for(chain.value)
        private_rpc = (
            self.env.evm_private_rpc
            if (s.mev.enabled and s.mev.evm_private_submission and chain is Chain.ETHEREUM)
            else None
        )
        wallet = self.wallets.bot_wallet("evm")
        if wallet is None:
            tr.status = TradeStatus.ABORTED
            tr.explanation = "Aborted: no EVM bot wallet"
            return
        acct = self.wallets.evm_account(wallet.id)
        w3 = AsyncWeb3(AsyncHTTPProvider(rpc))
        contract = w3.eth.contract(
            address=w3.to_checksum_address(self.env.flashloan_contract_for(chain.value)), abi=CONTRACT_ABI
        )
        params = self._build_params(cand, fresh.buy, fresh.sell)
        if params is None:
            tr.status = TradeStatus.ABORTED
            tr.explanation = "Aborted: route cannot be encoded for the contract"
            return
        asset, amount, p = params
        fn = contract.functions.executeArbitrage(asset, amount, p)
        # final simulation immediately before broadcast (abort-before-broadcast on any drift)
        try:
            await fn.call({"from": acct.address})
            gas = await fn.estimate_gas({"from": acct.address})
        except Exception as exc:
            tr.status = TradeStatus.ABORTED
            tr.explanation = f"Aborted before broadcast: final simulation reverted ({str(exc)[:160]})"
            tr.log("abort_pre_broadcast", error=str(exc)[:200])
            return
        fee_hist = await w3.eth.fee_history(5, "latest", [50])
        base_fee = int(fee_hist["baseFeePerGas"][-1])
        priority = min(
            int(D(s.gas.max_priority_fee_gwei) * Decimal(10**9)),
            int(fee_hist["reward"][-1][0]) if fee_hist.get("reward") else int(1e9),
        )
        max_fee = base_fee * 2 + priority
        if Decimal(max_fee) / Decimal(10**9) > s.gas.max_gas_price_gwei:
            tr.status = TradeStatus.ABORTED
            tr.explanation = "Aborted before broadcast: gas price above the configured maximum"
            return
        tx = await fn.build_transaction(
            {
                "from": acct.address,
                "nonce": await w3.eth.get_transaction_count(acct.address),
                "gas": int(gas * 1.15),
                "maxFeePerGas": max_fee,
                "maxPriorityFeePerGas": priority,
                "chainId": await w3.eth.chain_id,
            }
        )
        signed = acct.sign_transaction(tx)
        sender = AsyncWeb3(AsyncHTTPProvider(private_rpc)) if private_rpc else w3
        tx_hash = await sender.eth.send_raw_transaction(signed.raw_transaction)
        tr.log("broadcast", tx=tx_hash.hex(), private=bool(private_rpc))
        receipt = await w3.eth.wait_for_transaction_receipt(tx_hash, timeout=180)
        gas_used = int(receipt["gasUsed"])
        eff = int(receipt.get("effectiveGasPrice", max_fee))
        native_px = self.ctx.price_usd(chain.native_token) or ZERO
        gas_usd = Decimal(gas_used * eff) / Decimal(10**18) * native_px
        base, quote = fresh.pair.split("/")
        buy_req = OrderRequest(
            venue=fresh.buy.venue,
            symbol=fresh.pair,
            side=OrderSide.BUY,
            amount=fresh.size_base,
            limit_price=fresh.buy.avg_price,
            extra={"quoted_price": str(fresh.buy.avg_price), "flash_loan": True},
        )
        sell_req = OrderRequest(
            venue=fresh.sell.venue,
            symbol=fresh.pair,
            side=OrderSide.SELL,
            amount=fresh.size_base,
            limit_price=fresh.sell.avg_price,
            extra={"quoted_price": str(fresh.sell.avg_price), "flash_loan": True},
        )
        if int(receipt["status"]) != 1:
            tr.status = TradeStatus.FAILED
            tr.explanation = f"Transaction reverted on-chain (gas burned {fmt_money(gas_usd)})."
            tr.buy = OrderResult(
                request=buy_req,
                order_id=new_id("fl"),
                status=OrderStatus.FAILED,
                gas_cost_usd=gas_usd,
                tx_hash=tx_hash.hex(),
                error="reverted",
            )
            tr.sell = OrderResult(
                request=sell_req,
                order_id=new_id("fl"),
                status=OrderStatus.FAILED,
                tx_hash=tx_hash.hex(),
                error="reverted",
            )
            self.ctx.failed_trades_last_hour += 1
            return
        # Without parsing the ArbitrageExecuted event we conservatively book the quoted legs; reconciliation adjusts.
        tr.buy = OrderResult(
            request=buy_req,
            order_id=new_id("fl"),
            status=OrderStatus.FILLED,
            filled=fresh.size_base,
            avg_price=fresh.buy.avg_price,
            fee_quote=fresh.buy.quote_amount * Decimal("0.0005"),
            gas_cost_usd=gas_usd,
            tx_hash=tx_hash.hex(),
        )
        tr.sell = OrderResult(
            request=sell_req,
            order_id=new_id("fl"),
            status=OrderStatus.FILLED,
            filled=fresh.size_base,
            avg_price=fresh.sell.avg_price,
            tx_hash=tx_hash.hex(),
        )
        tr.status = TradeStatus.FILLED

    async def _abort(self, tr: TradeRecord, reason: str) -> TradeRecord:
        tr.status = TradeStatus.ABORTED
        tr.explanation = f"Aborted before broadcast because {reason}."
        tr.completed_at_ms = now_ms()
        tr.log("abort", reason=reason)
        if self.ctx.repo:
            await self.ctx.repo.save_trade(tr)
            await self.ctx.repo.audit(self.ctx.mode, "execution", tr.explanation, {"trade_id": tr.id})
        return tr
