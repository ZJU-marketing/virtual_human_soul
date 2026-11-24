"""kb.async_api —— 知识库异步 API (aio).

与同步 API (kb.retrieval) 一一对应的 async 版本, 全部跑在 psycopg async + SelectorEventLoop 上.
LLM 流式对话链路请统一用这一层, 避免在 async 上下文里混同步 IO.

用法:
    from virtual_human.db import run_async
    from virtual_human.kb.async_api import aretrieve_context, arender_persona_md

    async def main():
        async with async_session_factory() as session:
            ctx = await aretrieve_context(session, "chen-zhiqiang", "买房那年")
            print(ctx.render())

    run_async(main())
"""

from __future__ import annotations

from typing import AsyncIterator

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from virtual_human.db import async_session_factory
from virtual_human.kb.retrieval import (
    RetrievedContext,
    _embed_query,
    _fill_context,
    _lexical_similarity,
    _load_score,
    load_l0_identity,
)
from virtual_human.models import (
    EpisodicMemory,
    FinanceBalance,
    FinanceEvent,
    KbEdge,
    KbEmbedding,
    KbNode,
    KbNodeContent,
    Persona,
)

__all__ = [
    "aget_persona",
    "aload_l0_identity",
    "aget_node",
    "aget_kb_tree",
    "aget_node_clues",
    "aretrieve_context",
    "arender_finance",
    "arender_persona_md",
    "achat_context",
]


# ---------------------------------------------------------------------
# persona
# ---------------------------------------------------------------------
async def aget_persona(session: AsyncSession, slug: str) -> Persona:
    persona = (
        await session.execute(select(Persona).where(Persona.slug == slug))
    ).scalar_one_or_none()
    if persona is None:
        raise KeyError(f"persona not found: {slug}")
    return persona


async def aget_persona_by_id(session: AsyncSession, persona_id: str) -> Persona:
    persona = await session.get(Persona, persona_id)
    if persona is None:
        raise KeyError(f"persona not found: {persona_id}")
    return persona


async def aload_l0_identity(session: AsyncSession, persona_id: str) -> str:
    """L0: 常驻身份 (异步取 persona 后复用同步渲染)."""
    persona = await aget_persona_by_id(session, persona_id)
    return load_l0_identity(persona)


# ---------------------------------------------------------------------
# 单节点渐进加载 (异步)
# ---------------------------------------------------------------------
async def aget_node(session: AsyncSession, persona_id: str, slug: str, load_level: int = 1) -> dict:
    """get_node 的异步版, 返回 dict 视图.

    load_level=1 → {title, summary}; =2 → 附加 contents 全文列表.
    """
    from virtual_human.kb.retrieval import find_node  # noqa: F401  (同步版)

    if load_level not in (1, 2):
        raise ValueError(f"load_level 必须是 1 或 2, 得到 {load_level}")

    node = (
        await session.execute(
            select(KbNode).where(KbNode.persona_id == persona_id, KbNode.slug == slug)
        )
    ).scalar_one_or_none()
    if node is None:
        raise KeyError(f"node not found: persona_id={persona_id} slug={slug}")

    out: dict = {
        "node_id": node.id,
        "slug": f"{persona_id}/{node.node_type}",
        "title": node.title,
        "node_type": node.node_type,
        "summary": node.summary,
        "importance": node.importance,
    }

    if load_level >= 2:
        rows = (
            (
                await session.execute(
                    select(KbNodeContent)
                    .where(
                        KbNodeContent.node_id == node.id,
                        KbNodeContent.status == "published",
                    )
                    .order_by(KbNodeContent.seq)
                )
            )
            .scalars()
            .all()
        )
        out["contents"] = [
            {
                "id": c.id,
                "seq": c.seq,
                "label": c.label,
                "content": c.content,
                "token_estimate": c.token_estimate,
            }
            for c in rows
        ]

    return out


# ---------------------------------------------------------------------
# 关联线索 (异步)
# ---------------------------------------------------------------------
async def aget_node_clues(session: AsyncSession, persona_id: str, slug: str) -> list[dict]:
    """get_node_clues 的异步版: kb_edges 双向线索."""
    from virtual_human.models import KbEdge

    node = (
        await session.execute(
            select(KbNode).where(KbNode.persona_id == persona_id, KbNode.slug == slug)
        )
    ).scalar_one_or_none()
    if node is None:
        raise KeyError(f"node not found: persona_id={persona_id} slug={slug}")

    out_rows = (
        await session.execute(
            select(KbEdge, KbNode)
            .join(KbNode, KbEdge.dst_node_id == KbNode.id)
            .where(KbEdge.src_node_id == node.id)
            .order_by(KbEdge.weight.desc())
        )
    ).all()
    in_rows = (
        await session.execute(
            select(KbEdge, KbNode)
            .join(KbNode, KbEdge.src_node_id == KbNode.id)
            .where(KbEdge.dst_node_id == node.id)
            .order_by(KbEdge.weight.desc())
        )
    ).all()

    def _clue(direction: str, e: KbEdge, n: KbNode) -> dict:
        return {
            "direction": direction,
            "edge_type": e.edge_type,
            "weight": float(e.weight),
            "neighbor_slug": n.slug,
            "neighbor_title": n.title,
            "neighbor_summary": n.summary,
        }

    return ([_clue("outgoing", e, n) for e, n in out_rows]
            + [_clue("incoming", e, n) for e, n in in_rows])


