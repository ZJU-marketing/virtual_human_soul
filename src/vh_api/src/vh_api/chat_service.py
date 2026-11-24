"""对话 checkpoint 服务.

职责:
    - 按 session_id 持久化/恢复消息 (chat_messages 表)
    - 恢复历史时按预算压缩 (超长 → LLM 摘要成一条 summary)
    - 记录每轮"本轮调用的能力"(tools) 与记忆装载明细
"""

from __future__ import annotations

from sqlalchemy import delete, select

from virtual_human.chat import ChatMessage
from virtual_human.db import async_session_factory


async def load_history(persona_id: str, session_id: str) -> list[dict]:
    """读回会话消息 (按时间升序), 含 summary 占位."""
    async with async_session_factory() as session:
        rows = (
            (
                await session.execute(
                    select(ChatMessage)
                    .where(ChatMessage.persona_id == persona_id, ChatMessage.session_id == session_id)
                    .order_by(ChatMessage.created_at)
                )
            )
            .scalars()
            .all()
        )
        return [
            {
                "id": r.id,
                "role": r.role,
                "content": r.content,
                "recall": r.recall,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ]


async def append_message(
    persona_id: str,
    session_id: str,
    role: str,
    content: str,
    recall: dict | None = None,
) -> str:
    async with async_session_factory() as session:
        row = ChatMessage(
            persona_id=persona_id, session_id=session_id,
            role=role, content=content, recall=recall,
        )
        session.add(row)
        await session.commit()
        return row.id


async def replace_with_summary(persona_id: str, session_id: str, summary: str) -> int:
    """把旧消息折叠成一条 summary (压缩后清理原消息). 返回删除条数."""
    async with async_session_factory() as session:
        res = await session.execute(
            delete(ChatMessage).where(
                ChatMessage.persona_id == persona_id,
                ChatMessage.session_id == session_id,
            )
        )
        session.add(ChatMessage(
            persona_id=persona_id, session_id=session_id,
            role="summary", content=summary,
        ))
        await session.commit()
        return res.rowcount or 0


async def latest_session_id(persona_id: str) -> str | None:
    """最近一次对话的 session_id (用于进入页面时恢复)."""
    async with async_session_factory() as session:
        row = (
            await session.execute(
                select(ChatMessage.session_id)
                .where(ChatMessage.persona_id == persona_id)
                .order_by(ChatMessage.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        return row


async def clear_session(persona_id: str, session_id: str) -> int:
    async with async_session_factory() as session:
        res = await session.execute(
            delete(ChatMessage).where(
                ChatMessage.persona_id == persona_id,
                ChatMessage.session_id == session_id,
            )
        )
        await session.commit()
        return res.rowcount or 0
