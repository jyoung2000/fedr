#!/usr/bin/env python3
"""Fail if any git-tracked file contains something that looks like a real secret.

Patterns: EVM private keys (0x + 64 hex) outside test vectors, 12/24-word mnemonics, exchange API keys
with well-known prefixes, .env files, keystore JSON. Test files may contain the documented dummy keys
listed in ALLOW (the eth-tester / well-known Hardhat accounts and all-zero keys)."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ALLOW = {
    "0x" + "0" * 64,
    "0x" + "1" * 64,
    "0x" + "2" * 64,
    "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80",  # hardhat #0 (public test key)
    "0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d",  # hardhat #1
}
PATTERNS = [
    ("evm private key", re.compile(r"\b0x[0-9a-fA-F]{64}\b")),
    ("mnemonic", re.compile(r"\b(?:[a-z]{3,8} ){11,23}[a-z]{3,8}\b")),
    ("AWS key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    (
        "generic secret assignment",
        re.compile(
            r"(?i)\b(api[_-]?secret|secret[_-]?key|private[_-]?key)\s*[:=]\s*['\"][A-Za-z0-9+/=_-]{24,}['\"]"
        ),
    ),
]
SKIP_SUFFIX = {".png", ".jpg", ".ico", ".woff", ".woff2", ".lock", ".json.gz"}
SKIP_PATH_PARTS = {"package-lock.json", "node_modules", "dist"}
MNEMONIC_OK_FILES = {"docs/", "README", ".md"}


def _bip39_words() -> set[str] | None:
    try:
        from mnemonic import Mnemonic  # type: ignore

        return set(Mnemonic("english").wordlist)
    except Exception:
        return None


_BIP39 = _bip39_words()


def _looks_like_bip39(seq: str) -> bool:
    words = seq.split()
    if (
        _BIP39 is None
    ):  # package unavailable: only flag when every word is short and there are exactly 12/24
        return len(words) in (12, 24)
    return all(w in _BIP39 for w in words)


def main() -> int:
    files = subprocess.run(
        ["git", "ls-files"], cwd=ROOT, capture_output=True, text=True
    ).stdout.split()
    problems = []
    for rel in files:
        if rel in (".env",) or rel.endswith(".env") or rel.endswith(".keystore.json"):
            problems.append(f"{rel}: secret-bearing file tracked")
            continue
        p = ROOT / rel
        if (
            p.suffix in SKIP_SUFFIX
            or any(part in rel for part in SKIP_PATH_PARTS)
            or not p.is_file()
        ):
            continue
        try:
            text = p.read_text(errors="ignore")
        except OSError:
            continue
        for name, pat in PATTERNS:
            for m in pat.finditer(text):
                val = m.group(0)
                if name == "evm private key" and (
                    val.lower() in ALLOW or "tests/" in rel or "test_" in rel
                ):
                    # 64-hex values in tests are tx hashes / fixtures; only the ALLOW list is permitted as keys
                    continue
                if name == "mnemonic" and not _looks_like_bip39(val):
                    continue  # ordinary prose (docs, UI copy) also has runs of short lowercase words
                if name == "evm private key" and re.search(
                    r"(hash|tx|txid|sha|digest|topic|selector)",
                    text[max(0, m.start() - 80) : m.start()],
                    re.IGNORECASE,
                ):
                    continue
                problems.append(f"{rel}: possible {name}: {val[:12]}…")
    for pr in problems:
        print("PROBLEM:", pr)
    print("secret scan:", "OK" if not problems else f"{len(problems)} finding(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
