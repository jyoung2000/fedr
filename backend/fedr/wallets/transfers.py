"""Deposit detection and explicit withdrawals for bot wallets.

Withdrawals are never automatic: the API requires an explicit confirmation, an allowlisted destination (by
default) and, for recently added addresses, a configurable delay. Every request is recorded in the
``withdrawals`` ledger with an idempotency key so a retried request cannot broadcast twice. Native transfers
and ERC-20 / SPL token transfers are implemented; chains without a configured RPC report the feature as
unavailable rather than pretending.

Verification status: the transaction construction is unit-tested offline; broadcasting has NOT been exercised
against a live or test network in this build environment (no RPC egress).
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from fedr.core.enums import Chain
from fedr.core.logging import get_logger
from fedr.core.models import now_ms
from fedr.core.money import D

log = get_logger("transfers")

CHAIN_IDS = {
    Chain.ETHEREUM: 1,
    Chain.BASE: 8453,
    Chain.ARBITRUM: 42161,
    Chain.OPTIMISM: 10,
    Chain.POLYGON: 137,
    Chain.BSC: 56,
    Chain.AVALANCHE: 43114,
}
ERC20_ABI = [
    {
        "type": "function",
        "name": "transfer",
        "stateMutability": "nonpayable",
        "inputs": [{"name": "to", "type": "address"}, {"name": "amount", "type": "uint256"}],
        "outputs": [{"type": "bool"}],
    },
    {
        "type": "function",
        "name": "decimals",
        "stateMutability": "view",
        "inputs": [],
        "outputs": [{"type": "uint8"}],
    },
    {
        "type": "function",
        "name": "balanceOf",
        "stateMutability": "view",
        "inputs": [{"name": "a", "type": "address"}],
        "outputs": [{"type": "uint256"}],
    },
]
EVM_ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
SOLANA_ADDRESS_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")


def validate_destination(chain: Chain, address: str) -> str | None:
    """Return an error message when the address does not belong to the chain's address format."""
    if chain is Chain.SOLANA:
        if not SOLANA_ADDRESS_RE.match(address):
            return "destination is not a valid Solana address"
        try:
            from solders.pubkey import Pubkey

            Pubkey.from_string(address)
        except Exception:
            return "destination is not a valid Solana public key"
        return None
    if not EVM_ADDRESS_RE.match(address):
        return "destination is not a valid EVM address (0x + 40 hex characters)"
    from eth_utils import is_checksum_address, to_checksum_address

    if address != address.lower() and address != address.upper() and not is_checksum_address(address):
        return f"destination has an invalid EIP-55 checksum (did you mean {to_checksum_address(address)}?)"
    return None


@dataclass(slots=True)
class DepositEvent:
    chain: str
    asset: str
    amount: Decimal
    detected_at_ms: int = field(default_factory=now_ms)
    address: str = ""
    dedupe_key: str = ""


class DepositMonitor:
    """Diffs successive balance snapshots to detect incoming transfers (works for native and token balances).

    Each detected credit gets a deterministic dedupe key (chain, asset, address, balance-after) so a repeated
    observation of the same balance jump cannot be credited twice.
    """

    def __init__(self):
        self._last: dict[tuple[str, str], Decimal] = {}
        self.events: list[DepositEvent] = []
        self._seen: set[str] = set()

    def observe(
        self,
        chain: str,
        balances: dict[str, Decimal],
        expected_deltas: dict[str, Decimal] | None = None,
        address: str = "",
    ) -> list[DepositEvent]:
        out: list[DepositEvent] = []
        expected_deltas = expected_deltas or {}
        for asset, amount in balances.items():
            key = (chain, asset)
            prev = self._last.get(key)
            if prev is not None:
                delta = amount - prev - expected_deltas.get(asset, Decimal(0))
                if delta > Decimal("0.000001"):
                    dk = hashlib.sha256(f"{chain}|{asset}|{address}|{amount}".encode()).hexdigest()[:40]
                    if dk not in self._seen:
                        self._seen.add(dk)
                        ev = DepositEvent(
                            chain=chain, asset=asset, amount=delta, address=address, dedupe_key=dk
                        )
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
    is_token: bool = False


