"""Alembic environment.

Used two ways:
- by the app on startup (app.db.database.init_db), which passes an open connection in config.attributes
- by the alembic CLI, which connects with DATABASE_URL from app settings
"""
import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import get_settings
from app.db.database import Base
from app.db import models  # noqa: F401  (register models on Base.metadata)

config = context.config
target_metadata = Base.metadata


def render_item(type_, obj, autogen_context):
    # Migrations must not import app code, so render the GUID column type as its plain implementation.
    if type_ == "type" and isinstance(obj, models.GUID):
        return "sa.String(length=36)"
    return False


def configure(connection):
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        render_item=render_item,
        render_as_batch=connection.dialect.name == "sqlite",  # SQLite can't ALTER most columns in place
        compare_type=True,
    )


def run_migrations(connection):
    configure(connection)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations():
    engine = create_async_engine(get_settings().database_url)
    async with engine.begin() as connection:
        await connection.run_sync(run_migrations)
    await engine.dispose()


if context.is_offline_mode():
    context.configure(url=get_settings().database_url, target_metadata=target_metadata,
                      render_item=render_item, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
elif (connection := config.attributes.get("connection")) is not None:
    run_migrations(connection)
else:
    if config.config_file_name is not None:
        fileConfig(config.config_file_name, disable_existing_loggers=False)
    asyncio.run(run_async_migrations())
