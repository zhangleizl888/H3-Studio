"""数据库引擎与会话。"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from .config import get_settings


class Base(DeclarativeBase):
    pass


_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        url = get_settings().database_url
        if not url:
            raise RuntimeError(
                "H3_DATABASE_URL 未配置。先跑 apps/api/scripts/pg.py boot，它会把连接串写进 apps/api/.env"
            )
        _engine = create_async_engine(url, pool_pre_ping=True, future=True)
    return _engine


def session_factory() -> async_sessionmaker[AsyncSession]:
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(engine(), expire_on_commit=False, class_=AsyncSession)
    return _session_factory


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """默认隔离级别。派发循环需要 REPEATABLE READ + SKIP LOCKED，那里自己开事务。"""
    async with session_factory()() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


def reset_engine() -> None:
    """丢掉模块级引擎单例。

    pytest-asyncio 每个用例一个新事件循环，而 SQLAlchemy 的异步连接池绑定在创建它的
    循环上；跨循环复用会得到「Event loop is closed」和 asyncpg 的
    'NoneType' object has no attribute 'send'。测试收尾必须显式重置。
    """
    global _engine, _session_factory
    _engine = None
    _session_factory = None


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI 依赖。"""
    async with session_scope() as session:
        yield session
