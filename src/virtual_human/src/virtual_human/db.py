"""数据库连接: 同步与异步 (aio) 双引擎, 配置来自 virtual_human.config.

连接生命周期 (vh.db 日志可见):
    导入本模块 -> 数据源引擎 (连接池) 创建, 但不建物理连接
    首次取 Session -> 连接池新建物理连接, 用后归还池中复用, 不重连

同步 (脚本/CLI 传统路径):
    from virtual_human.db import session_factory
    with session_factory() as session: ...

异步 (asyncio 路径, 与 LLM 流式调用配套):
    from virtual_human.db import async_session_factory, run_async
    async def main():
        async with async_session_factory() as session: ...
    run_async(main())   # Windows 下自动用 SelectorEventLoop (psycopg async 要求)
"""

from __future__ import annotations

import asyncio
import logging
import sys

from sqlalchemy import create_engine, event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import sessionmaker

from virtual_human.config import settings

log = logging.getLogger("vh.db")

# ---- 同步 ----
engine = create_engine(settings.sqlalchemy_url, pool_pre_ping=True)
session_factory = sessionmaker(bind=engine, expire_on_commit=False)

# ---- 异步 (psycopg async) ----
# Windows: psycopg async 不能跑在默认 ProactorEventLoop 上, 用 run_async() 启动
async_engine = create_async_engine(settings.async_sqlalchemy_url, pool_pre_ping=True)
async_session_factory = async_sessionmaker(bind=async_engine, expire_on_commit=False)

# ---- 连接日志 (vh.db): create_engine 是惰性的, 物理连接首次 checkout 才建立 ----
_sync_conns = 0
_async_conns = 0


@event.listens_for(engine, "connect")
def _log_sync_connect(dbapi_conn, connection_record) -> None:
    global _sync_conns
    _sync_conns += 1
    log.info(
        "[sync] 连接池新建物理连接 #%d -> %s", _sync_conns,
        engine.url.render_as_string(hide_password=True),
    )


@event.listens_for(async_engine.sync_engine, "connect")
def _log_async_connect(dbapi_conn, connection_record) -> None:
    global _async_conns
    _async_conns += 1
    log.info(
        "[async] 连接池新建物理连接 #%d -> %s", _async_conns,
        async_engine.sync_engine.url.render_as_string(hide_password=True),
    )


log.info(
    "数据源引擎已创建 (惰性, 首次使用时才真正建连): sync=%s | async=%s",
    engine.url.render_as_string(hide_password=True),
    async_engine.sync_engine.url.render_as_string(hide_password=True),
)


async def dispose_async_engine() -> None:
    """关闭异步连接池 (含 psycopg_pool worker), 供应用 shutdown 时调用.

    不 dispose 时, uvicorn 退出会留下 pending 的 pool worker 任务,
    asyncio 会打印 "Task was destroyed but it is pending!".
    """
    await async_engine.dispose()


def _make_loop() -> asyncio.AbstractEventLoop:
    """Windows 默认 ProactorEventLoop 不支持 psycopg async, 换 Selector."""
    if sys.platform == "win32":
        return asyncio.SelectorEventLoop()
    return asyncio.new_event_loop()


def run_async(coro):
    """在兼容的事件循环上跑异步协程 (Windows 安全)."""
    return asyncio.run(coro, loop_factory=_make_loop)
