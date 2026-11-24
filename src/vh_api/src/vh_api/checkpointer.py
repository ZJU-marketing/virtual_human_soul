"""checkpointer —— LangGraph Postgres checkpoint 持久化.

对话/编排状态 (messages + metadata) 存 PostgreSQL, 跨进程/重启可续跑.
每个 (persona_id, session_id) 一条 thread, API 层用 thread_id 关联.

用法:
    from vh_api.checkpointer import async_checkpointer, make_thread_id

    thread_id = make_thread_id(persona_id)           # 新会话
    async with async_checkpointer() as saver:        # 自动打开/关闭连接池
        graph = compile_scaffold_graph(handlers, checkpointer=saver)
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager

from virtual_human.config import settings


def make_thread_id(persona_id: str) -> str:
    """新会话线程 id: persona 前缀 + uuid, 便于按人物筛选."""
    return f"{persona_id[:8]}-{uuid.uuid4().hex[:12]}"


def _dsn() -> str:
    """langgraph checkpoint 的 psycopg 连接串."""
    return settings.sqlalchemy_url.replace("postgresql+psycopg://", "postgresql://")


def get_checkpointer():
    """同步 checkpoint (langgraph PostgresSaver). 用完需 close()."""
    from langgraph.checkpoint.postgres import PostgresSaver
    from psycopg import Connection

    conn = Connection.connect(_dsn(), autocommit=True, prepare_threshold=0)
    saver = PostgresSaver(conn)
    saver.setup()   # 建表 (幂等)
    return saver


@asynccontextmanager
async def async_checkpointer():
    """异步 checkpoint 上下文: 负责连接池的打开与关闭.

    AsyncPostgresSaver 本身不支持 async with, 必须由这里管理底层
    AsyncConnectionPool, 否则进程退出时会留下 pool-worker 悬挂任务.
    """
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    from psycopg_pool import AsyncConnectionPool

    pool = AsyncConnectionPool(
        conninfo=_dsn(),
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


async def get_async_checkpointer():
    """返回已就绪的 AsyncPostgresSaver (兼容旧用法).

    注意: 调用方必须自行关闭底层连接池 `await saver.conn.close()`;
    推荐改用 `async with async_checkpointer() as saver`.
    """
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    from psycopg_pool import AsyncConnectionPool

    pool = AsyncConnectionPool(
        conninfo=_dsn(),
        max_size=5,
        open=False,
        kwargs={"autocommit": True, "prepare_threshold": 0},
    )
    await pool.open()
    saver = AsyncPostgresSaver(pool)
    await saver.setup()
    return saver
