"""应用服务 —— 人物查询 (persona 上下文).

编排 virtual_human.kb 的查询 API: 列表 / 知识树 / MD 简介.
"""

from __future__ import annotations

from sqlalchemy import select

from virtual_human.db import async_session_factory
from virtual_human.models import Persona


async def list_personas() -> list[dict]:
    """全部人物 (id/slug/name/status)."""
    async with async_session_factory() as session:
        rows = (await session.execute(select(Persona))).scalars().all()
        return [
            {"id": p.id, "slug": p.slug, "name": p.name, "status": p.status}
            for p in rows
        ]


async def find_persona(pid_or_slug: str) -> Persona | None:
    """按 id 或 slug 找人物."""
    from sqlalchemy import or_

    async with async_session_factory() as session:
        q = select(Persona).where(
            or_(Persona.id == pid_or_slug, Persona.slug == pid_or_slug)
        )
        return (await session.execute(q)).scalars().first()


async def get_tree(persona_id: str) -> dict:
    """知识树 (渐进加载的 L1 视图: 全部节点 summary)."""
    from virtual_human.kb.async_api import aget_kb_tree

    async with async_session_factory() as session:
        tree = await aget_kb_tree(session, persona_id)

        def node(t) -> dict:
            return {
                "slug": t.slug, "node_id": t.node_id, "node_type": t.node_type,
                "title": t.title, "summary": t.summary, "importance": t.importance,
                "children": [node(c) for c in t.children],
            }

        return {"slug": tree.slug, "children": [node(c) for c in tree.children]}


async def get_profile(persona_id: str, min_disclosure: int = 1) -> str:
    """Markdown 人物简介 (summary 带 <slug> 标签, 按披露度过滤)."""
    from virtual_human.kb.async_api import arender_persona_md

    async with async_session_factory() as session:
        return await arender_persona_md(
            session, persona_id, min_disclosure=min_disclosure, include_finance=True
        )
