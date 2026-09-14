"""Log redaction: secrets must never be written to logs."""
from __future__ import annotations

import re
from typing import Any

_SENSITIVE_KEYS = re.compile(
    r"(secret|password|passphrase|private[_-]?key|privkey|seed|mnemonic|api[_-]?key|token|authorization|cookie|signature)",
    re.I,
)
# 64-hex private keys, 0x-prefixed keys, base58 solana secrets (~87/88 chars), long API keys
_SENSITIVE_VALUES = [
    re.compile(r"\b0x[0-9a-fA-F]{64}\b"),
    re.compile(r"\b[0-9a-fA-F]{64}\b"),
    re.compile(r"\b[1-9A-HJ-NP-Za-km-z]{85,90}\b"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9\-_.=]{16,}"),
]


def redact_value(value: Any) -> Any:
    if isinstance(value, str):
        out = value
        for rx in _SENSITIVE_VALUES:
            out = rx.sub("[REDACTED]", out)
        return out
    if isinstance(value, dict):
        return {k: ("[REDACTED]" if _SENSITIVE_KEYS.search(str(k)) else redact_value(v)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact_value(v) for v in value]
    return value


def structlog_redactor(_logger: Any, _method: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    return {k: ("[REDACTED]" if _SENSITIVE_KEYS.search(k) else redact_value(v)) for k, v in event_dict.items()}
