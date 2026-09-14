"""Bot trading wallets (EVM + Solana) with encrypted key storage.

* Keys are generated or imported server-side, encrypted with the master key
  (AES-256-GCM) and stored in the database. They are never returned by the API,
  never logged, and never sent to third parties. Gateway (a local service in
  the same compose network) receives the key once so it can sign DEX swaps.
* Backups are exported as passphrase-encrypted keystores (EVM: Web3 Secret
  Storage v3 via eth_account; Solana: scrypt + AES-GCM JSON) - ciphertext only.
"""

from __future__ import annotations

import base64
import json
import secrets
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
from eth_account import Account
from solders.keypair import Keypair

from fedr.core.enums import Chain
from fedr.core.models import new_id
from fedr.core.money import D
from fedr.db.models import Wallet
from fedr.db.repo import Repo
from fedr.security.crypto import SecretBox

EVM_CHAINS = [c for c in Chain if c.is_evm]


@dataclass(slots=True)
class WalletInfo:
    id: str
    family: str  # evm | solana
    address: str
    label: str
    kind: str  # bot | external
    provider: str | None
    backed_up: bool
    mode: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "family": self.family,
            "address": self.address,
            "label": self.label,
            "kind": self.kind,
            "provider": self.provider,
            "backed_up": self.backed_up,
            "mode": self.mode,
        }


class WalletManager:
    def __init__(self, repo: Repo | None, box: SecretBox):
        self.repo = repo
        self.box = box
        self._wallets: dict[str, Wallet] = {}

    async def load(self) -> None:
        if self.repo is None:
            return
        for w in await self.repo.list_wallets():
            self._wallets[w.id] = w

    # ---- queries ------------------------------------------------------------------
    def list(self) -> list[WalletInfo]:
        return [self._info(w) for w in self._wallets.values()]

    def bot_wallet(self, family: str, mode: str = "live") -> WalletInfo | None:
        for w in self._wallets.values():
            if w.kind == "bot" and w.chain == family and w.mode == mode:
                return self._info(w)
        for w in self._wallets.values():  # fall back to any bot wallet of that family
            if w.kind == "bot" and w.chain == family:
                return self._info(w)
        return None

    def address_for_chain(self, chain: Chain, mode: str = "live") -> str | None:
        w = self.bot_wallet("solana" if chain is Chain.SOLANA else "evm", mode)
        return w.address if w else None

    @staticmethod
    def _info(w: Wallet) -> WalletInfo:
        return WalletInfo(
            id=w.id,
            family=w.chain,
            address=w.address,
            label=w.label,
            kind=w.kind,
            provider=w.provider,
            backed_up=w.backed_up,
            mode=w.mode,
        )

    # ---- creation / import --------------------------------------------------------------
    async def create_bot_wallet(
        self, family: str, label: str | None = None, mode: str = "live"
    ) -> WalletInfo:
        if family == "evm":
            acct = Account.create(secrets.token_bytes(32).hex())
            address, secret = acct.address, acct.key.hex()
        elif family == "solana":
            kp = Keypair()
            address, secret = str(kp.pubkey()), str(kp)  # base58 secret key (64 bytes)
        else:
            raise ValueError("family must be evm or solana")
        return await self._store(family, address, secret, label or f"Bot {family.upper()} wallet", mode)

    async def import_bot_wallet(
        self, family: str, private_key: str, label: str | None = None, mode: str = "live"
    ) -> WalletInfo:
        private_key = private_key.strip()
        if family == "evm":
            acct = Account.from_key(private_key)
            address, secret = acct.address, acct.key.hex()
        elif family == "solana":
            kp = self._solana_keypair_from_secret(private_key)
            address, secret = str(kp.pubkey()), str(kp)
        else:
            raise ValueError("family must be evm or solana")
        return await self._store(family, address, secret, label or f"Imported {family.upper()} wallet", mode)

    @staticmethod
    def _solana_keypair_from_secret(secret: str) -> Keypair:
        s = secret.strip()
        if s.startswith("["):
            return Keypair.from_bytes(bytes(json.loads(s)))
        return Keypair.from_base58_string(s)

    async def _store(self, family: str, address: str, secret: str, label: str, mode: str) -> WalletInfo:
        wid = new_id("wal")
        w = Wallet(
            id=wid,
            chain=family,
            address=address,
            label=label,
            kind="bot",
            provider=None,
            key_enc=self.box.encrypt(secret, aad=wid),
            backed_up=False,
            mode=mode,
        )
        self._wallets[wid] = w
        if self.repo is not None:
            await self.repo.upsert_wallet(w)
        return self._info(w)

    async def add_external_wallet(
        self, family: str, address: str, provider: str, label: str | None = None
    ) -> WalletInfo:
        for w in self._wallets.values():
            if w.kind == "external" and w.address.lower() == address.lower():
                return self._info(w)
        wid = new_id("ext")
        w = Wallet(
            id=wid,
            chain=family,
            address=address,
            label=label or f"{provider} wallet",
            kind="external",
            provider=provider,
            key_enc=None,
            backed_up=True,
        )
        self._wallets[wid] = w
        if self.repo is not None:
            await self.repo.upsert_wallet(w)
        return self._info(w)

    async def remove(self, wallet_id: str) -> None:
        self._wallets.pop(wallet_id, None)
        if self.repo is not None:
            await self.repo.delete_wallet(wallet_id)

    async def confirm_backup(self, wallet_id: str) -> None:
        w = self._wallets[wallet_id]
        w.backed_up = True
        if self.repo is not None:
            from fedr.db.models import utcnow

            await self.repo.update_wallet(wallet_id, backed_up=True, backup_confirmed_at=utcnow())

    # ---- secret access (server-side only) --------------------------------------------------
    def _secret(self, wallet_id: str) -> str:
        w = self._wallets[wallet_id]
        if not w.key_enc:
            raise ValueError("external wallets have no key material")
        return self.box.decrypt_str(w.key_enc, aad=wallet_id)

    def signer_secret_for_gateway(self, wallet_id: str) -> str:
        """Used once to register the wallet with the local Gateway service."""
        return self._secret(wallet_id)

    def evm_account(self, wallet_id: str):
        return Account.from_key(self._secret(wallet_id))

    def solana_keypair(self, wallet_id: str) -> Keypair:
        return self._solana_keypair_from_secret(self._secret(wallet_id))

    # ---- encrypted backup export --------------------------------------------------------
    def export_keystore(self, wallet_id: str, passphrase: str) -> dict[str, Any]:
        if len(passphrase) < 12:
            raise ValueError("backup passphrase must be at least 12 characters")
        w = self._wallets[wallet_id]
        secret = self._secret(wallet_id)
        if w.chain == "evm":
            ks = Account.encrypt(secret, passphrase)
            ks["fedr"] = {"family": "evm", "address": w.address, "label": w.label}
            return ks
        salt = secrets.token_bytes(16)
        key = Scrypt(salt=salt, length=32, n=2**17, r=8, p=1).derive(passphrase.encode())
        nonce = secrets.token_bytes(12)
        ct = AESGCM(key).encrypt(nonce, secret.encode(), b"fedr-solana-keystore")
        return {
            "version": 1,
            "family": "solana",
            "address": w.address,
            "label": w.label,
            "kdf": "scrypt",
            "kdfparams": {"n": 2**17, "r": 8, "p": 1, "dklen": 32, "salt": base64.b64encode(salt).decode()},
            "cipher": "aes-256-gcm",
            "nonce": base64.b64encode(nonce).decode(),
            "ciphertext": base64.b64encode(ct).decode(),
        }

    @staticmethod
    def decrypt_solana_keystore(ks: dict[str, Any], passphrase: str) -> str:
        p = ks["kdfparams"]
        key = Scrypt(
            salt=base64.b64decode(p["salt"]),
            length=int(p["dklen"]),
            n=int(p["n"]),
            r=int(p["r"]),
            p=int(p["p"]),
        ).derive(passphrase.encode())
        return (
            AESGCM(key)
            .decrypt(
                base64.b64decode(ks["nonce"]), base64.b64decode(ks["ciphertext"]), b"fedr-solana-keystore"
            )
            .decode()
        )


