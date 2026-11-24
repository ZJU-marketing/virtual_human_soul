"""simulate (aio) —— 人物模拟模块, 纯 Python async API.

从 persona_id 初始化可对话的模拟 agent:
  - system 提示词异步组装 (L0 身份 + 披露图谱 + 账本, async_session_factory)
  - 每轮异步检索记忆 (渐进加载), LLM 异步流式生成
  - 上下文压缩中间件: trim_messages 按 token 硬裁剪 + 超限 LLM 摘要压缩

用法:
    from virtual_human.db import run_async
    from vh_simulate import PersonaAgent

    async def main():
        agent = await PersonaAgent.create("6ad0f4ae-...")
        answer = await agent.asay("你还记得买房那年的事吗?")
        async for chunk in agent.stream("后来呢?"):
            print(chunk, end="")

    run_async(main())
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import AsyncIterator, Callable

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
    trim_messages,
)
from langchain_core.messages.utils import count_tokens_approximately
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from pydantic import BaseModel, Field

from vh_generate.generate.agent import make_llm
from vh_simulate.docs_middleware import (
    DOC_TOOL_GUIDE,
    DOC_TOOL_MODELS,
    docs_enabled,
    is_doc_tool,
    run_doc_tool,
)
from virtual_human.config import settings
from virtual_human.db import async_session_factory
from virtual_human.kb.async_api import (
    arender_finance,
    arender_persona_md,
    aretrieve_context,
)
from virtual_human.models import Persona

__all__ = ["PersonaAgent"]

# 事件回调: 每条事件一个 dict
#   {"event": "recall",   "blocks": [...], "used": int, "budget": int}   想起了哪些记忆
#   {"event": "compress", "before": int, "after": int, "summarized": bool}  上下文压缩
#   {"event": "clear"}                                                    清空上一轮事件
RecallEvent = Callable[[dict], None]

# 压缩策略: 历史超过 persona 预算×2.5 触发摘要压缩; 每轮送 LLM 前先 trim 硬裁剪
COMPRESS_RATIO = 2.5
COMPRESS_PROMPT = (
    "把以下人设模拟对话压缩为第三人称要点笔记 (300字内)。"
    "保留: 提到的事实(数字/人名/事件)、未解决的话题、情绪基调。删除: 寒暄、重复。"
)


# ---------------------------------------------------------------------
# system prompt 模板
# ---------------------------------------------------------------------
SIMULATE_SYSTEM = """你正在扮演一个真实的人——不是聊天机器人, 不是助手。

下面是你的记忆资料。你是"回忆起"这些事, 不是"读取"这些资料:
- 用第一人称说话, 带当时的心情
- 记不清的细节就含糊带过 ("大概是那年秋天吧"), 绝不编造精确数字
- 财务数字只允许来自[财务大事记], 别处说的金额都是随口的模糊话
- 你不知道自己是 AI。有人问起就按人物的性情岔开话题

{profile}"""


TOOL_GUIDE = """

## 可用的回忆工具 (按需调用, 渐进式)
你不需要一次把所有资料都读进来。需要细节时再调用:
- LoadSkeletonArgs: 查看人生骨架/知识节点索引
- LoadTreeArgs: 按层级查看知识树 (不传 node 看根层; 传标题/slug 看子节点)
- RetrieveContextArgs: 按问题检索相关的情景记忆与知识内容
- LoadContentArgs: 读取某个知识节点的完整正文 (参数 node 传节点标题或 slug)
- RecallMemoryArgs: 读取一条具体情景记忆 (参数 memory 传标题或 id)

不知道从哪个主题查起时, 先用 LoadTreeArgs 看树, 再 LoadContentArgs 加载节点;
关键词明确时可直接 RetrieveContextArgs。

