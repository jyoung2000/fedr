"""Curated CEX registry.

Every entry maps to a real CCXT 4.5.x exchange id. Nothing here is fabricated:
``sandbox`` reflects ``urls['test']`` availability in ccxt 4.5.78 and
``fallback_taker_pct`` is the documented default from the exchange class. No
venue is marked as live-verified because this build environment cannot reach
exchange APIs; the connector test matrix (docs/CONNECTORS.md) records what was
actually exercised.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from fedr.core.enums import VerificationLevel


@dataclass(frozen=True, slots=True)
class ExchangeSpec:
    id: str  # ccxt id
    display_name: str
    sandbox: bool  # ccxt urls['test'] exists
    sandbox_note: str = ""
    perps: bool = False  # linear perpetuals via the same id (or a sibling id)
    perp_id: str | None = None  # sibling ccxt id for perps when different (e.g. binanceusdm)
    funding_rates: bool = False
    fallback_taker_pct: Decimal | None = None  # documented default, used only with source="documented_fallback"
    fallback_maker_pct: Decimal | None = None
    ws: bool = True
    needs_password: bool = False
    auth_style: str = "api_key"  # api_key | wallet_key (hyperliquid)
    notes: str = ""
    verification: VerificationLevel = VerificationLevel.NOT_VERIFIED_LIVE
    quote_assets: tuple[str, ...] = ("USDT", "USDC", "USD")
    tier: int = 1  # 1 = designed-for, 2 = supported via ccxt
    orderbook_limits: tuple[int, ...] = field(default=())


EXCHANGES: dict[str, ExchangeSpec] = {
    e.id: e
    for e in [
        ExchangeSpec("binance", "Binance", True, "testnet.binance.vision (spot)", perps=True, perp_id="binanceusdm", funding_rates=True, fallback_taker_pct=Decimal("0.10"), fallback_maker_pct=Decimal("0.10"), orderbook_limits=(5, 10, 20, 50, 100, 500, 1000, 5000)),
        ExchangeSpec("binanceus", "Binance US", True, "inherits Binance testnet URLs (unverified for US keys)", fallback_taker_pct=Decimal("0.10"), fallback_maker_pct=Decimal("0.10"), quote_assets=("USD", "USDT", "USDC")),
        ExchangeSpec("coinbase", "Coinbase (Advanced Trade)", False, "no sandbox", fallback_taker_pct=Decimal("1.20"), fallback_maker_pct=Decimal("0.60"), quote_assets=("USD", "USDC", "USDT"), notes="market buys require price; fees are tier based - fetched from API when authenticated"),
        ExchangeSpec("coinbaseexchange", "Coinbase Exchange", True, "api-public.sandbox.exchange.coinbase.com", needs_password=True, fallback_taker_pct=Decimal("0.60"), fallback_maker_pct=Decimal("0.40"), quote_assets=("USD", "USDC", "USDT")),
        ExchangeSpec("kraken", "Kraken", False, "no spot sandbox (Kraken Futures demo exists)", fallback_taker_pct=Decimal("0.26"), fallback_maker_pct=Decimal("0.16"), quote_assets=("USD", "USDC", "USDT", "EUR"), orderbook_limits=(10, 25, 100, 500)),
        ExchangeSpec("krakenfutures", "Kraken Futures", True, "demo-futures.kraken.com", perps=True, funding_rates=True, fallback_taker_pct=Decimal("0.05"), fallback_maker_pct=Decimal("0.02"), quote_assets=("USD",)),
        ExchangeSpec("okx", "OKX", True, "demo trading keys + x-simulated-trading header", perps=True, funding_rates=True, needs_password=True, fallback_taker_pct=Decimal("0.15"), fallback_maker_pct=Decimal("0.10")),
        ExchangeSpec("bybit", "Bybit", True, "api-testnet.bybit.com", perps=True, funding_rates=True, fallback_taker_pct=Decimal("0.075"), fallback_maker_pct=Decimal("0.01"), orderbook_limits=(1, 50, 200, 1000)),
        ExchangeSpec("kucoin", "KuCoin", False, "no sandbox", perps=True, perp_id="kucoinfutures", funding_rates=True, needs_password=True, fallback_taker_pct=Decimal("0.10"), fallback_maker_pct=Decimal("0.10"), orderbook_limits=(20, 100)),
        ExchangeSpec("gate", "Gate", True, "api-testnet.gateapi.io (REST only, no WS)", perps=True, funding_rates=True, fallback_taker_pct=Decimal("0.20"), fallback_maker_pct=Decimal("0.20")),
        ExchangeSpec("bitget", "Bitget", False, "no sandbox", perps=True, funding_rates=True, needs_password=True, fallback_taker_pct=Decimal("0.20"), fallback_maker_pct=Decimal("0.20")),
        ExchangeSpec("mexc", "MEXC", False, "no sandbox", perps=True, fallback_taker_pct=Decimal("0.20"), fallback_maker_pct=Decimal("0.20")),
        ExchangeSpec("hyperliquid", "Hyperliquid", True, "api.hyperliquid-testnet.xyz", perps=True, funding_rates=True, auth_style="wallet_key", fallback_taker_pct=Decimal("0.07"), fallback_maker_pct=Decimal("0.04"), quote_assets=("USDC",), notes="authenticates with an EVM private key (walletAddress + privateKey); market orders are IOC limits with slippage"),
        ExchangeSpec("htx", "HTX", False, "no sandbox", perps=True, funding_rates=True, fallback_taker_pct=Decimal("0.20"), fallback_maker_pct=Decimal("0.20"), tier=2, orderbook_limits=(5, 10, 20, 150)),
        ExchangeSpec("cryptocom", "Crypto.com", True, "uat-api.3ona.co (REST only)", perps=True, fallback_taker_pct=Decimal("0.50"), fallback_maker_pct=Decimal("0.25"), tier=2),
        ExchangeSpec("bitfinex", "Bitfinex", False, "no sandbox", fallback_taker_pct=Decimal("0.20"), fallback_maker_pct=Decimal("0.10"), tier=2, quote_assets=("USD", "UST", "USDT")),
        ExchangeSpec("bitstamp", "Bitstamp", False, "no sandbox", fallback_taker_pct=Decimal("0.40"), fallback_maker_pct=Decimal("0.40"), tier=2, quote_assets=("USD", "USDC", "USDT", "EUR")),
        ExchangeSpec("woo", "WOO X", True, "api.staging.woox.io", perps=True, funding_rates=True, fallback_taker_pct=Decimal("0.05"), fallback_maker_pct=Decimal("0.02"), tier=2),
        ExchangeSpec("bingx", "BingX", True, "open-api-vst.bingx.com", perps=True, funding_rates=True, fallback_taker_pct=Decimal("0.10"), fallback_maker_pct=Decimal("0.10"), tier=2),
        ExchangeSpec("phemex", "Phemex", True, "testnet-api.phemex.com", perps=True, fallback_taker_pct=Decimal("0.10"), fallback_maker_pct=Decimal("0.10"), tier=2),
        ExchangeSpec("deribit", "Deribit", True, "test.deribit.com", perps=True, funding_rates=True, tier=2, quote_assets=("USDC", "USD"), notes="perp amounts are in USD; per-instrument fees from API"),
        ExchangeSpec("bitmex", "BitMEX", True, "testnet.bitmex.com", perps=True, funding_rates=True, tier=2, quote_assets=("USDT", "USD"), notes="per-instrument fees from API"),
    ]
}


def spec(exchange_id: str) -> ExchangeSpec | None:
    return EXCHANGES.get(exchange_id)


def list_specs(tier: int | None = None) -> list[ExchangeSpec]:
    return [e for e in EXCHANGES.values() if tier is None or e.tier == tier]
