"""persona 编辑器 —— 输入一句修改要求, LLM 检查设定并调工具修改.

流程:
    1. 读出人物全部设定 (主档 + 知识树 + 内容块 + 记忆) 压缩成"设定清单"喂给 LLM
    2. LLM 对照修改要求, 决定要改/删/加什么, 逐个调编辑工具 (实时落库+日志)
    3. 只做必要修改, 其他数据不动

主入口: edit_persona_async(persona_id, instruction, on_log)
"""

from __future__ import annotations

import sys
from typing import Callable

from langchain_core.messages import AIMessageChunk, HumanMessage, SystemMessage, ToolMessage
from sqlalchemy import select

from virtual_human.db import async_session_factory
from virtual_human.models import (
    EpisodicMemory,
    KbNode,
    KbNodeContent,
    Persona,
)

from vh_generate.generate.agent import make_llm
from vh_generate.generate.tools import (
    SeedState,
    TOOL_NAMES,
    TOOL_SCHEMAS,
    execute_tool,
)

LogFn = Callable[..., None]


def _stderr_log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


EDITOR_SYSTEM = """你是人物设定编辑员。用户会给出一句修改要求, 你对照下面的"当前设定清单"检查, 然后调用工具完成修改。

规则:
1. 只做用户要求相关的必要修改, 其他数据一律不动
2. 修改正文/身份时保持第一人称、口语化、贴合原人物的年代感和性格
3. 发现设定与要求矛盾 (如年龄对不上、时间线冲突), 一并修正并说明
4. 需要删数据时先确认不被引用 (记忆被财务事件引用时不能删)
5. 修改完成输出一段简短总结: 改了什么、为什么

可用工具: UpdatePersonaArgs / UpdateNodeArgs / UpdateContentArgs / DeleteContentArgs /
          DeleteNodeArgs / UpdateMemoryArgs / DeleteMemoryArgs /
          AddKbNodeArgs / AddContentBlockArgs / AddMemoryArgs / AddFinanceEventArgs
(创建新内容时用 Add 系列; CreatePersonaArgs 不需要, 人物已存在)"""


async def _dump_persona(session, persona_id: str) -> str:
    """把人物全部设定压缩成 LLM 可读的清单 (带各对象的 id)."""
    p = await session.get(Persona, persona_id)
    if p is None:
        raise KeyError(f"persona not found: {persona_id}")

    lines = [
        f"# 人物: {p.name} (slug={p.slug})",
        f"身份自述: {p.core_identity}",
        f"大事年表: {p.bio_skeleton}",
        f"语言风格: {p.voice_style}",
        "",
        "## 知识树",
    ]
    nodes = (
        (
            await session.execute(
                select(KbNode).where(KbNode.persona_id == persona_id).order_by(KbNode.created_at)
            )
        )
        .scalars()
        .all()
    )
    node_ids = []
    for n in nodes:
        node_ids.append(n.id)
        lines.append(f"- node_id={n.id} [{n.node_type}] {n.title} (imp={n.importance}, 披露={n.disclosure_level})")
        lines.append(f"  摘要: {n.summary}")
        contents = (
            (
                await session.execute(
                    select(KbNodeContent)
                    .where(KbNodeContent.node_id == n.id)
                    .order_by(KbNodeContent.seq)
                )
            )
            .scalars()
            .all()
        )
        for c in contents:
            preview = (c.content or "")[:100].replace("\n", " ")
            lines.append(f"  · content_id={c.id} ({c.label}) {len(c.content or '')}字: {preview}...")

    lines.append("")
    lines.append("## 情景记忆")
    mems = (
        (
            await session.execute(
                select(EpisodicMemory)
                .where(EpisodicMemory.persona_id == persona_id)
                .order_by(EpisodicMemory.occurred_at)
            )
        )
        .scalars()
        .all()
    )
    for m in mems:
        preview = (m.content or "")[:100].replace("\n", " ")
        lines.append(
            f"- memory_id={m.id} {m.occurred_at:%Y-%m-%d} 《{m.title}》 [{m.emotion}]: {preview}..."
        )

    return "\n".join(lines)