## L1 / L2 渐进式控制
- L0 = 标题树 (LoadTreeArgs, 最省 token); L1 = 标题 + 摘要; L2 = 完整正文。
- 先 LoadTreeArgs 看清结构; 选中节点后用 LoadContentArgs(level=1) 看摘要, 需要细节再 level=2。
- RetrieveContextArgs 默认 level=1, 先看命中摘要; 选中后再 LoadContentArgs(level=2) 或 RecallMemoryArgs(level=2)。
- 不要默认把大量内容以 L2 拉进来; 先 L0/L1 浏览, 再对少量目标读 L2。"""


class RetrieveContextArgs(BaseModel):
    """按问题检索相关记忆与知识内容 (可控制 L1/L2)."""

    query: str = Field(description="要检索的关键词或完整问题")
    level: int = Field(default=1, description="1=只看摘要(L1), 2=返回全文(L2)")


class LoadContentArgs(BaseModel):
    """读取某个知识节点 (可控制 L1/L2)."""

    node: str = Field(description="知识节点标题或 slug")
    level: int = Field(default=2, description="1=只要摘要(L1), 2=读全文(L2)")


class RecallMemoryArgs(BaseModel):
    """读取一条具体的情景记忆 (可控制 L1/L2)."""

    memory: str = Field(description="记忆标题或 memory id")
    level: int = Field(default=2, description="1=只要摘要(L1), 2=读全文(L2)")


class LoadSkeletonArgs(BaseModel):
    """查看人生骨架与知识节点索引."""

    scope: str = Field(default="all", description="固定填 all")


class LoadTreeArgs(BaseModel):
    """查看知识树的 Markdown 层级结构 (默认只看标题, 最省 token)."""

    node: str = Field(
        default="",
        description="查看某节点子树: 传标题/slug/id; 留空表示整棵树",
    )
    level: int = Field(default=0, description="0=只列标题(默认), 1=标题+短摘要")


TOOL_MODELS = [
    RetrieveContextArgs, LoadContentArgs, RecallMemoryArgs,
    LoadSkeletonArgs, LoadTreeArgs,
]


def _brief_args(args: dict) -> dict:
    """工具参数瘦身: 超长字符串 (如整篇文档 content) 截断, 避免 SSE/轨迹落库膨胀."""
    out: dict = {}
    for k, v in (args or {}).items():
        out[k] = (v[:400] + "…") if isinstance(v, str) and len(v) > 400 else v
    return out


@dataclass
class PersonaAgent:
    """一个已初始化的人物模拟 agent (aio)."""

    persona_id: str
    persona_slug: str
    name: str
    system_prompt: str
    model_name: str = ""     # 空 = 用 settings.llm_model
    history: list[BaseMessage] = field(default_factory=list)
    max_history: int = 20    # 历史窗口 (条)
    on_event: RecallEvent | None = None   # 渐进加载事件回调 (UI 可视化用)

    # ------------------------------------------------------------------
    # 初始化 (异步): persona_id → system prompt
    # ------------------------------------------------------------------
    @classmethod
    async def create(
        cls,
        persona_id: str,
        model: str | None = None,
        min_disclosure: int = 1,
        with_finance: bool = True,
        brief: bool = False,
    ) -> "PersonaAgent":
        """从 persona_id 异步构建模拟 agent.

        brief=True: 常驻上下文只放身份 + 骨架索引 + 财务, 节点/记忆交给工具按需加载.
        """
        async with async_session_factory() as session:
            persona = await session.get(Persona, persona_id)
            if persona is None:
                raise KeyError(f"persona not found: {persona_id}")

            if brief:
                lines: list[str] = []
                if persona.core_identity:
                    lines.append(persona.core_identity)
                if persona.voice_style:
                    lines.append(f"> 说话风格: {persona.voice_style}")
                if persona.bio_skeleton:
                    lines += ["", "## 人生骨架", "", persona.bio_skeleton]
                profile_md = "\n".join(lines).strip()
            else:
                profile_md = await arender_persona_md(
                    session, persona_id,
                    min_disclosure=min_disclosure,
                    include_finance=False,
                )
            finance_md = await arender_finance(session, persona.slug) if with_finance else ""

        sections = [profile_md]
        if finance_md:
            sections.append(f"## 财务账本 (精确数字, 只许引用这里的)\n\n{finance_md}")
        profile = "\n\n".join(sections)

        system_prompt = SIMULATE_SYSTEM.format(profile=profile)

        return cls(
            persona_id=persona_id,
            persona_slug=persona.slug,
            name=persona.name,
            system_prompt=system_prompt,
            model_name=model or "",
        )

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------
    def _emit(self, event: dict) -> None:
        if self.on_event is not None:
            try:
                self.on_event(event)
            except Exception:
                pass   # 事件回调不允许炸主流程

    async def _recall(self, user_input: str) -> str:
        """异步检索记忆, 返回"想起来的事"; 过程发渐进加载事件."""
        async with async_session_factory() as session:
            # 骨架已在 system prompt 中, 这里不再重复装载 (省预算)
            ctx = await aretrieve_context(
                session, self.persona_slug, user_input, with_skeleton=False,
            )

        # 事件: 想起了哪些块 (score/来源/标题/预算)
        self._emit({
            "event": "recall",
            "blocks": [
                {
                    "source": b.source, "title": b.title, "label": b.label,
                    "score": round(b.score, 3), "tokens": b.token_estimate,
                }
                for b in ctx.blocks
            ],
            "used": ctx.used_tokens,
            "budget": ctx.budget,
        })
        return ctx.render()

    def _chain(self):
        llm = make_llm(self.model_name or None)   # 始终流式
        prompt = ChatPromptTemplate.from_messages([
            ("system", "{system_prompt}"),
            MessagesPlaceholder("history"),
            ("system", "[此刻想起来的事]\n{recalled}"),
            ("human", "{input}"),
        ])
        return prompt | llm | StrOutputParser()

    def _trim_history(self) -> list[BaseMessage]:
        """上下文压缩中间件 (LangChain trim_messages):

        1. token 硬裁剪: 历史超过 persona 预算×1.5 → 只保留最近消息 (保 system 性质的头尾)
        2. 条数上限: max_history 双保险
        """
        budget = settings_budget_tokens(self)
        kept = trim_messages(
            self.history,
            token_counter=count_tokens_approximately,
            max_tokens=budget,
            strategy="last",
            allow_partial=False,
            include_system=True,   # 保留压缩后的 [既往对话要点], 否则等于没压
            start_on="human",      # 裁剪后第一条是人类消息, 保持交替结构
        )
        return kept[-self.max_history:]

    async def compress_history_if_needed(self) -> dict:
        """摘要压缩: 历史总 token 超过预算×2.5 时, 把旧消息压成一条笔记 (LLM 调用).

        返回压缩事件 {before, after, summarized}; 未触发时 summarized=False.
        """
        budget = settings_budget_tokens(self)
        limit = int(budget * COMPRESS_RATIO)
        total = count_tokens_approximately(self.history)

        if total <= limit or len(self.history) < 6:
            return {"before": total, "after": total, "summarized": False}

        # 切分: 旧消息(压缩) + 最近 4 条(保留原文)
        keep_recent = 4
        old, recent = self.history[:-keep_recent], self.history[-keep_recent:]

        from vh_generate.generate.agent import make_llm as _mk

        summary_llm = _mk(self.model_name or None)
        text = "\n".join(f"{type(m).__name__}: {m.content}" for m in old)
        note = await summary_llm.ainvoke([
            SystemMessage(content=COMPRESS_PROMPT),
            HumanMessage(content=text[:12000]),
        ])
        note_msg = SystemMessage(content=f"[既往对话要点]\n{note.content}")

        self.history = [note_msg, *recent]
        ev = {"event": "compress", "before": total,
              "after": count_tokens_approximately(self.history), "summarized": True}
        self._emit(ev)
        return ev

    def _record(self, user_input: str, answer: str) -> None:
        self.history.append(HumanMessage(content=user_input))
        self.history.append(AIMessage(content=answer))

    # ------------------------------------------------------------------
    # 对话 (纯 async, LLM 始终流式)
    # 每轮的 recall 事件只在当轮可见: 回答结束即清空 (不累积到下一轮)
    # ------------------------------------------------------------------
    def clear_last_events(self) -> None:
        """清掉上一轮的事件缓存 (对话完成后调用; CLI 每轮答完自动清)."""
        self._emit({"event": "clear"})

    async def _run_tool(self, name: str, args: dict) -> dict:
        """执行一个工具 (回忆工具 / 文档工具), 返回 {text, blocks, summary}."""
        if is_doc_tool(name):
            return await run_doc_tool(name, args, self.persona_id)

        from sqlalchemy import or_, select

        from virtual_human.models import EpisodicMemory, KbNode, KbNodeContent

        async with async_session_factory() as session:
            key = str(args.get("node") or args.get("memory") or args.get("query") or "").strip()

            if name in ("RetrieveContextArgs", "retrieve_context"):
                query = str(args.get("query") or key)
                try:
                    level = int(args.get("level") or 1)
                except (TypeError, ValueError):
                    level = 1
                ctx = await aretrieve_context(
                    session, self.persona_slug, query, with_skeleton=False,
                )
                blocks = [
                    {"source": b.source, "title": b.title, "label": b.label,
                     "score": round(b.score, 3), "tokens": b.token_estimate}
                    for b in ctx.blocks
                ]
                if level >= 2:
                    return {
                        "text": ctx.render() or "(没有检索到相关记忆)",
                        "blocks": blocks,
                        "summary": f"[L2] {len(blocks)} 段 · {ctx.used_tokens} tokens",
                    }

                # L1: 只回摘要 (内容块用节点 summary, 记忆用 memory.summary)
                content_ids = [b.entity_id for b in ctx.blocks if b.source == "content"]
                memory_ids = [b.entity_id for b in ctx.blocks if b.source == "memory"]
                content_meta: dict[str, tuple] = {}
                if content_ids:
                    rows = (
                        await session.execute(
                            select(KbNodeContent.id, KbNode.slug, KbNode.title, KbNode.summary)
                            .join(KbNode, KbNodeContent.node_id == KbNode.id)
                            .where(KbNodeContent.id.in_(content_ids))
                        )
                    ).all()
                    content_meta = {r[0]: (r[1], r[2], r[3]) for r in rows}

                mem_meta: dict[str, tuple] = {}
                if memory_ids:
                    rows = (
                        await session.execute(
                            select(EpisodicMemory.id, EpisodicMemory.title, EpisodicMemory.summary)
                            .where(EpisodicMemory.id.in_(memory_ids))
                        )
                    ).all()
                    mem_meta = {r[0]: (r[1], r[2]) for r in rows}

                lines = ["[检索命中 · L1 摘要] 需要全文时用 LoadContentArgs / RecallMemoryArgs (level=2)"]
                for b in ctx.blocks:
                    if b.source == "content":
                        slug, title, summary = content_meta.get(b.entity_id, ("", b.title, ""))
                        lines.append(
                            f"- [内容] {title or b.title} · {b.label or ''} — "
                            f"{summary or '(无摘要)'} [slug={slug}]"
                        )
                    else:
                        title, summary = mem_meta.get(b.entity_id, (b.title, ""))
                        lines.append(
                            f"- [情景] {title} — {summary or '(无摘要)'} [id={b.entity_id}]"
                        )
                return {
                    "text": "\n".join(lines),
                    "blocks": blocks,
                    "summary": f"[L1] {len(blocks)} 条摘要 · {ctx.used_tokens} tokens",
                }

            if name in ("LoadContentArgs", "load_content"):
                try:
                    level = int(args.get("level") or 2)
                except (TypeError, ValueError):
                    level = 2
                node = (
                    await session.execute(
                        select(KbNode).where(
                            KbNode.persona_id == self.persona_id,
                            or_(
                                KbNode.slug == key,
                                KbNode.title == key,
                                KbNode.title.ilike(f"%{key}%"),
                            ),
                        ).limit(1)
                    )
                ).scalar_one_or_none()
                if node is None:
                    return {"text": f"没有找到知识节点: {key}", "blocks": [], "summary": "未找到节点"}

                if level <= 1:
                    return {
                        "text": (
                            f"[{node.title}] (L1 摘要)\n{node.summary or '(无摘要)'}\n"
                            f"(slug={node.slug}; 需要全文用 level=2)"
                        ),
                        "blocks": [],
                        "summary": f"[L1] {node.title}",
                    }

                contents = (
                    await session.execute(
                        select(KbNodeContent)
                        .where(KbNodeContent.node_id == node.id, KbNodeContent.status == "published")
                        .order_by(KbNodeContent.seq)
                    )
                ).scalars().all()
                text = f"[{node.title}] (L2 全文)\n" + "\n\n".join(c.content for c in contents)
                blocks = [
                    {"source": "content", "title": node.title, "label": c.label,
                     "score": 1.0, "tokens": c.token_estimate}
                    for c in contents
                ]
                return {"text": text, "blocks": blocks, "summary": f"[L2] {len(contents)} 块内容"}

            if name in ("RecallMemoryArgs", "recall_memory"):
                try:
                    level = int(args.get("level") or 2)
                except (TypeError, ValueError):
                    level = 2
                mem = (
                    await session.execute(
                        select(EpisodicMemory).where(
                            EpisodicMemory.persona_id == self.persona_id,
                            or_(
                                EpisodicMemory.id == key,
                                EpisodicMemory.title == key,
                                EpisodicMemory.title.ilike(f"%{key}%"),
                            ),
                        ).limit(1)
                    )
                ).scalar_one_or_none()
                if mem is None:
                    return {"text": f"没有找到记忆: {key}", "blocks": [], "summary": "未找到记忆"}
                when = f"{mem.occurred_at:%Y-%m}" if mem.occurred_at else ""

                if level <= 1:
                    return {
                        "text": f"[{mem.title}] {when} (L1 摘要)\n{mem.summary or '(无摘要)'}\n"
                                f"(id={mem.id}; 需要全文用 level=2)",
                        "blocks": [{"source": "memory", "title": mem.title, "label": None,
                                    "score": 1.0, "tokens": len(mem.summary or "") // 2}],
                        "summary": f"[L1] {mem.title}",
                    }

                return {
                    "text": f"[{mem.title}] {when} (L2 全文)\n{mem.content}",
                    "blocks": [{"source": "memory", "title": mem.title, "label": None,
                                "score": 1.0, "tokens": len(mem.content or "") // 2}],
                    "summary": f"[L2] {mem.title}",
                }

            if name in ("LoadTreeArgs", "load_tree"):
                key = str(args.get("node") or "").strip()
                try:
                    level = int(args.get("level") or 0)
                except (TypeError, ValueError):
                    level = 0
                nodes = (
                    await session.execute(
                        select(KbNode)
                        .where(
                            KbNode.persona_id == self.persona_id,
                            KbNode.status == "published",
                        )
                        .order_by(KbNode.load_level, KbNode.title)
                    )
                ).scalars().all()
                by_id = {n.id: n for n in nodes}
                children_map: dict[str | None, list] = {}
                for n in nodes:
                    pid = n.parent_id if n.parent_id in by_id else None
                    children_map.setdefault(pid, []).append(n)

                parent = None
                if key:
                    parent = next(
                        (n for n in nodes
                         if key in (n.id, n.slug, n.title) or key in (n.title or "")),
                        None,
                    )
                    if parent is None:
                        return {"text": f"没有找到树节点: {key}", "blocks": [], "summary": "未找到节点"}

                lines = [f"# {parent.title} (子树)" if parent is not None else "# 人生骨架"]
                total = [0]

                def render(parent_id: str | None, depth: int) -> None:
                    for n in children_map.get(parent_id, []):
                        summary = ""
                        if level >= 1 and n.summary:
                            s = " ".join((n.summary or "").split())
                            summary = f" — {s[:36]}"
                        lines.append(f"{'  ' * depth}- {n.title}{summary}")
                        total[0] += 1
                        render(n.id, depth + 1)

                if parent is not None:
                    render(parent.id, 0)
                    if total[0] == 0:
                        lines.append("(无子节点)")
                else:
                    render(None, 0)
                    if total[0] == 0:
                        lines.append("(暂无知识节点)")

                return {
                    "text": "\n".join(lines),
                    "blocks": [],
                    "summary": f"{total[0]} 个节点 · L{level}",
                }

            if name in ("LoadSkeletonArgs", "load_skeleton"):
                rows = (
                    await session.execute(
                        select(KbNode.title, KbNode.summary)
                        .where(KbNode.persona_id == self.persona_id, KbNode.status == "published")
                        .order_by(KbNode.load_level, KbNode.title)
                    )
                ).all()
                text = "\n".join(
                    f"- {title}" + (f": {summary}" if summary else "")
                    for title, summary in rows
                ) or "(暂无知识节点)"
                return {"text": text, "blocks": [], "summary": f"{len(rows)} 个节点"}

        return {"text": f"未知工具: {name}", "blocks": [], "summary": "未知工具"}

    async def stream_tools(
        self,
        user_input: str,
        skip_compress: bool = False,
    ) -> AsyncIterator[dict]:
        """渐进式工具调用对话.

        逐个 yield 事件:
          thinking_start / thinking / thinking_end (含耗时)
          tool_start / tool_end (含耗时/参数/结果摘要/命中块)
          chunk (回答正文)
          trace (本轮完整轨迹, 供落库)
        """
        if not skip_compress:
            await self.compress_history_if_needed()

        # 工具集: 记忆/知识回忆 + (RustFS 就绪时) 本人 docs 文档读写
        tools = list(TOOL_MODELS)
        guide = TOOL_GUIDE
        if docs_enabled():
            tools.extend(DOC_TOOL_MODELS)
            guide += DOC_TOOL_GUIDE

        llm = make_llm(self.model_name or None).bind_tools(tools)
        messages: list = [
            SystemMessage(content=self.system_prompt + guide),
            *self._trim_history(),
            HumanMessage(content=user_input),
        ]

        trace: list[dict] = []
        answer_parts: list[str] = []
        answered = False

        for turn in range(1, 6):
            t0 = time.monotonic()
            yield {"type": "thinking_start", "turn": turn}

            chunks: list = []
            reasoning: list[str] = []
            content: list[str] = []

            async for chunk in llm.astream(messages):
                chunks.append(chunk)
                ak = getattr(chunk, "additional_kwargs", None) or {}
                rm = getattr(chunk, "response_metadata", None) or {}
                rc = ""
                if isinstance(ak, dict):
                    rc = ak.get("reasoning_content") or ak.get("reasoning") or ""
                if not rc and isinstance(rm, dict):
                    rc = rm.get("reasoning_content") or ""
                if rc:
                    reasoning.append(str(rc))
                    yield {"type": "thinking", "text": str(rc)}
                if chunk.content:
                    content.append(str(chunk.content))

            if not chunks:
                break

            ai = chunks[0]
            for c in chunks[1:]:
                ai = ai + c
            tool_calls = list(getattr(ai, "tool_calls", None) or [])
            think_ms = int((time.monotonic() - t0) * 1000)

            think_text = "".join(reasoning).strip()
            if tool_calls and not think_text and content:
                think_text = "".join(content).strip()
                yield {"type": "thinking", "text": think_text}

            yield {"type": "thinking_end", "turn": turn, "duration_ms": think_ms, "text": think_text}
            trace.append({
                "type": "thinking", "turn": turn,
                "duration_ms": think_ms, "text": think_text[:4000],
            })

            if not tool_calls:
                answer = "".join(content)
                for i in range(0, len(answer), 24):
                    yield {"type": "chunk", "text": answer[i:i + 24]}
                answer_parts.append(answer)
                answered = True
                break

            messages.append(ai)
            for tc in tool_calls:
                name = tc.get("name") or ""
                args = tc.get("args") or {}
                shown_args = _brief_args(args)
                yield {"type": "tool_start", "id": tc.get("id"), "name": name,
                       "args": shown_args, "turn": turn}
                ts = time.monotonic()
                try:
                    res = await self._run_tool(name, args)
                except Exception as e:  # noqa: BLE001
                    res = {"text": f"工具执行失败: {type(e).__name__}: {e}",
                           "blocks": [], "summary": "执行失败"}
                tool_ms = int((time.monotonic() - ts) * 1000)
                blocks = res.get("blocks") or []
                yield {"type": "tool_end", "id": tc.get("id"), "name": name,
                       "duration_ms": tool_ms, "summary": res.get("summary", ""),
                       "blocks": blocks}
                trace.append({
                    "type": "tool", "name": name, "args": shown_args,
                    "duration_ms": tool_ms, "summary": res.get("summary", ""),
                    "blocks": blocks,
                })
                messages.append(ToolMessage(
                    content=res.get("text") or "", tool_call_id=tc.get("id") or "",
                ))

        if not answered:
            # 工具轮次用尽: 最后一轮不带工具, 直接回答
            messages.append(HumanMessage(content="请直接回答, 不要再调用工具。"))
            final_llm = make_llm(self.model_name or None)
            async for chunk in final_llm.astream(messages):
                text = str(chunk.content or "")
                if text:
                    answer_parts.append(text)
                    yield {"type": "chunk", "text": text}

        answer = "".join(answer_parts)
        if answer:
            self._record(user_input, answer)
        yield {"type": "trace", "trace": trace}

    async def asay(
        self,
        user_input: str,
        recalled: str | None = None,
        skip_compress: bool = False,
    ) -> str:
        """异步单轮: 压缩检查 → 检索 → 流式生成 → 聚合.

        recalled: 调用方已预检索的上下文; 传入时不再重复检索.
        skip_compress: 调用方已完成压缩时跳过 (避免重复 LLM 摘要).
        """
        if not skip_compress:
            await self.compress_history_if_needed()
        if recalled is None:
            recalled = await self._recall(user_input)
        chain = self._chain()
        chunks: list[str] = []
        async for chunk in chain.astream({
            "system_prompt": self.system_prompt,
            "history": self._trim_history(),
            "recalled": recalled,
            "input": user_input,
        }):
            chunks.append(chunk)
        answer = "".join(chunks)
        self._record(user_input, answer)
        self.clear_last_events()   # 本轮结束, 事件不留给下一轮
        return answer

    async def stream(
        self,
        user_input: str,
        recalled: str | None = None,
        skip_compress: bool = False,
    ) -> AsyncIterator[str]:
        """异步流式输出 (打字机) —— 推荐路径. 完整后记历史并清事件.

        recalled: 调用方已预检索的上下文; 传入时不再重复检索.
        skip_compress: 调用方已完成压缩时跳过 (避免重复 LLM 摘要).
        """
        if not skip_compress:
            await self.compress_history_if_needed()
        if recalled is None:
            recalled = await self._recall(user_input)
        chain = self._chain()
        chunks: list[str] = []
        async for chunk in chain.astream({
            "system_prompt": self.system_prompt,
            "history": self._trim_history(),
            "recalled": recalled,
            "input": user_input,
        }):
            chunks.append(chunk)
            yield chunk
        self._record(user_input, "".join(chunks))
        self.clear_last_events()

    # ------------------------------------------------------------------
    # 辅助
    # ------------------------------------------------------------------
    def reset(self) -> None:
        """清空对话历史 (人设不变)."""
        self.history.clear()


def settings_budget_tokens(agent: "PersonaAgent") -> int:
    """历史裁剪预算 (tokens): persona 预算×1.5; agent 未带预算时用 4000×1.5 兜底."""
    budget = getattr(agent, "_budget_tokens", None)
    if budget is None:
        budget = 4000 * 1.5   # 与 seeder 默认 context_token_budget=4000 对齐
    return int(budget)