# ---------------------------------------------------------------------
# 整棵树 (异步, 复用同步组装逻辑)
# ---------------------------------------------------------------------
async def aget_kb_tree(session: AsyncSession, persona_id: str):
    """get_kb_tree 的异步版."""
    from virtual_human.kb.retrieval import get_kb_tree as _sync_tree

    # 树组装是纯内存操作, 只把查询异步化: 拉全部节点交给同步组装器
    nodes = (
        (
            await session.execute(
                select(KbNode)
                .where(KbNode.persona_id == persona_id, KbNode.status == "published")
                .order_by(KbNode.load_level, KbNode.title)
            )
        )
        .scalars()
        .all()
    )
    if not nodes:
        raise KeyError(f"no nodes for persona_id={persona_id}")

    return _build_tree_from_nodes(persona_id, nodes)


def _build_tree_from_nodes(persona_id: str, nodes: list[KbNode]):
    """纯内存树组装 (被同步/异步两版共用)."""
    from virtual_human.kb.retrieval import TreeNode

    by_id = {n.id: n for n in nodes}
    children_map: dict[str | None, list[KbNode]] = {}
    for n in nodes:
        pid = n.parent_id if n.parent_id in by_id else None
        children_map.setdefault(pid, []).append(n)

    def build(n: KbNode) -> "TreeNode":
        return TreeNode(
            slug=f"{persona_id}/{n.node_type}",
            node_id=n.id,
            node_type=n.node_type,
            title=n.title,
            summary=n.summary,
            importance=n.importance,
            parent_id=n.parent_id,
            children=[build(c) for c in children_map.get(n.id, [])],
        )

    return TreeNode(
        slug=f"{persona_id}/root",
        node_id="root",
        node_type="root",
        title=persona_id,
        summary=None,
        importance=0,
        parent_id=None,
        children=[build(n) for n in children_map.get(None, [])],
    )


