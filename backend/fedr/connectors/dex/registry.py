"""DEX / chain registry (Gateway 2.16 connector matrix).

Only connectors that exist in Gateway are listed; nothing is fabricated. The
``verification`` field stays NOT_VERIFIED_LIVE until a real quote/swap has been
exercised from a deployment with network access.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from fedr.core.enums import Chain, VerificationLevel


@dataclass(frozen=True, slots=True)
class DexSpec:
    id: str  # fedr venue name, e.g. "jupiter", "uniswap-base"
    connector: str  # gateway connector name
    trading_type: str  # router | amm | clmm
    chain: Chain
    gateway_chain: str  # ethereum | solana
    network: str  # gateway network id
    display_name: str
    typical_fee_pct: Decimal | None  # informational; executable quotes are authoritative
    gas_limit: int  # gateway's fixed gas limit for this connector/chain
    testnet_network: str | None = None
    maintained: bool = True
    notes: str = ""
    verification: VerificationLevel = VerificationLevel.NOT_VERIFIED_LIVE


DEXES: dict[str, DexSpec] = {
    d.id: d
    for d in [
        DexSpec("jupiter", "jupiter", "router", Chain.SOLANA, "solana", "mainnet-beta", "Jupiter (Solana)", None, 200_000, testnet_network="devnet", notes="aggregator; fee embedded in route output; Jupiter API key recommended (lite API deprecated)"),
        DexSpec("raydium", "raydium", "amm", Chain.SOLANA, "solana", "mainnet-beta", "Raydium (Solana)", Decimal("0.25"), 200_000, testnet_network="devnet", notes="pool address required from Gateway pool list"),
        DexSpec("meteora", "meteora", "clmm", Chain.SOLANA, "solana", "mainnet-beta", "Meteora DLMM (Solana)", None, 200_000, testnet_network="devnet"),
        DexSpec("orca", "orca", "clmm", Chain.SOLANA, "solana", "mainnet-beta", "Orca Whirlpools (Solana)", None, 200_000, testnet_network="devnet"),
        DexSpec("uniswap", "uniswap", "router", Chain.ETHEREUM, "ethereum", "mainnet", "Uniswap (Ethereum)", None, 500_000, notes="Universal Router via Permit2; approvals needed once per token"),
        DexSpec("uniswap-base", "uniswap", "router", Chain.BASE, "ethereum", "base", "Uniswap (Base)", None, 500_000),
        DexSpec("uniswap-arbitrum", "uniswap", "router", Chain.ARBITRUM, "ethereum", "arbitrum", "Uniswap (Arbitrum)", None, 500_000),
        DexSpec("uniswap-optimism", "uniswap", "router", Chain.OPTIMISM, "ethereum", "optimism", "Uniswap (Optimism)", None, 500_000),
        DexSpec("uniswap-polygon", "uniswap", "router", Chain.POLYGON, "ethereum", "polygon", "Uniswap (Polygon)", None, 500_000),
        DexSpec("uniswap-bsc", "uniswap", "router", Chain.BSC, "ethereum", "bsc", "Uniswap (BSC)", None, 500_000),
        DexSpec("uniswap-avalanche", "uniswap", "router", Chain.AVALANCHE, "ethereum", "avalanche", "Uniswap (Avalanche)", None, 500_000),
        DexSpec("pancakeswap-bsc", "pancakeswap", "router", Chain.BSC, "ethereum", "bsc", "PancakeSwap (BSC)", None, 300_000),
        DexSpec("pancakeswap-base", "pancakeswap", "router", Chain.BASE, "ethereum", "base", "PancakeSwap (Base)", None, 300_000),
        DexSpec("pancakeswap-arbitrum", "pancakeswap", "router", Chain.ARBITRUM, "ethereum", "arbitrum", "PancakeSwap (Arbitrum)", None, 300_000),
        DexSpec("0x", "0x", "router", Chain.ETHEREUM, "ethereum", "mainnet", "0x (Ethereum)", None, 300_000, notes="requires 0x API key in Gateway"),
        DexSpec("0x-base", "0x", "router", Chain.BASE, "ethereum", "base", "0x (Base)", None, 300_000, notes="requires 0x API key in Gateway"),
        DexSpec("0x-arbitrum", "0x", "router", Chain.ARBITRUM, "ethereum", "arbitrum", "0x (Arbitrum)", None, 300_000, notes="requires 0x API key in Gateway"),
        DexSpec("0x-optimism", "0x", "router", Chain.OPTIMISM, "ethereum", "optimism", "0x (Optimism)", None, 300_000, notes="requires 0x API key in Gateway"),
        DexSpec("0x-polygon", "0x", "router", Chain.POLYGON, "ethereum", "polygon", "0x (Polygon)", None, 300_000, notes="requires 0x API key in Gateway"),
    ]
}

CHAIN_NETWORKS: dict[Chain, dict[str, str]] = {
    Chain.SOLANA: {"mainnet": "mainnet-beta", "testnet": "devnet"},
    Chain.ETHEREUM: {"mainnet": "mainnet", "testnet": "sepolia"},
    Chain.BASE: {"mainnet": "base"},
    Chain.ARBITRUM: {"mainnet": "arbitrum"},
    Chain.OPTIMISM: {"mainnet": "optimism"},
    Chain.POLYGON: {"mainnet": "polygon"},
    Chain.BSC: {"mainnet": "bsc"},
    Chain.AVALANCHE: {"mainnet": "avalanche"},
}

# Default venues enabled for CEX<->DEX scanning (others can be enabled in Settings > Exchanges)
DEFAULT_DEXES = ["jupiter", "uniswap-base", "uniswap-arbitrum"]


def dex_spec(name: str) -> DexSpec | None:
    return DEXES.get(name)


def gateway_chain_for(chain: Chain) -> str:
    return "solana" if chain is Chain.SOLANA else "ethereum"
