"""
Async SQLAlchemy engine and session factory.

Works with Postgres (postgresql+asyncpg://...) and SQLite (sqlite+aiosqlite://...).
"""

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import settings

_is_sqlite = settings.DATABASE_URL.startswith("sqlite")

engine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,
    **({} if _is_sqlite else {"pool_pre_ping": True, "pool_size": 5, "max_overflow": 10}),
)

# expire_on_commit=False keeps attributes readable after commit; async SQLAlchemy
# cannot lazy-load an expired attribute.
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def get_db():
    """FastAPI dependency: one session and one transaction per request."""
    async with async_session() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
