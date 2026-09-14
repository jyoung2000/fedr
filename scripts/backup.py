#!/usr/bin/env python3
"""FEDR backup / restore / verify.

    scripts/backup.py create  [--data-dir /data] [--out FILE] [--include-secrets]
    scripts/backup.py restore ARCHIVE [--data-dir /data] [--force]
    scripts/backup.py verify  ARCHIVE

What a backup contains: a consistent SQLite copy (online backup API - safe while FEDR runs), config
(minus secrets by default), encrypted wallet keystores, strategies and reports, plus a manifest with
SHA-256 hashes. Logs, market-data caches and previous backups are excluded.

Secrets: `config/master.key` (encrypts credentials + wallet keys at rest) and `config/ui-token` are
NOT included unless --include-secrets is given. Without the ORIGINAL master key a restored database
cannot decrypt exchange credentials or bot-wallet keys - keep the key somewhere else and safe.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import sqlite3
import sys
import tarfile
import tempfile
import time
from pathlib import Path

SECRET_FILES = {"config/master.key", "config/ui-token"}
INCLUDE_DIRS = ("config", "wallets", "strategies", "reports")
DB_REL = "database/fedr.db"
MANIFEST = "manifest.json"
HEX_PRIVATE_KEY = re.compile(r"^(0x)?[0-9a-fA-F]{64}$")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _db_snapshot(db_path: Path) -> bytes:
    """Consistent copy of a (possibly live, WAL-mode) SQLite database."""
    with tempfile.TemporaryDirectory() as td:
        dst_path = Path(td) / "snapshot.db"
        src = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        dst = sqlite3.connect(dst_path)
        try:
            src.backup(dst)
        finally:
            dst.close()
            src.close()
        return dst_path.read_bytes()


def create(data_dir: Path, out: Path | None, include_secrets: bool) -> Path:
    data_dir = data_dir.resolve()
    if not data_dir.is_dir():
        sys.exit(f"data dir not found: {data_dir}")
    ts = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    out = out or (data_dir / "backups" / f"fedr-backup-{ts}.tar.gz")
    out.parent.mkdir(parents=True, exist_ok=True)
    files: dict[str, bytes] = {}
    db = data_dir / DB_REL
    if db.exists():
        files[DB_REL] = _db_snapshot(db)
    for sub in INCLUDE_DIRS:
        base = data_dir / sub
        if not base.is_dir():
            continue
        for p in sorted(base.rglob("*")):
            if not p.is_file():
                continue
            rel = p.relative_to(data_dir).as_posix()
            if rel in SECRET_FILES and not include_secrets:
                continue
            files[rel] = p.read_bytes()
    schema_version = None
    if DB_REL in files:
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "m.db"
            p.write_bytes(files[DB_REL])
            con = sqlite3.connect(p)
            try:
                row = con.execute("SELECT MAX(version) FROM schema_version").fetchone()
                schema_version = row[0] if row else None
            except sqlite3.Error:
                schema_version = None
            finally:
                con.close()
    manifest = {
        "created_at": ts,
        "tool": "fedr/scripts/backup.py",
        "includes_secrets": include_secrets,
        "schema_version": schema_version,
        "files": {
            rel: {"sha256": sha256(b), "bytes": len(b)} for rel, b in files.items()
        },
    }
    with tarfile.open(out, "w:gz") as tar:
        for rel, b in files.items():
            info = tarfile.TarInfo(rel)
            info.size = len(b)
            info.mtime = int(time.time())
            info.mode = 0o600
            tar.addfile(info, io.BytesIO(b))
        mb = json.dumps(manifest, indent=2).encode()
        info = tarfile.TarInfo(MANIFEST)
        info.size = len(mb)
        info.mtime = int(time.time())
        info.mode = 0o600
        tar.addfile(info, io.BytesIO(mb))
    os.chmod(out, 0o600)
    print(f"backup written: {out} ({len(files)} files, {out.stat().st_size} bytes)")
    if not include_secrets:
        print(
            "NOTE: config/master.key and config/ui-token were NOT included (use --include-secrets)."
        )
        print(
            "      Keep the master key separately: without it encrypted credentials and wallet keys are unusable."
        )
    else:
        print(
            "WARNING: the archive contains the master key and UI token - store it encrypted and offline."
        )
    return out


def _read_archive(archive: Path) -> tuple[dict, dict[str, bytes]]:
    files: dict[str, bytes] = {}
    with tarfile.open(archive, "r:gz") as tar:
        for m in tar.getmembers():
            if not m.isfile():
                continue
            name = m.name.lstrip("./")
            if name.startswith("/") or ".." in Path(name).parts:
                sys.exit(f"refusing unsafe path in archive: {m.name}")
            f = tar.extractfile(m)
            files[name] = f.read() if f else b""
    if MANIFEST not in files:
        sys.exit("not a FEDR backup: manifest.json missing")
    manifest = json.loads(files.pop(MANIFEST))
    return manifest, files


def verify(archive: Path) -> int:
    manifest, files = _read_archive(archive)
    problems: list[str] = []
    for rel, meta in manifest["files"].items():
        if rel not in files:
            problems.append(f"missing: {rel}")
        elif sha256(files[rel]) != meta["sha256"]:
            problems.append(f"hash mismatch: {rel}")
    for rel in files:
        if rel not in manifest["files"]:
            problems.append(f"unlisted file: {rel}")
    has_secrets = any(rel in SECRET_FILES for rel in files)
    if has_secrets != bool(manifest.get("includes_secrets")):
        problems.append(
            "manifest includes_secrets flag does not match the archive contents"
        )
    # plaintext-secret scan: wallet keys and exchange credentials must be ciphertext in the DB
    if DB_REL in files:
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "v.db"
            p.write_bytes(files[DB_REL])
            con = sqlite3.connect(p)
            try:
                for (key_enc,) in con.execute(
                    "SELECT key_enc FROM wallets WHERE key_enc IS NOT NULL"
                ):
                    if HEX_PRIVATE_KEY.match(str(key_enc).strip()):
                        problems.append(
                            "wallets.key_enc looks like a PLAINTEXT private key"
                        )
                for (cred,) in con.execute(
                    "SELECT credentials_enc FROM exchange_accounts WHERE credentials_enc IS NOT NULL"
                ):
                    try:
                        json.loads(cred)
                        problems.append(
                            "exchange_accounts.credentials_enc is PLAINTEXT JSON"
                        )
                    except Exception:
                        pass
            except sqlite3.Error as exc:
                problems.append(f"database unreadable: {exc}")
            finally:
                con.close()
    for rel, b in files.items():
        if rel.startswith("wallets/") and (
            b"private_key" in b or b"privateKey" in b or b"mnemonic" in b
        ):
            problems.append(f"{rel} contains a plaintext key field")
    print(f"archive: {archive}")
    print(
        f"created: {manifest.get('created_at')}  schema_version: {manifest.get('schema_version')}  files: {len(files)}  secrets included: {has_secrets}"
    )
    for pr in problems:
        print(f"PROBLEM: {pr}")
    print("verify: " + ("OK" if not problems else f"FAILED ({len(problems)} problems)"))
    return 0 if not problems else 1


def restore(archive: Path, data_dir: Path, force: bool) -> int:
    if verify(archive) != 0:
        sys.exit("refusing to restore: archive failed verification")
    manifest, files = _read_archive(archive)
    data_dir = data_dir.resolve()
    existing = (
        [p for p in data_dir.rglob("*") if p.is_file()] if data_dir.exists() else []
    )
    if existing and not force:
        sys.exit(
            f"{data_dir} is not empty ({len(existing)} files); pass --force to overwrite (the current database is replaced)"
        )
    for rel, b in files.items():
        dst = data_dir / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(b)
        os.chmod(dst, 0o600)
    for stale in ("database/fedr.db-wal", "database/fedr.db-shm"):
        p = data_dir / stale
        if p.exists():
            p.unlink()
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
        (data_dir / sub).mkdir(parents=True, exist_ok=True)
    print(f"restored {len(files)} files into {data_dir}")
    if not (data_dir / "config" / "master.key").exists():
        print(
            "WARNING: no master key restored. Set FEDR_MASTER_KEY to the ORIGINAL key (or copy config/master.key)"
        )
        print(
            "         before starting FEDR, otherwise stored credentials and bot-wallet keys cannot be decrypted."
        )
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("create")
    c.add_argument("--data-dir", default=os.environ.get("FEDR_DATA_DIR", "/data"))
    c.add_argument("--out")
    c.add_argument("--include-secrets", action="store_true")
    r = sub.add_parser("restore")
    r.add_argument("archive")
    r.add_argument("--data-dir", default=os.environ.get("FEDR_DATA_DIR", "/data"))
    r.add_argument("--force", action="store_true")
    v = sub.add_parser("verify")
    v.add_argument("archive")
    a = ap.parse_args(argv)
    if a.cmd == "create":
        create(Path(a.data_dir), Path(a.out) if a.out else None, a.include_secrets)
        return 0
    if a.cmd == "verify":
        return verify(Path(a.archive))
    return restore(Path(a.archive), Path(a.data_dir), a.force)


if __name__ == "__main__":
    sys.exit(main())
