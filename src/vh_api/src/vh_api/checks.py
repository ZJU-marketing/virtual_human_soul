"""vh_api 自检实现: checkpoint 存储连通性.

实现 HealthCheck 接口, app 启动时注册执行.
"""

from __future__ import annotations

from virtual_human.config import settings
from virtual_human.healthcheck import HealthCheck


class CheckpointerCheck(HealthCheck):
    """对话 checkpoint 自检: psycopg 连接探测 (与 LangGraph 共用同一 PG)."""

    name = "checkpointer"

    async def check(self) -> str:
        from psycopg import AsyncConnection

        from vh_api.checkpointer import _dsn

        conn = await AsyncConnection.connect(_dsn(), connect_timeout=3)
        try:
            async with conn.cursor() as cur:
                await cur.execute("SELECT 1")
                await cur.fetchone()
        finally:
            await conn.close()
        return f"checkpoint 存储 OK ({settings.pg_host}:{settings.pg_port}/{settings.pg_database})"


class StorageCheck(HealthCheck):
    """agent 文档对象存储自检: RustFS bucket 可用 (未配置则跳过)."""

    name = "storage"

    async def check(self) -> str:
        from virtual_human import storage

        if not settings.object_storage_enabled:
            return "未配置 (RUSTFS_ENDPOINT/ACCESS_KEY 为空, agent 文档工具关闭)"
        await storage.aensure_bucket()
        metas = await storage.alist_docs("__selfcheck__")
        return (
            f"RustFS OK ({settings.rustfs_endpoint} bucket={settings.rustfs_bucket}, "
            f"{len(metas)} 个对象)"
        )
