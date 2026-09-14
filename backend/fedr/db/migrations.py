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

LATEST = 2


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


STEPS = {1: ("initial schema", _v1_initial), 2: ("deposit/withdrawal/position ledgers", _v2_ledgers)}


async def current_version(engine: AsyncEngine) -> int:
    async with engine.connect() as conn:
        names = await conn.run_sync(_table_names)
        if "schema_version" not in names:
            return 0 if "trades" not in names else 1  # pre-migration database from the first release
        row = (await conn.execute(text("SELECT MAX(version) FROM schema_version"))).scalar()
        return int(row or 0)


async def migrate(engine: AsyncEngine) -> list[int]:
    applied: list[int] = []
    version = await current_version(engine)
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
