"""The backend as a process: startup, health, mandatory auth, persistence across restart, DB schema."""

from __future__ import annotations

import sqlite3
import time

import httpx


def test_live_ready_and_public_health(fedr):
    with fedr.client(auth=False) as c:
        assert c.get("/health/live").status_code == 200
        ready = fedr.wait_ready()
        assert "checks" in ready and ready["checks"].get("database") is True
        assert ready["checks"].get("scan_loop") is True
        h = c.get("/health")
        assert h.status_code in (200, 503) and "status" in h.json()


def test_api_requires_token_and_csrf_header(fedr):
    with fedr.client(auth=False) as c:
        assert c.get("/api/trading/state").status_code == 401
        assert c.get("/api/system/health").status_code == 200  # public, unauthenticated liveness summary
    with httpx.Client(base_url=fedr.base, headers={"Authorization": f"Bearer {fedr.token}"}) as c:
        assert c.get("/api/trading/state").status_code == 200
    # cookie session: state changes need the CSRF header; bearer requests do not carry that risk
    with httpx.Client(base_url=fedr.base) as c:
        r = c.post("/api/auth/login", json={"token": fedr.token})
        assert r.status_code == 200 and "fedr_session" in r.headers.get("set-cookie", "")
        assert c.get("/api/trading/state").status_code == 200
        assert c.post("/api/trading/bot", json={"enabled": True}).status_code == 403  # missing CSRF header
        assert (
            c.post("/api/trading/bot", json={"enabled": True}, headers={"X-Fedr-Request": "1"}).status_code
            == 200
        )
        assert (
            c.post("/api/trading/bot", json={"enabled": False}, headers={"X-Fedr-Request": "1"}).status_code
            == 200
        )


def test_simulation_produces_evaluated_opportunities_with_profit_guard_output(fedr):
    deadline = time.time() + 60
    opps: list = []
    with fedr.client() as c:
        while time.time() < deadline and not opps:
            data = c.get("/api/opportunities").json()
            opps = data if isinstance(data, list) else data.get("items") or data.get("opportunities") or []
            if not opps:
                time.sleep(1)
    assert opps, "no opportunities evaluated within 60s in SIMULATION mode"
    o = opps[0]
    for key in ("gross", "expected", "worst"):
        assert any(key in k for k in o), f"profit triple field '{key}' missing from {sorted(o)}"


def test_emergency_stop_persists_across_restart(fedr):
    with fedr.client() as c:
        r = c.post("/api/system/emergency-stop", json={"reason": "integration test"})
        assert r.status_code == 200
        assert c.get("/api/trading/state").json()["emergency_stop"] is True
    fedr.restart()
    with fedr.client() as c:
        st = c.get("/api/trading/state").json()
        assert st["emergency_stop"] is True, "emergency stop must survive a restart"
        assert c.post("/api/system/emergency-stop/release", json={}).status_code == 200
        assert c.get("/api/trading/state").json()["emergency_stop"] is False


def test_settings_persist_and_live_mode_is_refused(fedr):
    with fedr.client() as c:
        r = c.post("/api/trading/auto-execute", json={"enabled": False})
        assert r.status_code == 200
        r = c.post("/api/trading/mode", json={"mode": "live"})
        assert r.status_code == 400 and "activation" in r.text.lower()
    fedr.restart()
    with fedr.client() as c:
        assert c.get("/api/trading/state").json()["auto_execute"] is False


def test_database_schema_and_audit_trail(fedr):
    db = fedr.data_dir / "database" / "fedr.db"
    assert db.exists()
    con = sqlite3.connect(db)
    try:
        assert con.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        ver = con.execute("SELECT MAX(version) FROM schema_version").fetchone()[0]
        assert ver == 2
        n = con.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]
        assert n >= 1
    finally:
        con.close()
    with fedr.client() as c:
        audit = c.get("/api/history/audit?limit=20").json()
        rows = audit if isinstance(audit, list) else audit.get("items") or audit.get("rows") or []
        assert any("Startup" in str(r) or "startup" in str(r).lower() for r in rows)


def test_generated_ui_token_file_is_private_when_no_token_configured(tmp_path_factory):
    from tests.integration.conftest import FedrProcess

    p = FedrProcess(tmp_path_factory.mktemp("fedr-notoken"), extra_env={"FEDR_AUTH_TOKEN": ""})
    p.start()
    try:
        tok = p.data_dir / "config" / "ui-token"
        assert tok.exists() and (tok.stat().st_mode & 0o777) == 0o600
        with httpx.Client(base_url=p.base) as c:
            assert c.get("/api/trading/state").status_code == 401
            ok = c.get("/api/trading/state", headers={"Authorization": f"Bearer {tok.read_text().strip()}"})
            assert ok.status_code == 200
    finally:
        p.stop()
