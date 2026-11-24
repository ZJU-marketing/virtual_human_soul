"""模拟 slug 冲突场景: 冲突后事务不死, LLM 能继续录."""

from virtual_human.db import async_session_factory, run_async
from vh_generate.generate.seeder_tools import _delete_existing_persona
from vh_generate.generate.tools import SeedState, execute_tool


async def main() -> None:
    async with async_session_factory() as session:
        log = print

        await _delete_existing_persona("chen-zhiqiang", session, log)
        state = SeedState(session=session, log=log)

        r1 = await execute_tool(state, "CreatePersonaArgs", {
            "slug": "chen-zhiqiang", "name": "陈志强",
            "core_identity": "我是陈志强, 语文老师。",
            "bio_skeleton": "2018年买房", "voice_style": "温和",
        })
        print("第一次创建:", r1)

        # 同 slug 再建 → 唯一冲突, 验证事务能恢复
        state2 = SeedState(session=session, log=log)
        r2 = await execute_tool(state2, "CreatePersonaArgs", {
            "slug": "chen-zhiqiang", "name": "陈志强2",
            "core_identity": "x", "bio_skeleton": "y", "voice_style": "z",
        })
        print("冲突结果:", r2[:70])

        # 冲突后继续正常写
        r3 = await execute_tool(state, "AddKbNodeArgs", {
            "node_type": "chapter", "title": "教书生涯",
            "summary": "三十年讲台", "importance": 8,
        })
        print("冲突后写节点:", r3)

        # 清理
        await _delete_existing_persona("chen-zhiqiang", session, log)
        await session.commit()
        print("清理完成 ✓")


if __name__ == "__main__":
    run_async(main())
