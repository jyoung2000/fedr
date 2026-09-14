"""Bot wallet backup → restore into a *fresh* process/data directory, and withdrawal refusals."""

from __future__ import annotations

from tests.integration.conftest import FedrProcess


def test_backup_restores_into_fresh_installation(fedr, tmp_path_factory):
    with fedr.client() as c:
        w = c.post("/api/wallets/bot", json={"family": "evm", "label": "it-evm", "mode": "testnet"})
        assert w.status_code == 200, w.text
        wallet = w.json()
        wid, addr = wallet["id"], wallet["address"]
        b = c.post(f"/api/wallets/bot/{wid}/backup", json={"passphrase": "correct horse battery staple 42"})
        assert b.status_code == 200, b.text
        keystore = b.json()
        assert "crypto" in keystore or "ciphertext" in str(keystore)
        assert addr.lower()[2:] in str(keystore).lower()
        assert "privateKey" not in str(keystore) and "private_key" not in str(keystore)
    fresh = FedrProcess(tmp_path_factory.mktemp("fedr-fresh")).start()
    try:
        with fresh.client() as c2:
            bad = c2.post(
                "/api/wallets/bot/import",
                json={"keystore": keystore, "passphrase": "wrong", "label": "restored", "mode": "testnet"},
            )
            assert bad.status_code == 400
            ok = c2.post(
                "/api/wallets/bot/import",
                json={
                    "keystore": keystore,
                    "passphrase": "correct horse battery staple 42",
                    "label": "restored",
                    "mode": "testnet",
                },
            )
            assert ok.status_code == 200, ok.text
            assert ok.json()["address"].lower() == addr.lower()
            listing = c2.get("/api/wallets").json()
            assert any(x["address"].lower() == addr.lower() for x in listing["bot_wallets"]), listing
    finally:
        fresh.stop()


def test_withdrawals_refused_in_simulation_and_unknown_tokens_unavailable(fedr):
    with fedr.client() as c:
        w = c.post("/api/wallets/bot", json={"family": "evm", "label": "it-withdraw", "mode": "testnet"})
        assert w.status_code == 200, w.text
        q = c.post(
            "/api/wallets/withdraw/quote",
            json={
                "chain": "ethereum",
                "asset": "NOPE",
                "amount": "1",
                "destination": "0x000000000000000000000000000000000000dEaD",
            },
        )
        assert q.status_code in (200, 400), q.text
        if q.status_code == 200:
            assert q.json().get("available") is False
        w = c.post(
            "/api/wallets/withdraw",
            json={
                "chain": "ethereum",
                "asset": "ETH",
                "amount": "0.001",
                "destination": "0x000000000000000000000000000000000000dEaD",
                "confirm": True,
            },
        )
        assert w.status_code in (400, 403, 409), w.text
        # SIMULATION/PAPER never move real funds: the mode gate fires before allowlist/quote checks
        assert "not available in paper/simulation mode" in w.text.lower(), w.text
        listing = c.get("/api/wallets").json()
        assert not [x for x in listing.get("withdrawals", []) if x.get("status") == "broadcast"]
