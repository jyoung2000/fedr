"""Deposit detection and explicit withdrawals for bot wallets.

Withdrawals are never automatic: the API requires an explicit confirmation and
(by default) an allowlisted destination. Chains without a configured RPC report
the withdrawal feature as unavailable rather than pretending.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from fedr.core.enums import Chain
from fedr.core.logging import get_logger
from fedr.core.models import now_ms
from fedr.core.money import D

log = get_logger("transfers")

CHAIN_IDS = {Chain.ETHEREUM: 1, Chain.BASE: 8453, Chain.ARBITRUM: 42161, Chain.OPTIMISM: 10, Chain.POLYGON: 137, Chain.BSC: 56, Chain.AVALANCHE: 43114}


@dataclass(slots=True)
class DepositEvent:
    chain: str
    asset: str
    amount: Decimal
    detected_at_ms: int = field(default_factory=now_ms)


class DepositMonitor:
    """Diffs successive balance snapshots to detect incoming transfers."""

    def __init__(self):
        self._last: dict[tuple[str, str], Decimal] = {}
        self.events: list[DepositEvent] = []

    def observe(self, chain: str, balances: dict[str, Decimal], expected_deltas: dict[str, Decimal] | None = None) -> list[DepositEvent]:
        out: list[DepositEvent] = []
        expected_deltas = expected_deltas or {}
        for asset, amount in balances.items():
            key = (chain, asset)
            prev = self._last.get(key)
            if prev is not None:
                delta = amount - prev - expected_deltas.get(asset, Decimal(0))
                if delta > Decimal("0.000001"):
                    ev = DepositEvent(chain=chain, asset=asset, amount=delta)
                    out.append(ev)
                    self.events.append(ev)
            self._last[key] = amount
        self.events = self.events[-200:]
        return out


@dataclass(slots=True)
class WithdrawalQuote:
    chain: str
    asset: str
    amount: Decimal
    destination: str
    network_fee_native: Decimal
    network_fee_usd: Decimal | None
    estimated_received: Decimal
    available: bool
    reason: str | None = None


class WithdrawalService:
    """Builds and signs withdrawal transactions using the bot wallet key (server-side)."""

    def __init__(self, wallet_manager, env, price_usd):
        self.wm = wallet_manager
        self.env = env
        self.price_usd = price_usd

    def rpc_for(self, chain: Chain) -> str | None:
        return self.env.rpc_for(chain.value)

    async def quote(self, chain: Chain, asset: str, amount: Decimal, destination: str) -> WithdrawalQuote:
        rpc = self.rpc_for(chain)
        if not rpc:
            return WithdrawalQuote(chain.value, asset, amount, destination, Decimal(0), None, amount, False, f"no RPC configured for {chain.value} (set FEDR_RPC_{chain.value.upper()})")
        if asset != chain.native_token:
            return WithdrawalQuote(chain.value, asset, amount, destination, Decimal(0), None, amount, False, "token withdrawals are not implemented in this build - withdraw native assets or use the exchange/DEX to convert first")
        if chain is Chain.SOLANA:
            fee_native = Decimal("0.000005")
        else:
            try:
                from web3 import AsyncHTTPProvider, AsyncWeb3

                w3 = AsyncWeb3(AsyncHTTPProvider(rpc))
                gas_price = await w3.eth.gas_price
                fee_native = D(gas_price) * 21000 / Decimal(10**18)
            except Exception as exc:
                return WithdrawalQuote(chain.value, asset, amount, destination, Decimal(0), None, amount, False, f"RPC error: {exc}")
        px = self.price_usd(chain.native_token)
        return WithdrawalQuote(chain.value, asset, amount, destination, fee_native, fee_native * px if px else None, amount, True)

    async def execute(self, chain: Chain, wallet_id: str, amount: Decimal, destination: str) -> dict[str, Any]:
        rpc = self.rpc_for(chain)
        if not rpc:
            raise ValueError(f"no RPC configured for {chain.value}")
        if chain is Chain.SOLANA:
            from solana.rpc.async_api import AsyncClient
            from solders.message import Message
            from solders.pubkey import Pubkey
            from solders.system_program import TransferParams, transfer
            from solders.transaction import Transaction

            kp = self.wm.solana_keypair(wallet_id)
            client = AsyncClient(rpc)
            try:
                bh = (await client.get_latest_blockhash()).value.blockhash
                ix = transfer(TransferParams(from_pubkey=kp.pubkey(), to_pubkey=Pubkey.from_string(destination), lamports=int(amount * Decimal(10**9))))
                tx = Transaction([kp], Message([ix], kp.pubkey()), bh)
                sig = await client.send_transaction(tx)
                return {"tx_hash": str(sig.value), "chain": chain.value}
            finally:
                await client.close()
        from web3 import AsyncHTTPProvider, AsyncWeb3

        acct = self.wm.evm_account(wallet_id)
        w3 = AsyncWeb3(AsyncHTTPProvider(rpc))
        nonce = await w3.eth.get_transaction_count(acct.address)
        gas_price = await w3.eth.gas_price
        tx = {"to": w3.to_checksum_address(destination), "value": int(amount * Decimal(10**18)), "gas": 21000, "gasPrice": gas_price, "nonce": nonce, "chainId": CHAIN_IDS[chain]}
        signed = acct.sign_transaction(tx)
        tx_hash = await w3.eth.send_raw_transaction(signed.raw_transaction)
        return {"tx_hash": tx_hash.hex(), "chain": chain.value}
