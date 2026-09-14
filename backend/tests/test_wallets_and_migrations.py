"""Wallet backup/restore into a fresh store, withdrawal safety, deposit dedupe, EVM tx construction, migrations."""

from __future__ import annotations

import asyncio
import secrets
from decimal import Decimal

import pytest
from sqlalchemy import text

from fedr.core.enums import Chain
from fedr.db.engine import Database
from fedr.db.models import Base, Deposit, Withdrawal
from fedr.db.repo import Repo
from fedr.security.crypto import SecretBox
from fedr.wallets.keystore import KeystoreError, decrypt_keystore, restore_wallet
from fedr.wallets.manager import WalletManager
from fedr.wallets.transfers import DepositMonitor, WithdrawalService, validate_destination

D = Decimal


def test_backup_export_and_restore_into_fresh_store_with_integrity_check():
    async def run():
        wm = WalletManager(None, SecretBox(secrets.token_bytes(32)))
        e = await wm.create_bot_wallet("evm")
        s = await wm.create_bot_wallet("solana")
        ks_e = wm.export_keystore(e.id, "a-strong-passphrase-123")
        ks_s = wm.export_keystore(s.id, "a-strong-passphrase-123")
        assert "ciphertext" in ks_s and ks_s["cipher"] == "aes-256-gcm" and "crypto" in ks_e
        # fresh container: different master key, empty store
        fresh = WalletManager(None, SecretBox(secrets.token_bytes(32)))
        re_e = await restore_wallet(fresh, ks_e, "a-strong-passphrase-123")
        re_s = await restore_wallet(fresh, ks_s, "a-strong-passphrase-123")
        assert re_e.address == e.address and re_s.address == s.address and re_e.backed_up and re_s.backed_up
        assert fresh._secret(re_e.id) == wm._secret(e.id)
        # wrong passphrase / tampered ciphertext fail closed
        with pytest.raises(KeystoreError):
            decrypt_keystore(ks_s, "wrong-passphrase-xxxx")
        bad = dict(ks_s)
        bad["ciphertext"] = ks_s["ciphertext"][:-4] + "AAAA"
        with pytest.raises(KeystoreError):
            decrypt_keystore(bad, "a-strong-passphrase-123")
        with pytest.raises(KeystoreError):
            decrypt_keystore(ks_e, "wrong-passphrase-xxxx")
        # address mismatch (tampered address field) is rejected and nothing is stored
        tampered = dict(ks_e)
        tampered["address"] = "0" * 40
        with pytest.raises(KeystoreError):
            await restore_wallet(fresh, tampered, "a-strong-passphrase-123")
        assert len([w for w in fresh.list() if w.family == "evm"]) == 1
        with pytest.raises(KeystoreError):
            decrypt_keystore({"foo": "bar"}, "x")

    asyncio.run(run())


def test_destination_validation_per_chain():
    assert validate_destination(Chain.BASE, "0x" + "1" * 40) is None
    assert "checksum" in validate_destination(Chain.BASE, "0xAbC" + "1" * 37)
    assert validate_destination(Chain.BASE, "not-an-address")
    assert validate_destination(Chain.SOLANA, "0x" + "1" * 40)
    assert validate_destination(Chain.SOLANA, "So11111111111111111111111111111111111111112") is None


def test_withdrawal_quote_refuses_without_rpc_or_unknown_token():
    class Env:
        def rpc_for(self, chain):
            return None

    async def run():
        svc = WithdrawalService(None, Env(), lambda a: None)
        q = await svc.quote(Chain.BASE, "ETH", D("0.1"), "0x" + "1" * 40)
        assert not q.available and "no RPC" in q.reason
        q = await svc.quote(Chain.BASE, "ETH", D("0"), "0x" + "1" * 40)
        assert not q.available and "positive" in q.reason
        q = await svc.quote(Chain.BASE, "ETH", D("1"), "bad")
        assert not q.available and "valid EVM address" in q.reason

    asyncio.run(run())