def deposit_qr_svg(payload: str) -> str:
    """Render a QR code as an SVG string (server-side, no external service)."""
    import io

    import qrcode
    import qrcode.image.svg

    img = qrcode.make(payload, image_factory=qrcode.image.svg.SvgPathImage, box_size=8, border=2)
    buf = io.BytesIO()
    img.save(buf)
    return buf.getvalue().decode()


MIN_RECOMMENDED_DEPOSIT_USD: dict[str, Decimal] = {
    "solana": D("25"),
    "ethereum": D("250"),
    "base": D("25"),
    "arbitrum": D("25"),
    "optimism": D("25"),
    "polygon": D("25"),
    "bsc": D("25"),
    "avalanche": D("25"),
}
NETWORK_WARNINGS: dict[str, str] = {
    "solana": "Send only Solana (SPL) assets to this address. Sending from another network will lose funds.",
    "ethereum": "Send only on Ethereum mainnet. Gas on Ethereum is expensive; keep enough ETH for gas.",
    "base": "Send only on the Base network (chain id 8453). Assets sent on Ethereum mainnet will not arrive here.",
    "arbitrum": "Send only on Arbitrum One (chain id 42161).",
    "optimism": "Send only on OP Mainnet (chain id 10).",
    "polygon": "Send only on Polygon PoS (chain id 137). Native gas token is POL.",
    "bsc": "Send only on BNB Smart Chain (chain id 56). Native gas token is BNB.",
    "avalanche": "Send only on Avalanche C-Chain (chain id 43114).",
}
