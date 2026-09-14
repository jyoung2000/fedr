from __future__ import annotations

from typing import Any

from fedr.security.redaction import redact_value


def _deep_merge(base: dict, patch: dict) -> dict:
    out = dict(base)
    for k, v in patch.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _redact(obj: Any) -> Any:
    return redact_value(obj)
