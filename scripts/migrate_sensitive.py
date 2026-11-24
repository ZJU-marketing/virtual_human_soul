"""应用 sensitive_flag.sql 迁移: life_scaffold_events 加 sensitive 列."""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src" / "virtual_human" / "src"))

from virtual_human.db import run_async  # noqa: E402


async def main() -> None:
    from sqlalchemy import text

    from virtual_human.db import async_engine

    sql = (Path(__file__).resolve().parents[1] / "sql" / "sensitive_flag.sql").read_text(encoding="utf-8")
    async with async_engine.begin() as conn:
        await conn.execute(text(sql))
    print("migration applied: life_scaffold_events.sensitive")


run_async(main())
