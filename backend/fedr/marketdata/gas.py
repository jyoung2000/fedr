"""Gas oracle: per-chain fee conditions from Gateway (or synthetic data in SIMULATION)."""
from __future__ import annotations

from decimal import Decimal
from typing import Callable

from fedr.connectors.dex.gateway_client import GatewayClient, GatewayError
from fedr.connectors.dex.registry import CHAIN_NETWORKS, gateway_chain_for
from fedr.core.enums import Chain
from fedr.core.logging import get_logger
from fedr.core.models import now_ms
from fedr.core.money import D
from fedr.engine.gas_guard import GasBaseline, GasSnapshot

log = get_logger("gas")


class GasOracle:
    def __init__(self, price_usd: Callable[[str], Decimal | None], gateway: GatewayClient | None = None, synthetic=None, testnet: bool = False):
        self.price_usd = price_usd
        self.gateway = gateway
        self.synthetic = synthetic
        self.testnet = testnet
        self.baseline = GasBaseline()
        self._snapshots: dict[Chain, GasSnapshot] = {}
        self.last_error: dict[Chain, str] = {}

    def snapshot(self, chain: Chain, max_age_ms: int = 60_000) -> GasSnapshot | None:
        s = self._snapshots.get(chain)
        if s and s.age_ms <= max_age_ms:
            return s
        return None

    def all(self) -> dict[str, dict]:
        out = {}
        for chain, s in self._snapshots.items():
            out[chain.value] = {"gas_price": str(s.gas_price_native), "priority": str(s.priority_fee_native), "native_usd": str(s.native_usd), "age_ms": s.age_ms, "source": s.source, "baseline": str(self.baseline.get(chain) or "")}
        return out

    async def refresh(self, chain: Chain) -> GasSnapshot | None:
        native_px = self.price_usd(chain.native_token)
        if self.synthetic is not None and chain in self.synthetic.chains:
            g = self.synthetic.gas(chain)
            snap = GasSnapshot(chain=chain, gas_price_native=g["gas_price"], priority_fee_native=g["priority"], native_usd=g["native_usd"], source="synthetic")
            if self.baseline.get(chain) is None:
                self.baseline.seed(chain, g["baseline"])
            self._snapshots[chain] = snap
            self.baseline.update(chain, snap.gas_price_native)
            return snap
        if self.gateway is None:
            return None
        if native_px is None:
            self.last_error[chain] = f"no USD price for {chain.native_token}"
            return None
        nets = CHAIN_NETWORKS.get(chain, {})
        network = nets.get("testnet") if self.testnet else nets.get("mainnet")
        if not network:
            return None
        try:
            g = await self.gateway.estimate_gas(gateway_chain_for(chain), network)
        except GatewayError as exc:
            self.last_error[chain] = str(exc)
            log.warning("gas estimate failed", chain=chain.value, error=str(exc)[:200])
            return None
        if chain is Chain.SOLANA:
            snap = GasSnapshot(chain=chain, gas_price_native=g.fee_per_compute_unit, priority_fee_native=g.fee_per_compute_unit, native_usd=native_px, source="gateway")
        else:
            snap = GasSnapshot(chain=chain, gas_price_native=g.fee_per_compute_unit, priority_fee_native=g.max_priority_fee or D("0.001"), native_usd=native_px, source="gateway")
        self._snapshots[chain] = snap
        self.baseline.update(chain, snap.gas_price_native)
        self.last_error.pop(chain, None)
        return snap