# ---------------------------------------------------------------------
# 检索 + 预算装载 (异步)
# ---------------------------------------------------------------------
async def aretrieve_context(
    session: AsyncSession,
    persona_slug: str,
    query: str,
    embedding_model: str | None = None,
    top_k: int = 20,
    with_skeleton: bool = True,
) -> RetrievedContext:
    """retrieve_context 的异步版: 向量召回 → 打分 → 预算装载 → 访问记录."""
    from datetime import datetime, timezone

    from virtual_human.config import settings
    from virtual_human.kb.retrieval import MemoryBlock
    from virtual_human.models import MemoryAccessLog

    persona = await aget_persona(session, persona_slug)
    ctx = RetrievedContext(budget=persona.context_token_budget)

    if with_skeleton:
        skeleton_rows = (
            await session.execute(
                select(KbNode.title, KbNode.summary)
                .where(KbNode.persona_id == persona.id, KbNode.status == "published")
                .order_by(KbNode.load_level, KbNode.title)
            )
        ).all()
        skeleton = "\n".join(
            f"- {title}" + (f": {summary}" if summary else "")
            for title, summary in skeleton_rows
        )
        skel_tokens = len(skeleton) // 2
        ctx.used_tokens += skel_tokens
        ctx.blocks.append(MemoryBlock(
            source="summary", entity_id=persona.id, title="人生骨架", label=None,
            text=skeleton, token_estimate=skel_tokens, score=1.0,
        ))

    # 向量召回 (可选: VH_EMBED_ENABLED=true 且注册了 embedder 才走; 默认结构化降级)
    # 嵌入函数由上层 vh_generate 通过 virtual_human.kb.retrieval.register_embedder 注入,
    # 知识库层不 import 生成模块 —— 依赖保持单向.
    hits: list[tuple[str, str, float]] = []

    if settings.embed_enabled:
        import asyncio as _aio

        from pgvector.sqlalchemy import Vector as _Vec
        from sqlalchemy import type_coerce as _tc

        from virtual_human.kb.retrieval import _embed_query

        model = embedding_model or settings.embed_model
        query_vec = await _aio.to_thread(_embed_query, query, model)

        if query_vec is not None:
            qvec = _tc(query_vec, _Vec)
            hit_rows = (
                await session.execute(
                    select(
                        KbEmbedding.entity_type,
                        KbEmbedding.entity_id,
                        (1 - KbEmbedding.embedding.cosine_distance(qvec)).label("sim"),
                    )
                    .where(KbEmbedding.model == model)
                    .order_by(KbEmbedding.embedding.cosine_distance(qvec))
                    .limit(top_k)
                )
            ).all()
            hits = [(r[0], r[1], float(r[2])) for r in hit_rows]

    candidates = []
    content_ids = [eid for etype, eid, _ in hits if etype == "content"]
    memory_ids = [eid for etype, eid, _ in hits if etype == "memory"]

    if hits and content_ids:
        rows = (
            await session.execute(
                select(KbNodeContent, KbNode)
                .join(KbNode, KbNodeContent.node_id == KbNode.id)
                .where(
                    KbNodeContent.id.in_(content_ids),
                    KbNodeContent.status == "published",
                    KbNode.load_level >= 2,
                )
            )
        ).all()
        sim_by_id = {eid: s for etype, eid, s in hits if etype == "content"}
        for content, node in rows:
            candidates.append(("content", content.id, node.title, content.label,
                               content.content, content.token_estimate,
                               _load_score(sim_by_id.get(content.id, 0.0), content.importance,
                                           content.last_accessed_at, content.access_count)))

    if hits and memory_ids:
        rows = (
            await session.execute(
                select(EpisodicMemory)
                .where(
                    EpisodicMemory.id.in_(memory_ids),
                    EpisodicMemory.status == "published",
                )
            )
        ).scalars().all()
        sim_by_id = {eid: s for etype, eid, s in hits if etype == "memory"}
        for mem in rows:
            candidates.append(("memory", mem.id, mem.title, None, mem.content,
                               len(mem.content) // 2,
                               _load_score(sim_by_id.get(mem.id, 0.0), mem.importance,
                                           mem.last_accessed_at, mem.access_count)))

    # 无向量时的降级候选: 该 persona 全部 published 内容块/记忆, 结构化打分 (无相似度项)
    if not hits:
        rows = (
            await session.execute(
                select(KbNodeContent, KbNode)
                .join(KbNode, KbNodeContent.node_id == KbNode.id)
                .where(
                    KbNode.persona_id == persona.id,
                    KbNodeContent.status == "published",
                    KbNode.load_level >= 2,
                )
            )
        ).all()
        for content, node in rows:
            candidates.append(("content", content.id, node.title, content.label,
                               content.content, content.token_estimate,
                               _load_score(
                                   _lexical_similarity(query, node.title, content.label, content.content),
                                   content.importance,
                                   content.last_accessed_at, content.access_count,
                               )))

        mems = (
            await session.execute(
                select(EpisodicMemory)
                .where(
                    EpisodicMemory.persona_id == persona.id,
                    EpisodicMemory.status == "published",
                )
            )
        ).scalars().all()
        for mem in mems:
            candidates.append(("memory", mem.id, mem.title, None, mem.content,
                               len(mem.content) // 2,
                               _load_score(
                                   _lexical_similarity(query, mem.title, None, mem.content),
                                   mem.importance,
                                   mem.last_accessed_at, mem.access_count,
                               )))

    # 预算装载: 相对阈值 + 每类配额 + 近重复过滤
    blocks = _fill_context(ctx, [
        MemoryBlock(source=source, entity_id=eid, title=title, label=label,
                    text=text, token_estimate=tokens, score=score)
        for source, eid, title, label, text, tokens, score in candidates
    ])

    # 访问记录 (块级/节点级联动)
    now = datetime.now(timezone.utc)
    content_ids_hit = [b.entity_id for b in blocks if b.source == "content"]
    memory_ids_hit = [b.entity_id for b in blocks if b.source == "memory"]

    if content_ids_hit:
        await session.execute(
            update(KbNodeContent)
            .where(KbNodeContent.id.in_(content_ids_hit))
            .values(access_count=KbNodeContent.access_count + 1, last_accessed_at=now)
        )
        node_ids = (
            await session.execute(
                select(KbNodeContent.node_id).where(KbNodeContent.id.in_(content_ids_hit))
            )
        ).scalars().all()
        await session.execute(
            update(KbNode)
            .where(KbNode.id.in_(node_ids))
            .values(access_count=KbNode.access_count + 1, last_accessed_at=now)
        )
    if memory_ids_hit:
        await session.execute(
            update(EpisodicMemory)
            .where(EpisodicMemory.id.in_(memory_ids_hit))
            .values(access_count=EpisodicMemory.access_count + 1, last_accessed_at=now)
        )

    for b in blocks:
        if b.source == "summary":
            continue
        session.add(MemoryAccessLog(
            persona_id=persona.id, entity_type=b.source, entity_id=b.entity_id,
            trigger="retrieval", query_text=query, load_score=b.score,
        ))
    await session.commit()

    return ctx


