"""Database migrations: the schema built by migrations must match the models."""
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import inspect

from app.db.database import engine, Base, alembic_config, _upgrade
from app.db import models  # noqa: F401


def _sync(run, fn):
    async def call():
        async with engine.begin() as conn:
            return await conn.run_sync(fn)
    return run(call)


def test_database_is_at_latest_migration(run):
    head = ScriptDirectory.from_config(alembic_config()).get_current_head()
    current = _sync(run, lambda conn: MigrationContext.configure(conn).get_current_revision())
    assert current == head


def test_models_match_migrations(run):
    """Fails when app/db/models.py changes without a migration: run `alembic revision --autogenerate`."""
    def diff(conn):
        return compare_metadata(MigrationContext.configure(conn, opts={"compare_type": True}), Base.metadata)
    assert _sync(run, diff) == []


def test_downgrade_and_upgrade_round_trip(run):
    def round_trip(conn):
        config = alembic_config(conn)
        command.downgrade(config, "base")
        assert set(inspect(conn).get_table_names()) <= {"alembic_version"}
        command.upgrade(config, "head")
        return set(inspect(conn).get_table_names())
    assert _sync(run, round_trip) >= set(Base.metadata.tables)


def test_existing_create_all_database_is_adopted(run):
    """A database created by create_all before migrations existed keeps its data and is brought to head."""
    def adopt(conn):
        users = conn.exec_driver_sql("SELECT COUNT(*) FROM users").scalar()
        conn.exec_driver_sql("DROP TABLE alembic_version")
        _upgrade(conn)
        return users, conn.exec_driver_sql("SELECT COUNT(*) FROM users").scalar(), \
            MigrationContext.configure(conn).get_current_revision()
    before, after, revision = _sync(run, adopt)
    assert before > 0 and after == before
    assert revision == ScriptDirectory.from_config(alembic_config()).get_current_head()