class WithdrawalService:
    """Builds and signs withdrawal transactions with the bot wallet key (server-side only)."""

    def __init__(
        self, wallet_manager, env, price_usd, token_registry: dict[str, dict[str, dict]] | None = None
    ):
        self.wm = wallet_manager
        self.env = env
        self.price_usd = price_usd
        # chain -> asset -> {"address": mint/contract, "decimals": n}
        self.token_registry = token_registry or {}

    def rpc_for(self, chain: Chain) -> str | None:
        return self.env.rpc_for(chain.value)

    def token_info(self, chain: Chain, asset: str) -> dict | None:
        return self.token_registry.get(chain.value, {}).get(asset.upper())

    async def quote(self, chain: Chain, asset: str, amount: Decimal, destination: str) -> WithdrawalQuote:
        err = validate_destination(chain, destination)
        if err:
            return WithdrawalQuote(
                chain.value, asset, amount, destination, Decimal(0), None, amount, False, err
            )
        if amount <= 0:
            return WithdrawalQuote(
                chain.value,
                asset,
                amount,
                destination,
                Decimal(0),
                None,
                amount,
                False,
                "amount must be positive",
            )
        rpc = self.rpc_for(chain)
        if not rpc:
            return WithdrawalQuote(
                chain.value,
                asset,
                amount,
                destination,
                Decimal(0),
                None,
                amount,
                False,
                f"no RPC configured for {chain.value} (set FEDR_RPC_{chain.value.upper()})",
            )
        is_token = asset.upper() != chain.native_token
        if is_token and self.token_info(chain, asset) is None:
            return WithdrawalQuote(
                chain.value,
                asset,
                amount,
                destination,
                Decimal(0),
                None,
                amount,
                False,
                f"{asset} is not a known token on {chain.value} (no contract/mint address in the token registry)",
                True,
            )
        try:
            if chain is Chain.SOLANA:
                fee_native = Decimal("0.000005") + (
                    Decimal("0.00203928") if is_token else Decimal(0)
                )  # base fee + possible ATA rent
            else:
                from web3 import AsyncHTTPProvider, AsyncWeb3

                w3 = AsyncWeb3(AsyncHTTPProvider(rpc))
                gas_price = await w3.eth.gas_price
                gas_limit = 65_000 if is_token else 21_000
                fee_native = D(gas_price) * gas_limit / Decimal(10**18)
        except Exception as exc:
            return WithdrawalQuote(
                chain.value,
                asset,
                amount,
                destination,
                Decimal(0),
                None,
                amount,
                False,
                f"RPC error: {exc}",
                is_token,
            )
        px = self.price_usd(chain.native_token)
        received = (
            amount if is_token else amount
        )  # fee is paid in the native asset; token amount is untouched
        return WithdrawalQuote(
            chain.value,
            asset,
            amount,
            destination,
            fee_native,
            fee_native * px if px else None,
            received,
            True,
            None,
            is_token,
        )

    async def execute(
        self, chain: Chain, wallet_id: str, asset: str, amount: Decimal, destination: str
    ) -> dict[str, Any]:
        err = validate_destination(chain, destination)
        if err:
            raise ValueError(err)
        rpc = self.rpc_for(chain)
        if not rpc:
            raise ValueError(f"no RPC configured for {chain.value}")
        is_token = asset.upper() != chain.native_token
        if chain is Chain.SOLANA:
            return await self._send_solana(rpc, wallet_id, asset, amount, destination, is_token)
        return await self._send_evm(chain, rpc, wallet_id, asset, amount, destination, is_token)

    # ---- EVM ----------------------------------------------------------------------------------
    def build_evm_tx(
        self,
        chain: Chain,
        sender: str,
        asset: str,
        amount: Decimal,
        destination: str,
        nonce: int,
        gas_price: int,
        is_token: bool,
        w3=None,
    ) -> dict:
        """Pure transaction construction (unit-tested offline)."""
        from web3 import Web3

        to = Web3.to_checksum_address(destination)
        if not is_token:
            return {
                "to": to,
                "value": int(amount * Decimal(10**18)),
                "gas": 21_000,
                "gasPrice": gas_price,
                "nonce": nonce,
                "chainId": CHAIN_IDS[chain],
            }
        info = self.token_info(chain, asset)
        if info is None:
            raise ValueError(f"unknown token {asset} on {chain.value}")
        units = int(amount * Decimal(10 ** int(info["decimals"])))
        contract = (w3 or Web3()).eth.contract(
            address=Web3.to_checksum_address(info["address"]), abi=ERC20_ABI
        )
        data = contract.encode_abi(abi_element_identifier="transfer", args=[to, units])
        return {
            "to": Web3.to_checksum_address(info["address"]),
            "value": 0,
            "data": data,
            "gas": 90_000,
            "gasPrice": gas_price,
            "nonce": nonce,
            "chainId": CHAIN_IDS[chain],
            "from": sender,
        }

    async def _send_evm(
        self,
        chain: Chain,
        rpc: str,
        wallet_id: str,
        asset: str,
        amount: Decimal,
        destination: str,
        is_token: bool,
    ) -> dict[str, Any]:
        from web3 import AsyncHTTPProvider, AsyncWeb3

        acct = self.wm.evm_account(wallet_id)
        w3 = AsyncWeb3(AsyncHTTPProvider(rpc))
        chain_id = await w3.eth.chain_id
        if chain_id != CHAIN_IDS[chain]:
            raise ValueError(
                f"RPC chain id {chain_id} does not match {chain.value} ({CHAIN_IDS[chain]}) - refusing to send on the wrong network"
            )
        nonce = await w3.eth.get_transaction_count(acct.address)
        gas_price = await w3.eth.gas_price
        tx = self.build_evm_tx(chain, acct.address, asset, amount, destination, nonce, gas_price, is_token)
        tx.pop("from", None)
        if is_token:
            tx["gas"] = int((await w3.eth.estimate_gas({**tx, "from": acct.address})) * 1.2)
        signed = acct.sign_transaction(tx)
        tx_hash = await w3.eth.send_raw_transaction(signed.raw_transaction)
        return {
            "tx_hash": tx_hash.hex(),
            "chain": chain.value,
            "fee_native": str(D(gas_price) * tx["gas"] / Decimal(10**18)),
        }

    # ---- Solana ---------------------------------------------------------------------------------
    async def _send_solana(
        self, rpc: str, wallet_id: str, asset: str, amount: Decimal, destination: str, is_token: bool
    ) -> dict[str, Any]:
        from solana.rpc.async_api import AsyncClient
        from solders.message import Message
        from solders.pubkey import Pubkey
        from solders.system_program import TransferParams, transfer
        from solders.transaction import Transaction

        kp = self.wm.solana_keypair(wallet_id)
        dest = Pubkey.from_string(destination)
        client = AsyncClient(rpc)
        try:
            if not is_token:
                ix = [
                    transfer(
                        TransferParams(
                            from_pubkey=kp.pubkey(), to_pubkey=dest, lamports=int(amount * Decimal(10**9))
                        )
                    )
                ]
            else:
                from spl.token.constants import TOKEN_PROGRAM_ID
                from spl.token.instructions import (
                    TransferCheckedParams,
                    create_associated_token_account,
                    get_associated_token_address,
                    transfer_checked,
                )

                info = self.token_info(Chain.SOLANA, asset)
                if info is None:
                    raise ValueError(f"unknown SPL token {asset}")
                mint = Pubkey.from_string(info["address"])
                decimals = int(info["decimals"])
                src_ata = get_associated_token_address(kp.pubkey(), mint)
                dst_ata = get_associated_token_address(dest, mint)
                ix = []
                if (await client.get_account_info(dst_ata)).value is None:
                    ix.append(create_associated_token_account(kp.pubkey(), dest, mint))
                ix.append(
                    transfer_checked(
                        TransferCheckedParams(
                            program_id=TOKEN_PROGRAM_ID,
                            source=src_ata,
                            mint=mint,
                            dest=dst_ata,
                            owner=kp.pubkey(),
                            amount=int(amount * Decimal(10**decimals)),
                            decimals=decimals,
                        )
                    )
                )
            bh = (await client.get_latest_blockhash()).value.blockhash
            tx = Transaction([kp], Message(ix, kp.pubkey()), bh)
            sim = await client.simulate_transaction(tx)
            if sim.value.err is not None:
                raise ValueError(f"simulation failed: {sim.value.err}")
            sig = await client.send_transaction(tx)
            return {"tx_hash": str(sig.value), "chain": "solana", "fee_native": "0.000005"}
        finally:
            await client.close()
