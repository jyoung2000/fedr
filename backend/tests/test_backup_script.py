"""scripts/backup.py: create → verify → restore into a fresh directory; secrets excluded by default;
plaintext-key detection fails verification."""

from __future__ import annotations

import base64
import importlib.util
import json
import sqlite3
import sys
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("fedr_backup", ROOT / "scripts" / "backup.py")
backup = importlib.util.module_from_spec(spec)
sys.modules["fedr_backup"] = backup
spec.loader.exec_module(backup)


@pytest.fixture
def data_dir(tmp_path):
    import asyncio

    from fedr.db.engine import Database

    d = tmp_path / "data"
    for sub in ("database", "config", "wallets", "strategies", "logs", "backups", "reports"):
        (d / sub).mkdir(parents=True)

    async def init():
        db = Database(f"sqlite+aiosqlite:///{d}/database/fedr.db")
        await db.init()
        await db.close() if hasattr(db, "close") else None

    asyncio.run(init())
    con = sqlite3.connect(d / "database" / "fedr.db")
    con.execute(
        "INSERT INTO wallets (id, chain, address, label, kind, mode, key_enc, backed_up, created_at) VALUES ('w1','ethereum','0xabc','x','bot','testnet',?, 0, CURRENT_TIMESTAMP)",
        (base64.b64encode(b"ciphertext-not-a-key").decode(),),
    )
    con.commit()
    con.close()
    (d / "config" / "master.key").write_bytes(base64.b64encode(b"0" * 32))
    (d / "config" / "ui-token").write_text("tok")
    (d / "config" / "settings.json").write_text("{}")
    (d / "wallets" / "w1.keystore.json").write_text(json.dumps({"crypto": {"ciphertext": "aa"}}))
    (d / "logs" / "app.log").write_text("SECRET_LOG")
    return d


def test_create_verify_restore_roundtrip(data_dir, tmp_path, capsys):
    out = tmp_path / "b.tar.gz"
    backup.create(data_dir, out, include_secrets=False)
    with tarfile.open(out) as tar:
        names = set(tar.getnames())
    assert "database/fedr.db" in names and "wallets/w1.keystore.json" in names
    assert "config/master.key" not in names and "config/ui-token" not in names
    assert not any(n.startswith("logs/") for n in names)
    assert backup.verify(out) == 0
    fresh = tmp_path / "fresh"
    assert backup.restore(out, fresh, force=False) == 0
    assert (fresh / "database" / "fedr.db").exists()
    con = sqlite3.connect(fresh / "database" / "fedr.db")
    assert con.execute("SELECT COUNT(*) FROM wallets").fetchone()[0] == 1
    assert con.execute("SELECT MAX(version) FROM schema_version").fetchone()[0] == 2
    con.close()
    assert "no master key restored" in capsys.readouterr().out
    # refuses to clobber a non-empty target without --force
    with pytest.raises(SystemExit):
        backup.restore(out, fresh, force=False)


def test_include_secrets_flag_and_manifest(data_dir, tmp_path):
    out = tmp_path / "s.tar.gz"
    backup.create(data_dir, out, include_secrets=True)
    with tarfile.open(out) as tar:
        names = set(tar.getnames())
        manifest = json.loads(tar.extractfile("manifest.json").read())
    assert "config/master.key" in names and manifest["includes_secrets"] is True
    assert manifest["schema_version"] == 2
    assert backup.verify(out) == 0


def test_tampered_archive_and_plaintext_key_fail_verification(data_dir, tmp_path):
    con = sqlite3.connect(data_dir / "database" / "fedr.db")
    con.execute("UPDATE wallets SET key_enc = '0x' || substr(replace(hex(randomblob(32)),'','') , 1, 64)")
    con.commit()
    con.close()
    out = tmp_path / "p.tar.gz"
    backup.create(data_dir, out, include_secrets=False)
    assert backup.verify(out) == 1  # plaintext private key detected
    # tamper: flip a byte of a file inside a fresh archive
    con = sqlite3.connect(data_dir / "database" / "fedr.db")
    con.execute("UPDATE wallets SET key_enc = 'Y2lwaGVydGV4dA=='")
    con.commit()
    con.close()
    good = tmp_path / "g.tar.gz"
    backup.create(data_dir, good, include_secrets=False)
    assert backup.verify(good) == 0
    tampered = tmp_path / "t.tar.gz"
    with tarfile.open(good) as src, tarfile.open(tampered, "w:gz") as dst:
        for m in src.getmembers():
            data = src.extractfile(m).read()
            if m.name == "config/settings.json":
                data = b'{"tampered": true}'
                m.size = len(data)
            import io

            dst.addfile(m, io.BytesIO(data))
    assert backup.verify(tampered) == 1
