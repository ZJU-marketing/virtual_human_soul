"""virtual_human 内置自检实现: 数据源连通性.

实现 HealthCheck 接口, app 启动时注册执行.
"""

from __future__ import annotations

from virtual_human.config import settings
from virtual_human.healthcheck import HealthCheck


class DatabaseCheck(HealthCheck):
    """数据源自检: 异步连接池 SELECT 1 (顺带完成首次物理建连/预热)."""

    name = "database"

    async def check(self) -> str:
        from sqlalchemy import text
        from virtual_human.db import async_engine, async_session_factory

        async with async_session_factory() as session:
            await session.execute(text("SELECT 1"))
        url = async_engine.sync_engine.url.render_as_string(hide_password=True)
        pool = async_engine.sync_engine.pool.status()
        return f"PostgreSQL OK ({url}) 池={pool}"


class ConfigCheck(HealthCheck):
    """配置自检: PostgreSQL 必填项是否存在 (LLM 缺失属正常, 走 mock)."""

    name = "config"

    async def check(self) -> str:
        missing = [
            alias
            for alias, value in (
                ("PGHOST", settings.pg_host),
                ("PGPORT", settings.pg_port),
                ("PGUSER", settings.pg_user),
                ("PGDATABASE", settings.pg_database),
            )
            if not value
        ]
        if missing:
            raise ValueError(f"缺少配置: {', '.join(missing)}")
        llm = "LLM=已配置" if settings.has_llm else "LLM=未配置(mock)"
        return f"PG={settings.pg_host}:{settings.pg_port}/{settings.pg_database} {llm}"
