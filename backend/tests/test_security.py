import base64
import json
import secrets
from pathlib import Path

import pytest

from fedr.security.crypto import SecretBox, hash_phrase, load_or_create_master_key, mask_secret, verify_phrase
from fedr.security.redaction import redact_value, structlog_redactor


def test_secret_box_roundtrip_and_aad():
    box = SecretBox(secrets.token_bytes(32))
    token = box.encrypt("my-api-secret", aad="acct1")
    assert box.decrypt_str(token, aad="acct1") == "my-api-secret"
    with pytest.raises(Exception):
        box.decrypt_str(token, aad="acct2")
    with pytest.raises(ValueError):
        SecretBox(b"short")


def test_master_key_generated_with_0600(tmp_path: Path):
    key = load_or_create_master_key(None, tmp_path)
    p = tmp_path / "config" / "master.key"
    assert p.exists() and len(key) == 32
    assert oct(p.stat().st_mode & 0o777) == "0o600"
    assert load_or_create_master_key(None, tmp_path) == key
    env_key = base64.b64encode(secrets.token_bytes(32)).decode()
    assert load_or_create_master_key(env_key, tmp_path) == base64.b64decode(env_key)
    with pytest.raises(ValueError):
        load_or_create_master_key(base64.b64encode(b"x" * 5).decode(), tmp_path)


def test_phrase_hash():
    h = hash_phrase("ACTIVATE LIVE TRADING")
    assert verify_phrase("ACTIVATE LIVE TRADING", h) and not verify_phrase("nope", h)
    assert mask_secret("abcdefghijkl") == "abcd…ijkl"


def test_redaction_hides_keys_and_values():
    payload = {
        "apiKey": "ABC123",
        "nested": {"secret": "s", "privateKey": "0x" + "a" * 64},
        "note": "key 0x" + "b" * 64 + " leaked",
        "ok": "fine",
    }
    r = redact_value(payload)
    assert (
        r["apiKey"] == "[REDACTED]"
        and r["nested"]["secret"] == "[REDACTED]"
        and r["nested"]["privateKey"] == "[REDACTED]"
    )
    assert "b" * 64 not in r["note"] and r["ok"] == "fine"
    ev = structlog_redactor(
        None,
        "info",
        {"event": "x", "authorization": "Bearer abcdefghijklmnopqrstuvwxyz", "mnemonic": "word word"},
    )
    assert ev["authorization"] == "[REDACTED]" and ev["mnemonic"] == "[REDACTED]"
    sol = "5" * 88
    assert "[REDACTED]" in redact_value(f"key {sol} end")
    assert json.dumps(r)
