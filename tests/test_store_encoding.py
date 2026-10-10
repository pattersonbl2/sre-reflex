"""Regression: the connection pool must pin client_encoding to UTF8.

On a SQL_ASCII server (db-1 runs every database as SQL_ASCII) psycopg3 returns
text columns as `bytes`. `Store.migrate()` compares migration file names (str)
against names read from `schema_migrations`, so with bytes it never matched and
every start re-ran 001_init.sql -> DuplicateTable -> crash loop. This test needs
no database, so it runs in the default suite (the DB tests are marked `db`).
"""

import sre_reflex.store as store_mod


async def test_open_forces_utf8_client_encoding(monkeypatch):
    seen: dict = {}

    class FakePool:
        def __init__(self, url, **kwargs):
            seen.update(kwargs)

        async def open(self, **_):
            return None

    monkeypatch.setattr(store_mod, "AsyncConnectionPool", FakePool)
    await store_mod.Store.open("postgresql://u:p@db/x")

    assert seen.get("kwargs", {}).get("client_encoding", "").lower() in {"utf8", "utf-8"}
