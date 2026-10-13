"""Async database engine and session management."""
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from app.core.config import get_settings


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


async def init_db():
    """Create all tables (idempotent)."""
    from app.db import models  # noqa: F401  (register models on Base.metadata)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def close_db():
    await engine.dispose()
