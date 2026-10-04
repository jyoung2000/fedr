"""Versioned schema migrations (SQLite / Postgres via SQLAlchemy).

A fresh database gets ``create_all`` and is stamped with the latest version. An existing database is
upgraded step by step; each step is idempotent so an interrupted upgrade can be re-run safely.
"""

from __future__ import annotations

from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import AsyncEngine

from fedr.core.logging import get_logger
from fedr.db.models import Base

log = get_logger("migrations")

LATEST = 3


def _table_names(sync_conn) -> set[str]:
    return set(inspect(sync_conn).get_table_names())


def _columns(sync_conn, table: str) -> set[str]:
    return {c["name"] for c in inspect(sync_conn).get_columns(table)}


async def _v1_initial(conn) -> None:
    await conn.run_sync(Base.metadata.create_all)


async def _v2_ledgers(conn) -> None:
    """Deposits / withdrawals / positions / schema_version tables (added in the hardening release)."""
    await conn.run_sync(
        lambda c: Base.metadata.create_all(
            c,
            tables=[
                Base.metadata.tables[t] for t in ("deposits", "withdrawals", "positions", "schema_version")
            ],
        )
    )


_V3_COLUMNS = {
    "orders": [
        ("client_order_id", "VARCHAR(80)"),
        ("venue_order_id", "VARCHAR(120)"),
        ("role", "VARCHAR(24)"),
        ("account_ref", "VARCHAR(160)"),
        ("cost", "VARCHAR(40)"),
        ("updated_at", "DATETIME"),
    ],
    "trades": [("settlement", "VARCHAR(16)")],
    "pnl_daily": [("legacy", "BOOLEAN")],
}


async def _v3_journal_and_ledger(conn) -> None:
    """Order journal columns, settlement provenance, durable reconciliation ledger.

    Existing history is preserved verbatim. Records written before this version cannot prove the provenance
    of their realized values (audit F06/F08), so they are marked ``legacy`` instead of being "corrected":
    trades -> settlement='legacy', pnl_daily -> legacy=1, open carry positions -> payload.legacy and the
    entry amount that was already booked (from the entry trade), so the close books only the remainder."""
    for table, cols in _V3_COLUMNS.items():
        existing = await conn.run_sync(lambda c, t=table: _columns(c, t))
        for name, ddl in cols:
            if name not in existing:
                await conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))
    await conn.run_sync(
        lambda c: Base.metadata.create_all(c, tables=[Base.metadata.tables["ledger_entries"]])
    )
    await conn.execute(
        text("CREATE INDEX IF NOT EXISTS ix_orders_client_order_id ON orders (client_order_id)")
    )
    await conn.execute(text("UPDATE trades SET settlement = 'legacy' WHERE settlement IS NULL"))
    await conn.execute(text("UPDATE pnl_daily SET legacy = 1 WHERE legacy IS NULL"))
    rows = (
        await conn.execute(text("SELECT id, payload FROM positions WHERE status IN ('open', 'closing')"))
    ).all()
    for pid, payload in rows:
        pl = _json(payload)
        if pl.get("legacy") is not None:
            continue
        booked = None
        tid = pl.get("trade_id")
        if tid:
            r = (await conn.execute(text("SELECT actual_net FROM trades WHERE id = :i"), {"i": tid})).first()
            if r is not None and r[0] is not None:
                booked = str(r[0])
        pl["legacy"] = True
        if booked is not None:
            pl["entry_booked_usd"] = booked  # the old code realised the entry spread at entry
        else:
            pl["entry_booking_unknown"] = True
        await conn.execute(
            text("UPDATE positions SET payload = :p WHERE id = :i"), {"p": _dump(pl), "i": pid}
        )


def _json(v) -> dict:
    import json

    if isinstance(v, dict):
        return dict(v)
    try:
        return dict(json.loads(v or "{}"))
    except Exception:
        return {}


def _dump(v: dict) -> str:
    import json

    return json.dumps(v)


STEPS = {
    1: ("initial schema", _v1_initial),
    2: ("deposit/withdrawal/position ledgers", _v2_ledgers),
    3: ("order journal, settlement provenance, reconciliation ledger", _v3_journal_and_ledger),
}


async def current_version(engine: AsyncEngine) -> int:
    async with engine.connect() as conn:
        names = await conn.run_sync(_table_names)
        if "schema_version" not in names:
            return 0 if "trades" not in names else 1  # pre-migration database from the first release
        row = (await conn.execute(text("SELECT MAX(version) FROM schema_version"))).scalar()
        return int(row or 0)


def _sqlite_path(engine: AsyncEngine) -> str | None:
    url = engine.url
    if not url.drivername.startswith("sqlite"):
        return None
    db = url.database
    return db if db and db != ":memory:" else None


async def backup_before_upgrade(engine: AsyncEngine, from_version: int) -> str | None:
    """Consistent copy of an existing SQLite database before an upgrade (``VACUUM INTO``).

    Restore = stop FEDR, replace the database file with the backup, start the previous release."""
    path = _sqlite_path(engine)
    if path is None:
        return None
    from datetime import UTC, datetime
    from pathlib import Path

    target = Path(path).with_name(
        f"{Path(path).name}.pre-v{LATEST}-from-v{from_version}-{datetime.now(UTC):%Y%m%dT%H%M%SZ}.bak"
    )
    async with engine.connect() as conn:
        await conn.exec_driver_sql(f"VACUUM INTO '{target.as_posix()}'")
    log.warning("database backed up before schema upgrade", backup=str(target), from_version=from_version)
    return str(target)


async def migrate(engine: AsyncEngine) -> list[int]:
    applied: list[int] = []
    version = await current_version(engine)
    if 0 < version < LATEST:
        await backup_before_upgrade(engine, version)
    if version == 0:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            await conn.execute(
                text(
                    "INSERT INTO schema_version (version, note, applied_at) VALUES (:v, :n, CURRENT_TIMESTAMP)"
                ),
                {"v": LATEST, "n": "fresh install"},
            )
        log.info("database initialised", schema_version=LATEST)
        return [LATEST]
    for v in range(version + 1, LATEST + 1):
        note, fn = STEPS[v]
        async with engine.begin() as conn:
            await fn(conn)
            await conn.execute(
                text(
                    "INSERT INTO schema_version (version, note, applied_at) VALUES (:v, :n, CURRENT_TIMESTAMP)"
                ),
                {"v": v, "n": note},
            )
        applied.append(v)
        log.info("migration applied", version=v, note=note)
    return applied