async def edit_persona_async(
    persona_id: str,
    instruction: str,
    model: str | None = None,
    node_ids: list[str] | None = None,
    on_log: LogFn | None = None,
    on_chunk: Callable[[str], None] | None = None,
    on_progress: Callable[[int, int], None] | None = None,
    max_turns: int = 20,
) -> bool:
    """按一句修改要求编辑人物.

    node_ids: 定向修改范围 (为空=不限, 可改整个人物);
              非空时只允许改这些节点及其内容块, 其余节点工具调用会被拒绝.
    """
    log = on_log or _stderr_log
    emit = on_chunk or (lambda t: None)
    llm = make_llm(model).bind_tools(TOOL_SCHEMAS)

    # 定向范围: 只允许改这些节点 (及其内容块)
    allowed_nodes = set(node_ids) if node_ids else None

    async with async_session_factory() as session:
        dump = await _dump_persona(session, persona_id)
        state = SeedState(session=session, log=log)
        state.persona_id = persona_id
        state.allowed_node_ids = allowed_nodes     # tools 层据此拒绝越界修改

        scope = ""
        if allowed_nodes:
            # 收集范围内节点的标题, 写进 prompt 让 LLM 明确范围
            from sqlalchemy import select as _sel

            from virtual_human.models import KbNode as _KbNode

            rows = (
                (
                    await session.execute(
                        _sel(_KbNode.id, _KbNode.title, _KbNode.node_type).where(
                            _KbNode.persona_id == persona_id, _KbNode.id.in_(allowed_nodes)
                        )
                    )
                )
                .all()
            )
            scope_lines = "\n".join(f"- id={r[0]} [{r[2]}] {r[1]}" for r in rows)
            scope = (
                f"\n\n【本次修改范围 (定向)】只能修改以下 {len(rows)} 个节点及其内容块, "
                f"禁止改动其它节点/人物主档/记忆/财务:\n{scope_lines}\n"
            )

        messages: list = [
            SystemMessage(content=EDITOR_SYSTEM + ("\n\n注意: 本次是定向修改, 严格限制在下述节点范围内。" if allowed_nodes else "")),
            HumanMessage(content=f"当前设定清单:\n\n{dump}{scope}\n\n修改要求: {instruction}"),
        ]

        import logging as _logging
        import time as _time

        _log = _logging.getLogger("vh.llm")

        for _turn in range(max_turns):
            chunks: list[AIMessageChunk] = []
            _t0 = _time.time()
            _log.info("[edit] LLM turn=%d 请求中… model=%s", _turn + 1, model or "default")
            async for chunk in llm.astream(messages):
                chunks.append(chunk)
                if chunk.content:
                    emit(chunk.content)
            if chunks:
                emit("\n")
            ai_msg = _merge(chunks)
            messages.append(ai_msg)
            _log.info(
                "[edit] LLM turn=%d 完成 %.2fs, tool_calls=%d",
                _turn + 1, _time.time() - _t0, len(ai_msg.tool_calls or []),
            )
            if on_progress:
                on_progress(_turn + 1, max_turns)

            tool_calls = ai_msg.tool_calls or []
            if not tool_calls:
                return True   # 模型说完了 = 修改完成

            for tc in tool_calls:
                name = tc["name"]
                if name not in TOOL_NAMES or name == "CreatePersonaArgs":
                    result = f"该工具不可用: {name}"
                else:
                    result = await execute_tool(state, name, tc["args"] or {})
                messages.append(ToolMessage(content=str(result), tool_call_id=tc["id"]))

        return False   # 轮数用尽


def _merge(chunks: list[AIMessageChunk]) -> AIMessageChunk:
    merged = chunks[0]
    for c in chunks[1:]:
        merged = merged + c
    return merged
