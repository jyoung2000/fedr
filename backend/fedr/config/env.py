"""Process-level configuration from environment variables / .env.

Only *infrastructure* settings live here (ports, paths, keys, endpoints).
Everything a user changes in the UI is an ``AppSettings`` document persisted in
the database (see ``fedr.config.schema``).
"""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class EnvSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="FEDR_", env_file=".env", extra="ignore")

    port: int = 8935
    bind: str = "0.0.0.0"  # inside Docker; compose publishes 127.0.0.1:8935 by default
    data_dir: Path = Path("/data")
    database_url: str | None = None  # default: sqlite+aiosqlite:///<data_dir>/database/fedr.db
    log_level: str = "INFO"
    log_json: bool = False

    # Security
    master_key: str | None = None  # base64 32-byte key; generated into data_dir/config if absent
    auth_token: str | None = None  # if set, API/UI require it (Bearer or cookie)
    allowed_origins: str = ""  # comma separated extra CORS origins (same-origin always allowed)
    secure_cookies: bool = False

    # Live trading hard gate: an *additional* precondition. Setting it to true
    # never activates live trading by itself - the UI workflow is still required.
    live_trading_allowed: bool = False

    # Default mode on first boot. Live can never be the boot default.
    default_mode: str = "paper"

    # Gateway (DEX middleware)
    gateway_url: str = "http://gateway:15888"
    gateway_api_key: str | None = None  # bearer token when Gateway runs with GATEWAY_REQUIRE_AUTH=true
    gateway_enabled: bool = True
    gateway_timeout_s: float = 8.0

    # RPC endpoints (optional; Gateway has its own). Used for gas oracles, deposits and flash loans.
    rpc_ethereum: str | None = None
    rpc_base: str | None = None
    rpc_arbitrum: str | None = None
    rpc_optimism: str | None = None
    rpc_polygon: str | None = None
    rpc_bsc: str | None = None
    rpc_avalanche: str | None = None
    rpc_solana: str | None = None
    rpc_sepolia: str | None = None
    rpc_base_sepolia: str | None = None
    rpc_solana_devnet: str | None = None

    # MEV / private submission
    evm_private_rpc: str | None = None  # e.g. https://rpc.flashbots.net (opt-in, documented in docs/MEV.md)
    jito_block_engine_url: str | None = None

    # Flash loan contract deployments (never auto-deployed)
    flashloan_contract_ethereum: str | None = None
    flashloan_contract_arbitrum: str | None = None
    flashloan_contract_base: str | None = None
    flashloan_contract_optimism: str | None = None
    flashloan_contract_polygon: str | None = None
    flashloan_contract_sepolia: str | None = None

    telemetry_enabled: bool = False  # local-first: nothing leaves the box unless opted-in

    demo_seed: int | None = Field(default=None, description="Deterministic seed for SIMULATION mode data")

    @property
    def database_url_resolved(self) -> str:
        if self.database_url:
            return self.database_url
        return f"sqlite+aiosqlite:///{self.data_dir / 'database' / 'fedr.db'}"

    def rpc_for(self, chain: str) -> str | None:
        return getattr(self, f"rpc_{chain}", None)

    def flashloan_contract_for(self, chain: str) -> str | None:
        return getattr(self, f"flashloan_contract_{chain}", None)

    def ensure_dirs(self) -> None:
        for sub in (
            "database",
            "config",
            "wallets",
            "strategies",
            "logs",
            "backups",
            "market-data",
            "reports",
        ):
            (self.data_dir / sub).mkdir(parents=True, exist_ok=True)


_env: EnvSettings | None = None


def get_env() -> EnvSettings:
    global _env
    if _env is None:
        _env = EnvSettings()
    return _env


def reset_env_for_tests(**overrides) -> EnvSettings:
    global _env
    _env = EnvSettings(**overrides)
    return _env
