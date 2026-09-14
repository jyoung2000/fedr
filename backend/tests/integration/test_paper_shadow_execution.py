"""Shadow mode records without submitting; paper (simulated) auto-execution produces reconciled trades
with actual vs expected P&L. Runs offline on the synthetic market (SIMULATION mode)."""

from __future__ import annotations

import time


def _recent(c):
    st = c.get("/api/trading/state").json()
    return st, st.get("recent") or []


def test_shadow_mode_records_but_submits_nothing(fedr):
    with fedr.client() as c:
        c.post("/api/trading/shadow/clear", json={})
        assert c.post("/api/trading/shadow", json={"enabled": True}).status_code == 200
        assert c.post("/api/trading/bot", json={"enabled": True}).status_code == 200
        assert c.post("/api/trading/auto-execute", json={"enabled": True}).status_code == 200
        deadline = time.time() + 45
        shadow = {}
        while time.time() < deadline:
            st, recent = _recent(c)
            shadow = st.get("shadow") or {}
            if (shadow.get("recorded") or shadow.get("count") or shadow.get("total") or 0) > 0:
                break
            time.sleep(1)
        assert any((v or 0) > 0 for k, v in shadow.items() if isinstance(v, (int, float))), shadow
        st, recent = _recent(c)
        assert st["shadow_mode"] is True
        assert not [t for t in recent if t.get("status") == "filled"], "shadow mode must never fill"
        c.post("/api/trading/shadow", json={"enabled": False})


def test_paper_auto_execution_produces_reconciled_trades(fedr):
    with fedr.client() as c:
        c.post("/api/trading/shadow", json={"enabled": False})
        c.post("/api/trading/bot", json={"enabled": True})
        c.post("/api/trading/auto-execute", json={"enabled": True})
        deadline = time.time() + 120
        filled: list = []
        while time.time() < deadline and not filled:
            _, recent = _recent(c)
            filled = [t for t in recent if t.get("status") in ("filled", "recovered")]
            if not filled:
                time.sleep(2)
        assert filled, "no simulated trade filled within 120s (synthetic dislocations enabled)"
        t = filled[0]
        assert t.get("actual_net") is not None
        detail = c.get(f"/api/history/trades/{t['id']}").json()
        assert detail.get("mode") in ("simulation", "paper")
        assert "detail" in detail and detail.get("actual_net") is not None
        pnl = c.get("/api/history/pnl").json()
        assert pnl, "P&L summary must exist after a filled trade"
        # positions endpoint is live and honest about its verification level
        pos = c.get("/api/trading/positions").json()
        assert "open" in pos and "NOT verified" in pos["verification"]
        c.post("/api/trading/auto-execute", json={"enabled": False})
