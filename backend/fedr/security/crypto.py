"""Encryption at rest for exchange credentials and wallet key material.

AES-256-GCM with a per-installation master key. The master key comes from the
``FEDR_MASTER_KEY`` environment variable or is generated once into
``<data_dir>/config/master.key`` (mode 0600). Ciphertext is versioned so the
scheme can be rotated later.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_VERSION = b"v1"


class SecretBox:
    def __init__(self, key: bytes):
        if len(key) != 32:
            raise ValueError("master key must be exactly 32 bytes")
        self._aead = AESGCM(key)
        self._fingerprint = hashlib.sha256(key).hexdigest()[:12]

    @property
    def fingerprint(self) -> str:
        return self._fingerprint

    def encrypt(self, plaintext: str | bytes, aad: str = "") -> str:
        data = plaintext.encode() if isinstance(plaintext, str) else plaintext
        nonce = secrets.token_bytes(12)
        ct = self._aead.encrypt(nonce, data, aad.encode())
        return base64.urlsafe_b64encode(_VERSION + nonce + ct).decode()

    def decrypt(self, token: str, aad: str = "") -> bytes:
        raw = base64.urlsafe_b64decode(token.encode())
        if raw[:2] != _VERSION:
            raise ValueError("unsupported ciphertext version")
        nonce, ct = raw[2:14], raw[14:]
        return self._aead.decrypt(nonce, ct, aad.encode())

    def decrypt_str(self, token: str, aad: str = "") -> str:
        return self.decrypt(token, aad).decode()


def load_or_create_master_key(env_value: str | None, data_dir: Path) -> bytes:
    if env_value:
        try:
            key = base64.b64decode(env_value)
        except Exception as exc:  # pragma: no cover - defensive
            raise ValueError("FEDR_MASTER_KEY must be base64") from exc
        if len(key) != 32:
            raise ValueError("FEDR_MASTER_KEY must decode to 32 bytes")
        return key
    path = data_dir / "config" / "master.key"
    if path.exists():
        key = base64.b64decode(path.read_text().strip())
        if len(key) != 32:
            raise ValueError(f"corrupt master key at {path}")
        return key
    path.parent.mkdir(parents=True, exist_ok=True)
    key = secrets.token_bytes(32)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(base64.b64encode(key).decode())
    return key


def hash_phrase(phrase: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(8)
    digest = hashlib.pbkdf2_hmac("sha256", phrase.encode(), salt.encode(), 100_000).hex()
    return f"{salt}${digest}"


def verify_phrase(phrase: str, stored: str) -> bool:
    try:
        salt, digest = stored.split("$", 1)
    except ValueError:
        return False
    return hmac.compare_digest(hash_phrase(phrase, salt), stored)


def constant_time_equals(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())


def mask_secret(value: str | None, keep: int = 4) -> str:
    if not value:
        return ""
    if len(value) <= keep * 2:
        return "*" * len(value)
    return f"{value[:keep]}…{value[-keep:]}"
