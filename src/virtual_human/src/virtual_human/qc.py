"""qc_issues —— 质检问题记录 ORM 模型."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from virtual_human.models import Base, new_id


class QcIssue(Base):
    __tablename__ = "qc_issues"
    __table_args__ = (
        CheckConstraint("severity IN ('high','medium','low')", name="ck_qc_issues_severity"),
        CheckConstraint("status IN ('open','resolved','dismissed')", name="ck_qc_issues_status"),
        CheckConstraint(
            "entity_type IN ('node','content','memory','finance_event','finance_balance','persona')",
            name="ck_qc_issues_entity_type",
        ),
        Index("ix_qc_issues_persona", "persona_id"),
        Index("ix_qc_issues_entity", "entity_type", "entity_id"),
        Index("ix_qc_issues_open", "persona_id", "status"),
        {"comment": "质检问题记录: open=未处理, resolved=已修复, dismissed=忽略"},
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    persona_id: Mapped[str] = mapped_column(ForeignKey("persona.id", ondelete="CASCADE"))
    entity_type: Mapped[str] = mapped_column(String(20))
    entity_id: Mapped[str | None] = mapped_column(String(36))
    entity_title: Mapped[str | None] = mapped_column(String(255))
    severity: Mapped[str] = mapped_column(String(10))       # high/medium/low
    category: Mapped[str | None] = mapped_column(String(32))
    message: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="open")
    checked_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime)
    check_run_id: Mapped[str | None] = mapped_column(String(36))
