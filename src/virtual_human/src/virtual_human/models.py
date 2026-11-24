"""SQLAlchemy 模型 —— 「模拟人类」知识库 (docs/kb_er.md 定稿 schema, sql/schema.sql 落库).

约定:
- ID 均为 varchar(36) UUID 字符串, 应用侧生成 (uuid4).
- 大文本按冷热分离: kb_nodes 无正文, 正文在 kb_node_contents (1:N).
- 向量维度 1024, 模型记录在 kb_embeddings.model.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    JSON,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    REAL,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

# jsonb 列: 其他方言退化为 JSON
JSONBType = JSON().with_variant(JSONB(), "postgresql")


def new_id() -> str:
    return str(uuid.uuid4())


class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------------
# persona —— 模拟对象根档案 (L0)
# ---------------------------------------------------------------------
class Persona(Base):
    __tablename__ = "persona"
    __table_args__ = (
        Index("ux_persona_slug", "slug", unique=True),
        CheckConstraint("status IN ('draft','live','archived')", name="ck_persona_status"),
        {"comment": "模拟对象根档案: core_identity 每次会话必进 system prompt (L0)"},
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    slug: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(20), default="draft")
    core_identity: Mapped[str | None] = mapped_column(Text)          # L0 常驻
    personality: Mapped[str | None] = mapped_column(Text, comment="性格导向提示词: 为空时由 agent 从履历归纳生成")
    bio_skeleton: Mapped[str | None] = mapped_column(Text)           # L1 骨架
    voice_style: Mapped[str | None] = mapped_column(String(512))
    context_token_budget: Mapped[int] = mapped_column(Integer, default=4000)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    kb_nodes: Mapped[list["KbNode"]] = relationship(back_populates="persona")
    memories: Mapped[list["EpisodicMemory"]] = relationship(back_populates="persona")
    finance_events: Mapped[list["FinanceEvent"]] = relationship(
        back_populates="persona", passive_deletes=True
    )
    finance_balance: Mapped["FinanceBalance | None"] = relationship(
        back_populates="persona", passive_deletes=True
    )


# ---------------------------------------------------------------------
# kb_nodes —— 知识树元数据 (热表, 无大文本)
# ---------------------------------------------------------------------
class KbNode(Base):
    __tablename__ = "kb_nodes"
    __table_args__ = (
        Index("ux_kb_nodes_persona_slug", "persona_id", "slug", unique=True),
        CheckConstraint("node_type IN ('identity','chapter','semantic','procedural')", name="ck_kb_nodes_type"),
        CheckConstraint("load_level BETWEEN 0 AND 4", name="ck_kb_nodes_load_level"),
        CheckConstraint("importance BETWEEN 1 AND 10", name="ck_kb_nodes_importance"),
        CheckConstraint("disclosure_level BETWEEN 0 AND 9", name="ck_kb_nodes_disclosure"),
        CheckConstraint("status IN ('draft','published','archived')", name="ck_kb_nodes_status"),
        Index("ix_kb_nodes_persona_id", "persona_id"),
        Index("ix_kb_nodes_parent_id", "parent_id"),
        Index("ix_kb_nodes_persona_type_status", "persona_id", "node_type", "status"),
        {"comment": "知识树节点: 只存 summary (~100 token) 与统计; 全文在 kb_node_contents"},
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    persona_id: Mapped[str] = mapped_column(ForeignKey("persona.id", ondelete="CASCADE"))
    parent_id: Mapped[str | None] = mapped_column(ForeignKey("kb_nodes.id", ondelete="CASCADE"))
    node_type: Mapped[str] = mapped_column(String(20))   # identity/chapter/semantic/procedural
    slug: Mapped[str] = mapped_column(String(128))
    title: Mapped[str] = mapped_column(String(255))
    summary: Mapped[str | None] = mapped_column(Text)    # L1 骨架用, ~100 token
    aliases: Mapped[dict[str, Any] | None] = mapped_column(JSONBType)
    tags: Mapped[dict[str, Any] | None] = mapped_column(JSONBType)
    load_level: Mapped[int] = mapped_column(Integer, default=2)
    importance: Mapped[int] = mapped_column(Integer, default=5)
    disclosure_level: Mapped[int] = mapped_column(Integer, default=5, comment="重要披露度: 0=隐藏, 1-9 越大越优先披露; 简介 API 按此字段决定 summary 是否出现")
    token_estimate: Mapped[int] = mapped_column(Integer, default=0)
    content_count: Mapped[int] = mapped_column(Integer, default=0)
    access_count: Mapped[int] = mapped_column(Integer, default=0)
    last_accessed_at: Mapped[datetime | None] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String(20), default="draft")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    persona: Mapped["Persona"] = relationship(back_populates="kb_nodes")
    parent: Mapped["KbNode | None"] = relationship(remote_side="KbNode.id", backref="children")
    contents: Mapped[list["KbNodeContent"]] = relationship(back_populates="node", order_by="KbNodeContent.seq")
    edges_from: Mapped[list["KbEdge"]] = relationship(foreign_keys="KbEdge.src_node_id", back_populates="src")
    edges_to: Mapped[list["KbEdge"]] = relationship(foreign_keys="KbEdge.dst_node_id", back_populates="dst")


# ---------------------------------------------------------------------
# kb_node_contents —— 节点内容块 (冷表, 1:N, 检索单元=加载单元)
# ---------------------------------------------------------------------
class KbNodeContent(Base):
    __tablename__ = "kb_node_contents"
    __table_args__ = (
        CheckConstraint("content_format IN ('markdown','plaintext')", name="ck_kb_node_contents_format"),
        CheckConstraint("importance BETWEEN 1 AND 10", name="ck_kb_node_contents_importance"),
        CheckConstraint("status IN ('draft','published','archived')", name="ck_kb_node_contents_status"),
        CheckConstraint("valid_from IS NULL OR valid_to IS NULL OR valid_from <= valid_to", name="ck_kb_node_contents_valid_range"),
        Index("ix_kb_node_contents_node_seq", "node_id", "seq"),
        Index("ix_kb_node_contents_node_status", "node_id", "status"),
        {"comment": "节点内容块 1:N: 单块 1-3KB, 命中才 join (L2); valid_from/valid_to 表达观点演变"},
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    node_id: Mapped[str] = mapped_column(ForeignKey("kb_nodes.id", ondelete="CASCADE"))
    seq: Mapped[int] = mapped_column(Integer, default=0)
    label: Mapped[str | None] = mapped_column(String(255))   # 块标题, 如"对金钱的态度"
    content: Mapped[str] = mapped_column(Text)
    content_format: Mapped[str] = mapped_column(String(20), default="markdown")
    token_estimate: Mapped[int] = mapped_column(Integer, default=0)
    importance: Mapped[int] = mapped_column(Integer, default=5)
    access_count: Mapped[int] = mapped_column(Integer, default=0)
    last_accessed_at: Mapped[datetime | None] = mapped_column(DateTime)
    valid_from: Mapped[date | None] = mapped_column(Date)
    valid_to: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(20), default="published")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    node: Mapped["KbNode"] = relationship(back_populates="contents")


# ---------------------------------------------------------------------
# kb_edges —— 节点联想关系
# ---------------------------------------------------------------------
class KbEdge(Base):
    __tablename__ = "kb_edges"
    __table_args__ = (
        Index("ix_kb_edges_src_weight", "src_node_id", text("weight DESC NULLS LAST")),
        Index("ux_kb_edges_triple", "src_node_id", "dst_node_id", "edge_type", unique=True),
        CheckConstraint("edge_type IN ('related','causes','contradicts','elaborates','part_of')", name="ck_kb_edges_type"),
        CheckConstraint("weight BETWEEN 0 AND 1", name="ck_kb_edges_weight"),
        {"comment": "知识点语义关联: 加载 A 时预取 weight 高的关联节点 summary (联想)"},
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    src_node_id: Mapped[str] = mapped_column(ForeignKey("kb_nodes.id", ondelete="CASCADE"))
    dst_node_id: Mapped[str] = mapped_column(ForeignKey("kb_nodes.id", ondelete="CASCADE"))
    edge_type: Mapped[str] = mapped_column(String(20))   # related/causes/contradicts/elaborates/part_of
    weight: Mapped[float] = mapped_column(REAL, default=0.5)
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    src: Mapped["KbNode"] = relationship(foreign_keys=[src_node_id], back_populates="edges_from")
    dst: Mapped["KbNode"] = relationship(foreign_keys=[dst_node_id], back_populates="edges_to")# ---------------------------------------------------------------------
# episodic_memories —— 情景记忆时间轴
# ---------------------------------------------------------------------
class EpisodicMemory(Base):
    __tablename__ = "episodic_memories"
    __table_args__ = (
        CheckConstraint("importance BETWEEN 1 AND 10", name="ck_episodic_memories_importance"),
        CheckConstraint("status IN ('draft','published','archived')", name="ck_episodic_memories_status"),
        CheckConstraint("occurred_end_at IS NULL OR occurred_at IS NULL OR occurred_end_at >= occurred_at", name="ck_episodic_memories_range"),
        Index("ix_episodic_memories_persona_occurred", "persona_id", "occurred_at"),
        Index("ix_episodic_memories_chapter", "chapter_node_id"),
        {"comment": "情景记忆: content 1-5KB inline, 财务大事的叙事面挂在这里"},
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    persona_id: Mapped[str] = mapped_column(ForeignKey("persona.id", ondelete="CASCADE"))
    chapter_node_id: Mapped[str | None] = mapped_column(ForeignKey("kb_nodes.id", ondelete="SET NULL"))
    title: Mapped[str] = mapped_column(String(255))
    content: Mapped[str] = mapped_column(Text)           # 1-5KB inline
    summary: Mapped[str | None] = mapped_column(Text)
    occurred_at: Mapped[datetime | None] = mapped_column(DateTime)
    occurred_end_at: Mapped[datetime | None] = mapped_column(DateTime)
    location: Mapped[str | None] = mapped_column(String(255))
    participants: Mapped[dict[str, Any] | None] = mapped_column(JSONBType)
    emotion: Mapped[str | None] = mapped_column(String(32))
    importance: Mapped[int] = mapped_column(Integer, default=5)
    access_count: Mapped[int] = mapped_column(Integer, default=0)
    last_accessed_at: Mapped[datetime | None] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String(20), default="published")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    persona: Mapped["Persona"] = relationship(back_populates="memories")
    chapter: Mapped["KbNode | None"] = relationship()
    finance_events: Mapped[list["FinanceEvent"]] = relationship(back_populates="memory")


# ---------------------------------------------------------------------
# persona_relations —— 身边的人
# ---------------------------------------------------------------------
class PersonaRelation(Base):
    __tablename__ = "persona_relations"
    __table_args__ = (
        CheckConstraint("closeness IS NULL OR closeness IN ('inner','close','acquaintance')", name="ck_persona_relations_closeness"),
        Index("ix_persona_relations_persona_id", "persona_id"),
        {"comment": "身边的人: 轻量档案, 不强制建 persona"},
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    persona_id: Mapped[str] = mapped_column(ForeignKey("persona.id", ondelete="CASCADE"))
    related_name: Mapped[str] = mapped_column(String(128))
    relation_code: Mapped[str] = mapped_column(String(32))   # father/mother/spouse/friend/mentor...
    profile_summary: Mapped[str | None] = mapped_column(Text)
    closeness: Mapped[str | None] = mapped_column(String(20))
    start_date: Mapped[date | None] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


# ---------------------------------------------------------------------
# kb_embeddings —— 统一向量索引 (content + memory), HNSW
# ---------------------------------------------------------------------
class KbEmbedding(Base):
    __tablename__ = "kb_embeddings"
    __table_args__ = (
        Index("ux_kb_embeddings_entity_model", "entity_type", "entity_id", "model", unique=True),
        CheckConstraint("entity_type IN ('content','memory')", name="ck_kb_embeddings_entity_type"),
        Index("ix_kb_embeddings_embedding", "embedding", postgresql_using="hnsw", postgresql_ops={"embedding": "vector_cosine_ops"}),
        {"comment": "向量索引: entity_type=content(kb_node_contents.id)/memory(episodic_memories.id); 维度 1024"},
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    entity_type: Mapped[str] = mapped_column(String(20))     # content/memory
    entity_id: Mapped[str] = mapped_column(String(36), nullable=False)
    model: Mapped[str] = mapped_column(String(64), nullable=False)
    embedding: Mapped[list[float]] = mapped_column(Vector(1024), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


# ---------------------------------------------------------------------
# memory_access_log —— 想起记录 (衰减/强化驱动)
# ---------------------------------------------------------------------
class MemoryAccessLog(Base):
    __tablename__ = "memory_access_log"
    __table_args__ = (
        Index("ix_memory_access_log_persona_time", "persona_id", text("accessed_at DESC NULLS LAST")),
        Index("ix_memory_access_log_entity", "entity_type", "entity_id"),
        CheckConstraint("entity_type IN ('content','memory')", name="ck_memory_access_log_entity_type"),
        {"comment": "何时想起过什么: 驱动 recency 衰减与 access_count 强化"},
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    persona_id: Mapped[str] = mapped_column(ForeignKey("persona.id", ondelete="CASCADE"))
    entity_type: Mapped[str] = mapped_column(String(20))     # content/memory
    entity_id: Mapped[str] = mapped_column(String(36))
    trigger: Mapped[str | None] = mapped_column(String(32))  # session/retrieval/consolidation
    query_text: Mapped[str | None] = mapped_column(Text)
    load_score: Mapped[float | None] = mapped_column(REAL)
    accessed_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


# ---------------------------------------------------------------------
# finance_events —— 大额事件账
# ---------------------------------------------------------------------
class FinanceEvent(Base):
    __tablename__ = "finance_events"
    __table_args__ = (
        CheckConstraint("event_type IN ('income','expense_buy','expense_live','loan_taken','loan_repay')", name="ck_finance_events_type"),
        CheckConstraint("amount > 0", name="ck_finance_events_amount"),
        Index("ix_finance_events_persona_occurred", "persona_id", "occurred_at"),
        Index("ix_finance_events_memory", "memory_id"),
        {"comment": "大额事件账: 只记人生大事 (买房/买车/彩礼/大病), 每笔必挂情景记忆"},
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    persona_id: Mapped[str] = mapped_column(ForeignKey("persona.id", ondelete="CASCADE"))
    event_type: Mapped[str] = mapped_column(String(20))  # income/expense_buy/expense_live/loan_taken/loan_repay
    title: Mapped[str] = mapped_column(String(255))      # 如"买下城东两居室"
    item_category: Mapped[str | None] = mapped_column(String(32))  # real_estate/vehicle/medical...
    amount: Mapped[float] = mapped_column(Numeric(14, 2))
    currency: Mapped[str] = mapped_column(String(8), default="CNY")
    occurred_at: Mapped[datetime | None] = mapped_column(DateTime)
    balance_after: Mapped[float | None] = mapped_column(Numeric(14, 2))
    memory_id: Mapped[str] = mapped_column(ForeignKey("episodic_memories.id", ondelete="CASCADE"))  # 必挂记忆
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    persona: Mapped["Persona"] = relationship(back_populates="finance_events")
    memory: Mapped["EpisodicMemory"] = relationship(back_populates="finance_events")


# ---------------------------------------------------------------------
# finance_balance —— 当前家底 (一人一行)
# ---------------------------------------------------------------------
class FinanceBalance(Base):
    __tablename__ = "finance_balance"
    __table_args__ = (
        {"comment": "当前家底: net_worth = cash + property_value - liability_balance (查询时算, 不落盘)"},
    )

    persona_id: Mapped[str] = mapped_column(
        ForeignKey("persona.id", ondelete="CASCADE"), primary_key=True
    )
    cash: Mapped[float] = mapped_column(Numeric(14, 2), default=0)
    property_value: Mapped[float] = mapped_column(Numeric(14, 2), default=0)
    liability_balance: Mapped[float] = mapped_column(Numeric(14, 2), default=0)
    currency: Mapped[str] = mapped_column(String(8), default="CNY")
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    persona: Mapped["Persona"] = relationship(back_populates="finance_balance")


# 质检问题记录表 (qc_issues) —— 注册进 Base.metadata 供 alembic autogenerate
from virtual_human import qc as _qc  # noqa: E402,F401,C414

# 后台任务表 (jobs)
from virtual_human import jobs as _jobs  # noqa: E402,F401,C414

# 对话 checkpoint (chat_messages)
from virtual_human import chat as _chat  # noqa: E402,F401,C414

# 人生骨架事件 (life_scaffold_events)
from virtual_human import scaffold as _scaffold  # noqa: E402,F401,C414

from virtual_human.scaffold import LifeScaffoldContent, LifeScaffoldEvent  # noqa: E402,F401