def test_evm_tx_construction_native_and_erc20():
    class Env:
        def rpc_for(self, chain):
            return "http://unused"

    svc = WithdrawalService(
        None,
        Env(),
        lambda a: None,
        token_registry={
            "base": {"USDC": {"address": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913", "decimals": 6}}
        },
    )
    tx = svc.build_evm_tx(
        Chain.BASE,
        "0x" + "a" * 40,
        "ETH",
        D("0.25"),
        "0x" + "1" * 40,
        nonce=7,
        gas_price=10**9,
        is_token=False,
    )
    assert (
        tx["value"] == 250_000_000_000_000_000
        and tx["gas"] == 21_000
        and tx["chainId"] == 8453
        and tx["nonce"] == 7
    )
    tx = svc.build_evm_tx(
        Chain.BASE,
        "0x" + "a" * 40,
        "USDC",
        D("12.5"),
        "0x" + "1" * 40,
        nonce=8,
        gas_price=10**9,
        is_token=True,
    )
    assert tx["value"] == 0 and tx["to"].lower() == "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913"
    assert tx["data"].startswith("0xa9059cbb")  # ERC-20 transfer selector
    assert int(tx["data"][-64:], 16) == 12_500_000  # 12.5 USDC in 6 decimals
    with pytest.raises(ValueError):
        svc.build_evm_tx(
            Chain.BASE, "0x" + "a" * 40, "PEPE", D("1"), "0x" + "1" * 40, nonce=1, gas_price=1, is_token=True
        )


def test_deposit_monitor_dedupes_and_ignores_expected_outflows():
    mon = DepositMonitor()
    assert mon.observe("base", {"USDC": D("100")}) == []  # first observation only seeds
    ev = mon.observe("base", {"USDC": D("150")}, address="0xabc")
    assert len(ev) == 1 and ev[0].amount == D("50") and ev[0].dedupe_key
    assert mon.observe("base", {"USDC": D("150")}) == []  # no change
    assert mon.observe("base", {"USDC": D("140")}) == []  # outflow is not a deposit
    assert mon.observe("base", {"USDC": D("160")}, expected_deltas={"USDC": D("20")}) == []  # our own trade
    mon2 = DepositMonitor()
    mon2.observe("base", {"USDC": D("100")}, address="0xabc")
    mon2.observe("base", {"USDC": D("150")}, address="0xabc")
    mon2._last[("base", "USDC")] = D("100")  # simulate a re-read of an older snapshot then the same jump
    assert (
        mon2.observe("base", {"USDC": D("150")}, address="0xabc") == []
    )  # same balance-after -> not credited twice


def test_ledgers_dedupe_and_idempotency(tmp_path):
    async def run():
        db = Database(f"sqlite+aiosqlite:///{tmp_path}/t.db")
        await db.init()
        repo = Repo(db)
        from fedr.core.enums import TradingMode

        assert await repo.record_deposit(
            Deposit(
                id="d1",
                mode="live",
                chain="base",
                asset="USDC",
                amount="50",
                address="0xabc",
                dedupe_key="k1",
            )
        )
        assert not await repo.record_deposit(
            Deposit(
                id="d2",
                mode="live",
                chain="base",
                asset="USDC",
                amount="50",
                address="0xabc",
                dedupe_key="k1",
            )
        )
        assert len(await repo.list_deposits(TradingMode.LIVE)) == 1
        w = await repo.create_withdrawal(
            Withdrawal(
                id="w1",
                mode="live",
                chain="base",
                asset="ETH",
                amount="0.1",
                destination="0x1",
                request_key="rk",
            )
        )
        assert w is not None
        assert (
            await repo.create_withdrawal(
                Withdrawal(
                    id="w2",
                    mode="live",
                    chain="base",
                    asset="ETH",
                    amount="0.1",
                    destination="0x1",
                    request_key="rk",
                )
            )
            is None
        )
        await repo.update_withdrawal("w1", status="broadcast", tx_hash="0xhash")
        assert (await repo.list_withdrawals(TradingMode.LIVE))[0].tx_hash == "0xhash"
        assert await repo.schema_version() >= 2
        await db.close()

    asyncio.run(run())


def test_migration_upgrades_a_first_release_database(tmp_path):
    """A v1 database (no schema_version, no ledgers) is upgraded in place without touching existing rows."""

    async def run():
        from sqlalchemy.ext.asyncio import create_async_engine

        url = f"sqlite+aiosqlite:///{tmp_path}/old.db"
        eng = create_async_engine(url)
        old_tables = [
            t
            for n, t in Base.metadata.tables.items()
            if n not in ("deposits", "withdrawals", "positions", "schema_version")
        ]
        async with eng.begin() as conn:
            await conn.run_sync(lambda c: Base.metadata.create_all(c, tables=old_tables))
            await conn.execute(
                text("INSERT INTO settings (key, value, updated_at) VALUES ('app', '{}', CURRENT_TIMESTAMP)")
            )
        await eng.dispose()
        db = Database(url)
        applied = await db.init()
        assert applied == [2]
        repo = Repo(db)
        assert await repo.schema_version() == 2
        assert (await repo.get_settings_doc("app")) == {}
        assert await db.init() == []  # idempotent
        await db.close()

    asyncio.run(run())
