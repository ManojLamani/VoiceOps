"""Async database engine and session management."""
import os

from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from app.core.config import get_settings, BACKEND_DIR

BASELINE_REVISION = "0001"  # schema the app created with create_all before migrations existed


class Base(DeclarativeBase):
    pass


settings = get_settings()

engine_kwargs = {"echo": False}
if settings.is_sqlite:
    from sqlalchemy.pool import NullPool
    engine_kwargs.update(connect_args={"check_same_thread": False}, poolclass=NullPool)
else:
    engine_kwargs.update(pool_size=10, max_overflow=10, pool_pre_ping=True)

engine = create_async_engine(settings.database_url, **engine_kwargs)

async_session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_db():
    """Dependency that yields an async database session and commits on success."""
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


def alembic_config(connection=None):
    from alembic.config import Config
    config = Config(os.path.join(BACKEND_DIR, "alembic.ini"))
    config.attributes["connection"] = connection
    return config


def _upgrade(connection):
    from alembic import command
    config = alembic_config(connection)
    tables = set(inspect(connection).get_table_names())
    if "users" in tables and "alembic_version" not in tables:
        # Created by create_all before migrations existed: record it as the baseline instead of recreating it.
        command.stamp(config, BASELINE_REVISION)
    command.upgrade(config, "head")


def _drop_all(connection):
    from app.db import models  # noqa: F401  (register models on Base.metadata)
    Base.metadata.drop_all(connection)
    connection.execute(text("DROP TABLE IF EXISTS alembic_version"))


async def init_db(reset: bool = False):
    """Bring the schema up to date by applying migrations. With reset=True all tables are dropped first."""
    async with engine.begin() as conn:
        if reset:
            await conn.run_sync(_drop_all)
        await conn.run_sync(_upgrade)


async def close_db():
    await engine.dispose()
