"""seeder (tool-calling 版) —— LLM 通过调用工具把人设直接写进知识库.

不再让 LLM 输出大 JSON (流式下必然截断), 而是:
    bind_tools(工具集) → 流式思考+调用 → 每次调用即时落库 → 日志实时打印

健壮性:
    - slug 冲突自动重灌 (删旧 persona, 级联清掉全部数据, 事务内安全)
    - 单个工具失败不炸整个会话: 回滚本工具的挂起事务, 错误文本回给 LLM 自行修正
    - LLM 输出实时 chunk 打印 (on_chunk 回调), 工具结果打短日志

主入口: ingest_persona_toolcall(skeleton, on_log, on_chunk) / ingest_persona_toolcall_async(...)
"""

from __future__ import annotations

import sys
from typing import Callable

from langchain_core.messages import AIMessageChunk, HumanMessage, SystemMessage, ToolMessage

from virtual_human.db import async_session_factory

from vh_generate.generate.agent import make_llm
from vh_generate.generate.compression import chars_of, compress_messages
from vh_generate.generate.tools import (
    SeedState,
    TOOL_NAMES,
    TOOL_SCHEMAS,
    execute_tool,
    finalize,
)

LogFn = Callable[..., None]     # 工具结果短日志
ChunkFn = Callable[[str], None]  # LLM 文本 chunk 实时回调


def _stderr_log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def _stdout_chunk(text: str) -> None:
    print(text, end="", flush=True)


SEED_SYSTEM = """你是人生模拟数据录入员。根据用户给的骨架描述, 通过调用工具把一个虚拟人物完整录入知识库。

录入顺序 (严格依次):
1. 先调 CreatePersonaArgs 创建人物主档 (core_identity 用第一人称, 200-400字)
2. 用 AddKbNodeArgs 建知识树节点: 至少 3 个 (章节/观念/习惯各至少1个), importance 按对人物的重要性给
3. 每个节点用 AddContentBlockArgs 加 1-3 块回忆正文 (第一人称, 200-500字, 有具体细节和情绪, 不要概括空话)
4. 大额财务事件: 先 AddMemoryArgs 写经历, 拿到 memory_id 再 AddFinanceEventArgs 记账 (金额要与记忆叙事一致)
5. 全部录完, 输出一句"录入完成"结束

要求:
- 一切内容第一人称、口语化、有生活质感, 避免 AI 腔
- 时间线自洽 (年龄/年份/事件先后)
- 骨架没提的信息可合理虚构, 但要贴合人物身份
- 金额只记大额 (买房/买车/大病/大额收入), 日常开销不入账
- slug 尽量起独特一些 (可加姓氏拼音+职业, 如 chen-zhiqiang-teacher), 避免与他人重复"""


async def _delete_existing_persona(slug: str, session, log: LogFn) -> None:
    """slug 冲突时删旧重灌 (DB 级联清掉节点/内容/记忆/财务), 事务内安全."""
    from sqlalchemy import delete

    from virtual_human.models import Persona

    result = await session.execute(delete(Persona).where(Persona.slug == slug))
    if result.rowcount and result.rowcount > 0:
        log(f"  → slug={slug} 已存在, 删除旧数据重灌 (级联清除 {result.rowcount} 个 persona)")


async def ingest_persona_toolcall_async(
    skeleton: str,
    model: str | None = None,
    on_log: LogFn | None = None,
    on_chunk: ChunkFn | None = None,
    max_turns: int = 40,
) -> str:
    """aio 主入口: 骨架 → LLM 流式调工具 → 落库. 返回 persona_id.

    on_log:   工具结果短日志 (默认 stderr)
    on_chunk: LLM 文本 chunk 实时回调 (默认 stdout 打印, 打字机效果)
    """
    log = on_log or _stderr_log
    emit = on_chunk or _stdout_chunk
    llm = make_llm(model).bind_tools(TOOL_SCHEMAS)

    messages: list = [
        SystemMessage(content=SEED_SYSTEM),
        HumanMessage(content=f"骨架描述:\n{skeleton}"),
    ]

    async with async_session_factory() as session:
        state = SeedState(session=session, log=log)
        ai_msg = None

        for _turn in range(max_turns):
            # 流式接收本回合输出: 文本 chunk 实时外抛, 完整消息留在对话里
            # 压缩中间件: 发给 LLM 前压缩历史 (长工具结果截断头尾), 本体不动
            payload = compress_messages(messages)
            if len(payload) != len(messages) or chars_of(payload) < chars_of(messages):
                log(f"  ◔ 上下文 {chars_of(messages)} → {chars_of(payload)} 字符")
            chunks: list[AIMessageChunk] = []
            async for chunk in llm.astream(payload):
                chunks.append(chunk)
                if chunk.content:
                    emit(chunk.content)   # 实时打印模型说话
            if chunks:
                emit("\n")               # 每回合收尾换行
            ai_msg = _merge_chunks(chunks)
            messages.append(ai_msg)

            tool_calls = ai_msg.tool_calls or []
            if not tool_calls:
                break   # 模型不再调工具 = 录完

            for tc in tool_calls:
                name = tc["name"]
                if name not in TOOL_NAMES:
                    result = f"未知工具: {name}"
                else:
                    # slug 冲突自动重灌: CreatePersona 前清同名旧档
                    if name == "CreatePersonaArgs" and tc.get("args", {}).get("slug"):
                        try:
                            await _delete_existing_persona(tc["args"]["slug"], session, log)
                        except Exception as e:
                            log(f"  → 清旧档失败(忽略): {type(e).__name__}")
                    # 工具内部: 成功即 commit, 失败自行 rollback 并返回错误文本给 LLM 自纠
                    result = await execute_tool(state, name, tc["args"] or {})
                messages.append(ToolMessage(content=str(result), tool_call_id=tc["id"]))

        if state.persona_id is None:
            tail = str(ai_msg.content)[:200] if ai_msg is not None else "(无输出)"
            raise RuntimeError(f"LLM 未创建 persona (轮数用尽或未调工具). 最后输出: {tail}")

        # 收尾提交 (失败则整体回滚, 不留半截数据)
        try:
            await finalize(state)
        except Exception:
            await session.rollback()
            raise
        log(f"  → 提交事务 ✓  persona_id = {state.persona_id}")
        return state.persona_id


def _merge_chunks(chunks: list[AIMessageChunk]) -> AIMessageChunk:
    """合并流式 chunk (langchain 的 + 运算符逐块聚合 tool_call_chunks)."""
    if not chunks:
        raise RuntimeError("LLM 无输出")
    merged = chunks[0]
    for c in chunks[1:]:
        merged = merged + c
    return merged


def ingest_persona_toolcall(
    skeleton: str,
    model: str | None = None,
    on_log: LogFn | None = None,
    on_chunk: ChunkFn | None = None,
) -> str:
    """同步入口: 内部走 aio (run_async)."""
    from virtual_human.db import run_async

    return run_async(ingest_persona_toolcall_async(skeleton, model, on_log, on_chunk))
