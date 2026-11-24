"""基础设施 —— 模拟会话注册表 + checkpoint 线程.

会话生命周期:
    进程内 dict 缓存 PersonaAgent (避免每轮重建 system prompt);
    thread_id 同时落 langgraph checkpoint (Postgres), 重启后可恢复.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any


def make_thread_id(persona_id: str) -> str:
    """会话线程 id: persona 前缀 + uuid, 便于按人物筛选."""
    return f"{persona_id[:8]}-{uuid.uuid4().hex[:12]}"


@dataclass
class ChatSession:
    agent: Any
    thread_id: str


class SessionRegistry:
    """进程内会话表 (简单互斥访问由 asyncio 单线程保证)."""

    def __init__(self) -> None:
        self._sessions: dict[str, ChatSession] = {}

    async def get_or_create(self, persona_id: str, session_id: str | None) -> ChatSession:
        """命中且人物一致则复用, 否则重建 agent."""
        if session_id and session_id in self._sessions:
            s = self._sessions[session_id]
            if s.agent.persona_id == persona_id:
                return s

        from vh_simulate import PersonaAgent

        agent = await PersonaAgent.create(persona_id)
        sid = session_id or make_thread_id(persona_id)
        s = ChatSession(agent=agent, thread_id=sid)
        self._sessions[sid] = s
        return s

    def close(self, session_id: str) -> bool:
        return self._sessions.pop(session_id, None) is not None


registry = SessionRegistry()
