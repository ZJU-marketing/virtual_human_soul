"""jobs —— 后台任务表 ORM 模型."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from virtual_human.models import Base, JSONBType, new_id


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        CheckConstraint("job_type IN ('ingest','check','fix','edit','scaffold')", name="ck_jobs_type"),
        CheckConstraint("status IN ('running','done','error')", name="ck_jobs_status"),
        Index("ix_jobs_type_created", "job_type", "created_at"),
        Index("ix_jobs_status", "status"),
        Index("ix_jobs_persona", "persona_id"),
        {"comment": "后台任务: 铸造(persona_id=产物)/质检/修复/修改(persona_id=目标)"},
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    job_type: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20), default="running")
    percent: Mapped[int] = mapped_column(Integer, default=0)
    message: Mapped[str | None] = mapped_column(Text)
    persona_id: Mapped[str | None] = mapped_column(String(36))
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSONBType)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONBType)
    logs: Mapped[list[str] | None] = mapped_column(JSONBType)
    report: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
