"""LLM 工具集 —— agent 通过调用这些工具把人设直接写进知识库.

不再走 "LLM 生成大 JSON → 解析 → 落库" (流式输出下 JSON 必然截断报错),
而是把落库动作暴露成 tool, agent 边思考边调用, 每次调用实时落一条数据.

工具清单:
    create_persona      创建人物主档 (必须第一个调)
    add_kb_node         知识树加节点
    add_content_block   节点加内容块 (第一人称回忆正文)
    add_memory          加情景记忆
    add_finance_event   记大额财务事件 (需先有 memory_id)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable

from pydantic import BaseModel, Field

from virtual_human.models import (
    EpisodicMemory,
    FinanceBalance,
    FinanceEvent,
    KbNode,
    KbNodeContent,
    Persona,
)

LogFn = Callable[..., None]


# ---------------------------------------------------------------------
# 工具参数 schema (langchain bind_tools 直接吃 pydantic 类)
# ---------------------------------------------------------------------
class CreatePersonaArgs(BaseModel):
    """创建人物主档。整个录入的第一步, 必须最先调用."""

    slug: str = Field(description="英文标识, 如 chen-zhiqiang")
    name: str = Field(description="姓名")
    core_identity: str = Field(description="我是谁, 第一人称, 200-400字")
    bio_skeleton: str = Field(description="人生大事年表, 200字内")
    voice_style: str = Field(description="语言风格, 50字内")
    initial_cash: float = Field(default=100000, description="当前现金(元)")


class AddKbNodeArgs(BaseModel):
    """在知识树上加一个节点."""

    node_type: str = Field(description="chapter(人生章节) / semantic(观念态度) / procedural(习惯技能)")
    title: str = Field(description="节点标题")
    summary: str = Field(description="50字内摘要, 供骨架加载")
    importance: int = Field(ge=1, le=10, description="重要度")


class AddContentBlockArgs(BaseModel):
    """给节点加一块回忆正文."""

    node_id: str = Field(description="目标节点 id (add_kb_node 的返回值)")
    label: str = Field(description="内容块小标题, 如'买房的心路'")
    content: str = Field(description="第一人称回忆正文, 200-500字, 有细节有情绪")


class AddMemoryArgs(BaseModel):
    """加一条情景记忆 (大额财务事件必须先有记忆)."""

    title: str = Field(description="记忆标题")
    content: str = Field(description="第一人称经历叙事, 150-400字")
    occurred_at: str = Field(description="发生日期 YYYY-MM-DD")
    emotion: str = Field(default="", description="情绪, 如 激动/平静/失落")
    importance: int = Field(default=8, ge=1, le=10)


class AddFinanceEventArgs(BaseModel):
    """记一笔大额财务事件."""

    event_type: str = Field(description="income/expense_buy/expense_live/loan_taken/loan_repay")
    title: str = Field(description="如'买下城东两居室'")
    item_category: str = Field(default="other", description="real_estate/vehicle/medical/education/wedding/other")
    amount: float = Field(gt=0, description="金额(元), 恒为正")
    occurred_at: str = Field(description="YYYY-MM-DD")
    memory_id: str = Field(description="对应记忆的 id (add_memory 的返回值)")


# ---------------------------------------------------------------------
# 修改设定工具 (编辑已有 persona)
# ---------------------------------------------------------------------
class UpdatePersonaArgs(BaseModel):
    """修改人物主档 (身份/骨架/风格)."""

    name: str | None = Field(default=None, description="新姓名 (不改则省略)")
    core_identity: str | None = Field(default=None, description="新身份自述 (不改则省略)")
    bio_skeleton: str | None = Field(default=None, description="新大事年表 (不改则省略)")
    voice_style: str | None = Field(default=None, description="新语言风格 (不改则省略)")


class UpdateNodeArgs(BaseModel):
    """修改知识树节点."""

    node_id: str = Field(description="目标节点 id")
    title: str | None = Field(default=None, description="新标题")
    summary: str | None = Field(default=None, description="新摘要")
    importance: int | None = Field(default=None, ge=1, le=10, description="新重要度")
    disclosure_level: int | None = Field(default=None, ge=0, le=9, description="新披露度: 0隐藏")


class UpdateContentArgs(BaseModel):
    """修改内容块正文."""

    content_id: str = Field(description="目标内容块 id")
    label: str | None = Field(default=None, description="新小标题")
    content: str | None = Field(default=None, description="新正文 (第一人称)")


class DeleteContentArgs(BaseModel):
    """删除一个内容块."""

    content_id: str = Field(description="要删的内容块 id")


class DeleteNodeArgs(BaseModel):
    """删除一个知识节点 (连带其内容块)."""

    node_id: str = Field(description="要删的节点 id")


class UpdateMemoryArgs(BaseModel):
    """修改一条情景记忆."""

    memory_id: str = Field(description="目标记忆 id")
    title: str | None = Field(default=None, description="新标题")
    content: str | None = Field(default=None, description="新叙事")


class DeleteMemoryArgs(BaseModel):
    """删除一条情景记忆 (若被财务事件引用会失败, 需先删事件)."""

    memory_id: str = Field(description="要删的记忆 id")


EDIT_TOOL_SCHEMAS = [
    UpdatePersonaArgs,
    UpdateNodeArgs,
    UpdateContentArgs,
    DeleteContentArgs,
    DeleteNodeArgs,
    UpdateMemoryArgs,
    DeleteMemoryArgs,
]

# 录入用全套 = 建 + 改 (建完发现写错可直接改, 不必重录)
TOOL_SCHEMAS = [
    CreatePersonaArgs,
    AddKbNodeArgs,
    AddContentBlockArgs,
    AddMemoryArgs,
    AddFinanceEventArgs,
    *EDIT_TOOL_SCHEMAS,
]

TOOL_NAMES = {s.__name__ for s in TOOL_SCHEMAS}
EDIT_TOOL_NAMES = {s.__name__ for s in EDIT_TOOL_SCHEMAS}


# ---------------------------------------------------------------------
# 执行状态 + 派发
# ---------------------------------------------------------------------
@dataclass
class SeedState:
    """一次录入/修改过程的共享状态."""

    session: Any                     # AsyncSession
    log: LogFn = field(default=lambda *a, **k: None)
    persona_id: str | None = None
    cash: float = 0.0
    allowed_node_ids: set[str] | None = None   # 定向修改范围 (None=不限)


def _parse_date(s: str) -> datetime:
    return datetime.strptime(s.strip()[:10], "%Y-%m-%d")


async def execute_tool(state: SeedState, name: str, args: dict) -> str:
    """执行一次工具调用, 返回给 LLM 的结果文本 (id 或错误说明).

    每个成功工具即时 commit —— 失败回滚只影响当前工具, 之前已录数据不动.
    定向修改时 (state.allowed_node_ids 非空), 越界节点/内容块一律拒绝.
    """
    session = state.session
    log = state.log
    allowed = state.allowed_node_ids

    async def _node_allowed(node_id: str) -> bool:
        if allowed is None:
            return True
        return node_id in allowed

    async def _content_node_allowed(content_id: str) -> tuple[bool, str | None]:
        """内容块是否在定向范围内; 返回 (ok, node_id)."""
        from virtual_human.models import KbNodeContent as _KNC

        c = await session.get(_KNC, content_id)
        if c is None:
            return False, None
        if allowed is None:
            return True, c.node_id
        return c.node_id in allowed, c.node_id

    try:
        if name == "CreatePersonaArgs":
            p = Persona(
                slug=args["slug"], name=args["name"], status="live",
                core_identity=args["core_identity"],
                bio_skeleton=args["bio_skeleton"],
                voice_style=args["voice_style"],
                context_token_budget=4000,
            )
            session.add(p)
            await session.flush()
            state.persona_id = p.id
            state.cash = float(args.get("initial_cash", 100000))
            await session.commit()
            log(f"  → create_persona {args['name']} (slug={args['slug']}) ✓")
            return f"persona_id={p.id}"

        if state.persona_id is None:
            return "错误: 请先调用 CreatePersonaArgs 创建人物。"

        if name == "AddKbNodeArgs":
            slug = args["title"][:64].replace(" ", "-").replace("，", "").replace(",", "")
            node = KbNode(
                persona_id=state.persona_id, node_type=args["node_type"],
                slug=slug, title=args["title"], summary=args["summary"],
                load_level=2, importance=args["importance"], status="published",
            )
            session.add(node)
            await session.flush()
            await session.commit()
            log(f"  → add_kb_node [{args['node_type']}] {args['title']} ✓")
            return f"node_id={node.id}"

        if name == "AddContentBlockArgs":
            if not await _node_allowed(args["node_id"]):
                return "越界: 该节点不在本次定向修改范围内, 已拒绝"
            from sqlalchemy import func, select

            n = (
                await session.execute(
                    select(func.count()).where(KbNodeContent.node_id == args["node_id"])
                )
            ).scalar() or 0
            c = KbNodeContent(
                node_id=args["node_id"], seq=n + 1,
                label=args["label"], content=args["content"],
                token_estimate=len(args["content"]) // 2,
                status="published",
            )
            session.add(c)
            await session.flush()
            await session.commit()
            log(f"  → add_content_block {args['label'][:20]} ({len(args['content'])}字) ✓")
            return f"content_id={c.id}"

        if name == "AddMemoryArgs":
            m = EpisodicMemory(
                persona_id=state.persona_id, title=args["title"], content=args["content"],
                occurred_at=_parse_date(args["occurred_at"]),
                emotion=args.get("emotion") or None,
                importance=args.get("importance", 8), status="published",
            )
            session.add(m)
            await session.flush()
            await session.commit()
            log(f"  → add_memory 《{args['title']}》{args['occurred_at']} ✓")
            return f"memory_id={m.id}"

        if name == "AddFinanceEventArgs":
            session.add(FinanceEvent(
                persona_id=state.persona_id, event_type=args["event_type"],
                title=args["title"], item_category=args.get("item_category", "other"),
                amount=args["amount"], occurred_at=_parse_date(args["occurred_at"]),
                memory_id=args["memory_id"],
            ))
            await session.flush()
            et = args["event_type"]
            if et in ("expense_buy", "expense_live"):
                state.cash -= args["amount"]
            elif et == "income":
                state.cash += args["amount"]
            await session.commit()
            log(f"  → add_finance_event {args['title']} {args['amount']:,.0f}元 ✓")
            return "ok"

        # ---------------- 编辑已有 persona 的工具 ----------------
        if name == "UpdatePersonaArgs":
            if allowed is not None:
                return "定向修改模式下禁止修改人物主档"
            p = await session.get(Persona, state.persona_id)
            for k in ("name", "core_identity", "bio_skeleton", "voice_style"):
                if args.get(k) is not None:
                    setattr(p, k, args[k])
            await session.commit()
            log(f"  → update_persona (改了 {', '.join(k for k in args if k)}) ✓")
            return "ok"

        if name == "UpdateNodeArgs":
            if not await _node_allowed(args["node_id"]):
                return "越界: 该节点不在本次定向修改范围内, 已拒绝"
            n = await session.get(KbNode, args["node_id"])
            if n is None or n.persona_id != state.persona_id:
                return "错误: node 不存在或不属于当前人物"
            for k in ("title", "summary", "importance", "disclosure_level"):
                if args.get(k) is not None:
                    setattr(n, k, args[k])
            await session.commit()
            log(f"  → update_node {n.title} ✓")
            return "ok"

        if name == "UpdateContentArgs":
            ok, _nid = await _content_node_allowed(args["content_id"])
            if not ok:
                return "越界: 该内容块不在本次定向修改范围内, 已拒绝"
            c = await session.get(KbNodeContent, args["content_id"])
            if c is None:
                return "错误: content 不存在"
            for k in ("label", "content"):
                if args.get(k) is not None:
                    setattr(c, k, args[k])
            if args.get("content") is not None:
                c.token_estimate = len(args["content"]) // 2
            await session.commit()
            log(f"  → update_content {c.label or c.id[:8]} ✓")
            return "ok"

        if name == "DeleteContentArgs":
            ok, _nid = await _content_node_allowed(args["content_id"])
            if not ok:
                return "越界: 该内容块不在本次定向修改范围内, 已拒绝"
            c = await session.get(KbNodeContent, args["content_id"])
            if c is None:
                return "错误: content 不存在"
            label = c.label or c.id[:8]
            await session.delete(c)
            await session.commit()
            log(f"  → delete_content {label} ✓")
            return "ok"

        if name == "DeleteNodeArgs":
            if not await _node_allowed(args["node_id"]):
                return "越界: 该节点不在本次定向修改范围内, 已拒绝"
            n = await session.get(KbNode, args["node_id"])
            if n is None or n.persona_id != state.persona_id:
                return "错误: node 不存在或不属于当前人物"
            title = n.title
            await session.delete(n)   # 级联删内容块
            await session.commit()
            log(f"  → delete_node {title} ✓")
            return "ok"

        if name == "UpdateMemoryArgs":
            m = await session.get(EpisodicMemory, args["memory_id"])
            if m is None or m.persona_id != state.persona_id:
                return "错误: memory 不存在或不属于当前人物"
            for k in ("title", "content"):
                if args.get(k) is not None:
                    setattr(m, k, args[k])
            await session.commit()
            log(f"  → update_memory 《{m.title}》 ✓")
            return "ok"

        if name == "DeleteMemoryArgs":
            m = await session.get(EpisodicMemory, args["memory_id"])
            if m is None or m.persona_id != state.persona_id:
                return "错误: memory 不存在或不属于当前人物"
            title = m.title
            await session.delete(m)
            await session.commit()
            log(f"  → delete_memory 《{title}》 ✓")
            return "ok"

        return f"未知工具: {name}"

    except Exception as e:
        # DB 级失败 (唯一冲突/FK 违反等) 会把事务打进 aborted 状态, 后续 flush 全挂.
        # 策略: 回滚到干净状态. 因为每个成功工具都已即时 commit,
        # 回滚只丢当前这个失败工具自身的写入, 已录入数据不受影响.
        try:
            await session.rollback()
        except Exception:
            pass
        return f"工具执行失败(已回滚本次写入, 之前的数据不受影响): {type(e).__name__}: {str(e)[:120]}。请修正参数重试"


async def finalize(state: SeedState) -> None:
    """录入收尾: 写家底 + 提交事务."""
    if state.persona_id:
        state.session.add(FinanceBalance(
            persona_id=state.persona_id, cash=max(state.cash, 0.0),
        ))
    await state.session.commit()
