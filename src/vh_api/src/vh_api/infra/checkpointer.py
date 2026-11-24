"""基础设施 —— langgraph Postgres checkpoint (对话状态持久化)."""

from __future__ import annotations

from contextlib import asynccontextmanager


def checkpoint_dsn() -> str:
    """psycopg 连接串 (langgraph 要求 postgresql:// 裸格式)."""
    from virtual_human.config import settings

    return settings.sqlalchemy_url.replace("postgresql+psycopg://", "postgresql://")


def get_checkpointer():
    """同步 PostgresSaver. 用完 close."""
    from langgraph.checkpoint.postgres import PostgresSaver
    from psycopg import Connection

    conn = Connection.connect(checkpoint_dsn(), autocommit=True, prepare_threshold=0)
    saver = PostgresSaver(conn)
    saver.setup()   # 建表 (幂等)
    return saver


@asynccontextmanager
async def async_checkpointer():
    """AsyncPostgresSaver 上下文 (自动管理 psycopg 连接池).

    AsyncPostgresSaver 本身不支持 async with, 必须由外部关闭底层连接池.
    """
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    from psycopg_pool import AsyncConnectionPool

    pool = AsyncConnectionPool(
        conninfo=checkpoint_dsn(),
        max_size=5,
        open=False,
        kwargs={"autocommit": True, "prepare_threshold": 0},
    )
    await pool.open()
    try:
        saver = AsyncPostgresSaver(pool)
        await saver.setup()
        yield saver
    finally:
        await pool.close()