# ---------------------------------------------------------------------
# 账本 / 简介 (异步)
# ---------------------------------------------------------------------
async def arender_finance(session: AsyncSession, persona_slug: str) -> str:
    """账本概览 (异步查询, 复用渲染)."""
    persona = await aget_persona(session, persona_slug)
    events = (
        (
            await session.execute(
                select(FinanceEvent)
                .where(FinanceEvent.persona_id == persona.id)
                .order_by(FinanceEvent.occurred_at)
            )
        )
        .scalars()
        .all()
    )
    bal = await session.get(FinanceBalance, persona.id)

    lines = ["[财务大事记]"]
    for e in events:
        lines.append(f"- {e.occurred_at:%Y-%m} {e.title}: {e.event_type} {e.amount:,.0f}元")
    if bal is not None:
        net = float(bal.cash) + float(bal.property_value) - float(bal.liability_balance)
        lines.append(
            f"[当前家底] 现金 {bal.cash:,.0f} / 大件现值 {bal.property_value:,.0f} "
            f"/ 负债 {bal.liability_balance:,.0f} / 净资产 {net:,.0f}"
        )
    return "\n".join(lines)


async def arender_persona_md(
    session: AsyncSession,
    persona_id: str,
    min_disclosure: int = 1,
    include_finance: bool = True,
) -> str:
    """Markdown 人物简介 (异步查询 + 同步渲染)."""
    persona = await aget_persona_by_id(session, persona_id)

    # 拉数据 (异步) → 渲染 (内存)
    nodes = (
        (
            await session.execute(
                select(KbNode)
                .where(KbNode.persona_id == persona_id, KbNode.status == "published")
                .order_by(KbNode.load_level, KbNode.title)
            )
        )
        .scalars()
        .all()
    )
    by_id = {n.id: n for n in nodes}

    lines: list[str] = []
    lines.append(f"# {persona.name}")
    lines.append("")
    if persona.core_identity:
        lines.append(persona.core_identity)
        lines.append("")
    if persona.voice_style:
        lines.append(f"> 说话风格: {persona.voice_style}")
        lines.append("")
    # L1 人生骨架 = 知识节点标题索引 (<slug> 引导 agent 按 slug 继续探索)
    lines.append("## 人生骨架")
    lines.append("")
    if nodes:
        for n in sorted(nodes, key=lambda x: (-x.importance, x.title)):
            lines.append(f'- <slug id="{persona_id}/{n.slug}">{n.title}</slug>')
    else:
        lines.append("（暂无知识节点）")
    lines.append("")

    lines.append("## 记忆图谱")
    lines.append("")
    for n in sorted(nodes, key=lambda x: (-x.importance, x.title)):
        lines.append(f"### {n.title}")
        lines.append("")
        d = n.disclosure_level
        slug_tag = f"{persona_id}/{n.node_type}"
        if d >= min_disclosure and d > 0 and n.summary:
            lines.append(f'<slug id="{slug_tag}">{n.summary}</slug>')
        else:
            lines.append(f'<slug id="{slug_tag}" disclosed="false"/>')
        lines.append("")

    if include_finance:
        finance_md = await arender_finance(session, persona.slug)
        if finance_md:
            lines.append("## 财务大事记")
            lines.append("")
            lines.append(finance_md)
            lines.append("")

    return "\n".join(lines).strip() + "\n"


# ---------------------------------------------------------------------
# 一站式: 组装 system prompt 上下文 (异步)
# ---------------------------------------------------------------------
async def achat_context(session: AsyncSession, persona_slug: str, user_input: str) -> str:
    """给对话 agent 的一次性异步取数: L0 + 检索 + 账本 → system prompt 片段."""
    persona = await aget_persona(session, persona_slug)
    identity = load_l0_identity(persona)
    ctx = await aretrieve_context(session, persona_slug, user_input)
    finance = await arender_finance(session, persona_slug)

    remembered = ctx.render()
    sections = [
        "你正在扮演一个真实的人。以下是你的记忆与经历, 不是资料——要用第一人称自然地回忆, 不确定就说记不清, 绝不编造具体数字。",
        f"\n[你是谁]\n{identity}",
    ]
    if remembered:
        sections.append(f"\n[想起来的事]\n{remembered}")
    sections.append(f"\n[你的财务大事 (精确账, 引用数字以此为准)]\n{finance}")
    sections.append("\n[表达要求]\n- 用口语回忆往事, 可以带情绪\n- 财务数字只说账本里有的\n- 账本之外的日常开销, 按你的性格即兴描述, 不给具体金额")
    return "\n".join(sections)
