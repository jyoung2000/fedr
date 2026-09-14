"""Encrypted keystore import (restore) - the counterpart of WalletManager.export_keystore.

Accepts:
* EVM: Web3 Secret Storage v3 JSON (what `export_keystore` produces via eth_account, also MetaMask/geth exports)
* Solana: FEDR keystore v1 (scrypt + AES-256-GCM) produced by `export_keystore`
Integrity is verified by the AES-GCM tag / keystore MAC; a wrong passphrase or tampered file fails closed.
"""

from __future__ import annotations

from typing import Any

from eth_account import Account

from fedr.wallets.manager import WalletManager


class KeystoreError(ValueError):
    pass


def decrypt_keystore(ks: dict[str, Any], passphrase: str) -> tuple[str, str, str | None]:
    """Return (family, private_key, address_hint)."""
    if not isinstance(ks, dict):
        raise KeystoreError("keystore must be a JSON object")
    if ks.get("family") == "solana" and ks.get("cipher") == "aes-256-gcm":
        try:
            secret = WalletManager.decrypt_solana_keystore(ks, passphrase)
        except Exception as exc:
            raise KeystoreError("wrong passphrase or corrupted Solana keystore") from exc
        return "solana", secret, ks.get("address")
    if "crypto" in ks or "Crypto" in ks:
        try:
            key = Account.decrypt(ks, passphrase)
        except ValueError as exc:
            raise KeystoreError("wrong passphrase or corrupted EVM keystore") from exc
        addr = ks.get("address")
        return "evm", key.hex(), ("0x" + addr if addr and not addr.startswith("0x") else addr)
    raise KeystoreError("unrecognised keystore format (expected EVM v3 JSON or FEDR Solana keystore)")


async def restore_wallet(
    wm: WalletManager, ks: dict[str, Any], passphrase: str, label: str | None = None, mode: str = "live"
):
    family, secret, hint = decrypt_keystore(ks, passphrase)
    info = await wm.import_bot_wallet(
        family,
        secret,
        label or ks.get("fedr", {}).get("label") or ks.get("label") or f"Restored {family.upper()} wallet",
        mode,
    )
    if hint and info.address.lower() != str(hint).lower():
        await wm.remove(info.id)
        raise KeystoreError(f"keystore address {hint} does not match the derived address {info.address}")
    await wm.confirm_backup(info.id)  # a restored wallet is, by construction, backed up
    return wm._info(wm._wallets[info.id])
