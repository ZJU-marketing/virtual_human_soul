"""life_scaffold_events / life_scaffold_contents —— 人生骨架事件与展开内容的 ORM 模型."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from virtual_human.models import Base, JSONBType, new_id


class LifeScaffoldEvent(Base):
    __tablename__ = "life_scaffold_events"
    __table_args__ = (
        Index("ix_lse_persona_year", "persona_id", "start_year", "ord"),
        Index("ix_lse_pending", "persona_id", "processed"),
        {"comment": "人生骨架事件: 外部 JSON 导入, 按 start_year 排序后由 agent 渐进生成记忆/内容"},
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    persona_id: Mapped[str] = mapped_column(ForeignKey("persona.id", ondelete="CASCADE"))
    ord: Mapped[int] = mapped_column(Integer, default=0)
    event_type: Mapped[str] = mapped_column(String(32))
    start_year: Mapped[int | None] = mapped_column(Integer)
    start_raw: Mapped[str | None] = mapped_column(String(32))
    end_year: Mapped[int | None] = mapped_column(Integer)
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSONBType)
    causes: Mapped[list | dict | None] = mapped_column(JSONBType)
    world_event_ref: Mapped[str | None] = mapped_column(String(64))
    source: Mapped[str | None] = mapped_column(String(32))
    confidence: Mapped[float | None] = mapped_column()
    visibility: Mapped[str | None] = mapped_column(String(32))
    correction_of: Mapped[str | None] = mapped_column(String(36))
    schema_version: Mapped[str | None] = mapped_column(String(16))
    processed: Mapped[bool] = mapped_column(Boolean, default=False)
    sensitive: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    memory_id: Mapped[str | None] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class LifeScaffoldContent(Base):
    """履历事件展开: agent 为每条事件生成的摘要 + 正文 (1:N)."""

    __tablename__ = "life_scaffold_contents"
    __table_args__ = (
        Index("ix_lsc_event", "event_id", "seq"),
        Index("ix_lsc_persona", "persona_id"),
        {"comment": "履历事件展开: agent 为每条 life_scaffold_event 生成的摘要与正文"},
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    event_id: Mapped[str] = mapped_column(
        ForeignKey("life_scaffold_events.id", ondelete="CASCADE")
    )
    persona_id: Mapped[str] = mapped_column(ForeignKey("persona.id", ondelete="CASCADE"))
    seq: Mapped[int] = mapped_column(Integer, default=0)
    summary: Mapped[str | None] = mapped_column(Text)
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
