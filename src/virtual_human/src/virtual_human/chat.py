"""chat_messages —— 对话 checkpoint (按 session_id 持久化消息)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from virtual_human.models import Base, JSONBType, new_id


class ChatMessage(Base):
    __tablename__ = "chat_messages"
    __table_args__ = (
        CheckConstraint("role IN ('user','assistant','summary')", name="ck_chat_messages_role"),
        Index("ix_chat_messages_session", "session_id", "created_at"),
        Index("ix_chat_messages_persona", "persona_id", "created_at"),
        {"comment": "对话 checkpoint: 按 session_id 持久化消息, 刷新/重启可续聊"},
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    persona_id: Mapped[str] = mapped_column(ForeignKey("persona.id", ondelete="CASCADE"))
    session_id: Mapped[str] = mapped_column(String(64))
    role: Mapped[str] = mapped_column(String(16))      # user/assistant/summary
    content: Mapped[str] = mapped_column(Text)
    recall: Mapped[dict[str, Any] | None] = mapped_column(JSONBType)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
