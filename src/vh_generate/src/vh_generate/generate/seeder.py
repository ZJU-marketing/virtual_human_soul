"""persona 种子生成 —— 输入一段骨架描述, LLM 生成完整人设并灌库.

流程 (ingest):
  骨架描述 (用户输入)
    → LLM 结构化生成: 身份/知识树(章节+内容块)/情景记忆/财务大事
    → Pydantic 校验
    → 落库: persona + kb_nodes + kb_node_contents + episodic_memories + finance_*
    → 向量化: 全部内容块与记忆写入 kb_embeddings

用法:
    from vh_generate.generate.seeder import ingest_persona
    ingest_persona("陈志强, 52岁, 中学语文老师, 妻子周敏, 女儿陈小雨读大二, 2018年在城东买房, 性格温和爱引经据典")

无 OPENAI_API_KEY 时走 mock 模板生成 (冒烟用), 生产必须配真实 key.
"""

from __future__ import annotations

import json
import sys

from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from vh_generate.generate.embeddings import embed_texts
from virtual_human.db import async_session_factory, session_factory
from virtual_human.models import (
    EpisodicMemory,
    FinanceBalance,
    FinanceEvent,
    KbEmbedding,
    KbNode,
    KbNodeContent,
    Persona,
)

EMBED_MODEL = "text-embedding-3-small"


# ---------------------------------------------------------------------
# LLM 输出的结构定义
# ---------------------------------------------------------------------
class SeedContent(BaseModel):
    """知识节点下的一个内容块."""

    label: str = Field(description="内容块标题, 如'对金钱的态度'")
    content: str = Field(description="整块全文, 第一人称, 200-500字, 单块不超过600字")


class SeedNode(BaseModel):
    """知识树节点."""

    node_type: str = Field(description="节点类型: chapter/semantic/procedural")
    title: str
    summary: str = Field(description="一句话摘要, 50字内, 用于骨架加载")
    importance: int = Field(ge=1, le=10)
    contents: list[SeedContent] = Field(min_length=1, max_length=5)

    @field_validator("node_type")
    @classmethod
    def check_type(cls, v: str) -> str:
        if v not in ("chapter", "semantic", "procedural"):
            raise ValueError(f"node_type 必须是 chapter/semantic/procedural, 得到 {v!r}")
        return v


class SeedFinanceEvent(BaseModel):
    """一笔大额财务事件."""

    event_type: str = Field(description="income/expense_buy/expense_live/loan_taken/loan_repay")
    title: str = Field(description="如'买下城东两居室'")
    item_category: str = Field(default="other")
    amount: float = Field(gt=0, description="金额(元), 恒为正")
    occurred_at: str = Field(description="ISO 日期, 如 2018-05-20")
    memory_title: str = Field(description="这笔事对应的记忆标题")
    memory_content: str = Field(description="这笔事的经历叙事, 第一人称, 150-400字")
    emotion: str = Field(default="平静")

    @field_validator("event_type")
    @classmethod
    def check_type(cls, v: str) -> str:
        if v not in ("income", "expense_buy", "expense_live", "loan_taken", "loan_repay"):
            raise ValueError(f"event_type 非法: {v!r}")
        return v


class SeedPersona(BaseModel):
    """ingest 的完整输出, 一人一份数据."""

    slug: str = Field(description="英文标识, 如 chen-zhiqiang")
    name: str
    core_identity: str = Field(description="L0 常驻身份, 200-400字, 第一人称")
    bio_skeleton: str = Field(description="L1 人生骨架大事年表, 200字内")
    voice_style: str = Field(description="语言风格提示, 50字内")
    nodes: list[SeedNode] = Field(min_length=2, max_length=8)
    finances: list[SeedFinanceEvent] = Field(default_factory=list, max_length=10)
    initial_cash: float = Field(default=100000, description="当前现金(元)")


# ---------------------------------------------------------------------
# LLM 结构化生成
# ---------------------------------------------------------------------
INGEST_SYSTEM = """你是人生模拟数据生成器。根据用户给的骨架描述, 生成一个虚拟人物的完整人设数据。

要求:
1. 一切内容第一人称, 口语化, 有生活质感, 避免 AI 腔
2. 内容块 (content) 每块 200-500 字, 是"可被回忆的具体细节", 不是概括性介绍
3. 节点 summary 是 50 字内的一句话, 用于快速浏览
4. 财务事件只写大额的 (购房/买车/彩礼/大病/大额收入), 每笔必须配一段经历叙事
5. 时间线要自洽: 年龄、年份、事件先后要合理
6. slug 用纯小写英文与连字符 (如 career-chapter), 不要中文
7. 严格输出 JSON, 结构:
{
  "slug": "拼音标识如 chen-zhiqiang",
  "name": "姓名",
  "core_identity": "我是谁(第一人称 200-400字)",
  "bio_skeleton": "大事年表(200字内)",
  "voice_style": "语言风格(50字内)",
  "nodes": [
    {"node_type": "chapter|semantic|procedural", "title": "...", "summary": "...",
     "importance": 1-10,
     "contents": [{"label": "...", "content": "200-500字"}]}
  ],
  "finances": [
    {"event_type": "income|expense_buy|expense_live|loan_taken|loan_repay",
     "title": "...", "item_category": "real_estate|vehicle|medical|education|wedding|other",
     "amount": 正数, "occurred_at": "YYYY-MM-DD",
     "memory_title": "...", "memory_content": "150-400字第一人称", "emotion": "..."}
  ],
  "initial_cash": 当前现金数
}"""


def _llm_generate_seed(skeleton_desc: str, model: str) -> SeedPersona:
    from vh_generate.generate.agent import make_llm

    llm = make_llm(model)
    messages = [
        {"role": "system", "content": INGEST_SYSTEM},
        {"role": "user", "content": f"骨架描述:\n{skeleton_desc}"},
    ]
    try:
        raw = llm.invoke(messages).content
    except Exception as e:
        # LLM 调用失败 (凭证/IP 限制/网络) → 降级 mock, 保证灌库链路可用
        print(f"[警告] LLM 调用失败({type(e).__name__}: {str(e)[:60]}), 降级为 mock 模板生成", file=sys.stderr)
        return _mock_generate_seed(skeleton_desc)
    # 兼容 markdown 代码块包裹
    if "```" in raw:
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    data = json.loads(raw.strip())
    return SeedPersona.model_validate(data)


def _mock_generate_seed(skeleton_desc: str) -> SeedPersona:
    """无 API key 时的模板生成, 保证链路可跑."""
    name = skeleton_desc.split(",")[0].strip() or "李明"
    return SeedPersona.model_validate({
        "slug": "mock-persona",
        "name": name,
        "core_identity": f"我叫{name}。{skeleton_desc}。这就是我现在的样子。",
        "bio_skeleton": skeleton_desc,
        "voice_style": "平实温和",
        "nodes": [
            {
                "node_type": "chapter", "title": "主要经历", "summary": "骨架描述中的核心经历。",
                "importance": 8,
                "contents": [{"label": "概述", "content": f"{skeleton_desc}。这些事塑造了现在的我, 回想起来每一步都不容易。"}],
            },
            {
                "node_type": "semantic", "title": "生活态度", "summary": "踏实过日子, 家人最重要。",
                "importance": 7,
                "contents": [{"label": "对家的态度", "content": "我跟你说, 人这辈子忙来忙去, 最后还不是为了家里那几张笑脸。别的都是虚的。"}],
            },
        ],
        "finances": [],
        "initial_cash": 100000,
    })


def generate_seed(skeleton_desc: str, model: str | None = None) -> SeedPersona:
    """骨架描述 → 结构化人设 (同步版, 兼容旧脚本). 无 key 时走 mock (settings.has_llm)."""
    from virtual_human.config import settings

    if not settings.has_llm:
        return _mock_generate_seed(skeleton_desc)
    return _llm_generate_seed(skeleton_desc, model or settings.llm_model)


async def generate_seed_async(skeleton_desc: str, model: str | None = None) -> SeedPersona:
    """骨架描述 → 结构化人设 (aio 主路径). LLM 调用放线程池, 不阻塞事件循环."""
    import asyncio

    from virtual_human.config import settings

    if not settings.has_llm:
        return _mock_generate_seed(skeleton_desc)
    return await asyncio.to_thread(_llm_generate_seed, skeleton_desc, model or settings.llm_model)


# ---------------------------------------------------------------------
# 落库 + 向量化
# ---------------------------------------------------------------------
def _persist(session: Session, seed: SeedPersona) -> str:
    # 幂等: 同 slug 重灌
    session.query(Persona).filter_by(slug=seed.slug).delete(synchronize_session=False)

    persona = Persona(
        slug=seed.slug, name=seed.name, status="live",
        core_identity=seed.core_identity, bio_skeleton=seed.bio_skeleton,
        voice_style=seed.voice_style, context_token_budget=4000,
    )
    session.add(persona)
    session.flush()

    embed_texts_jobs: list[tuple[str, str, str]] = []   # (entity_type, entity_id, text)

    for n in seed.nodes:
        node = KbNode(
            persona_id=persona.id, node_type=n.node_type,
            slug=n.title[:64].replace(" ", "-").replace("，", "").replace(",", ""), title=n.title, summary=n.summary,
            load_level=2, importance=n.importance, status="published",
        )
        session.add(node)
        session.flush()
        for i, c in enumerate(n.contents, start=1):
            content = KbNodeContent(
                node_id=node.id, seq=i, label=c.label, content=c.content,
                token_estimate=len(c.content) // 2, importance=n.importance,
                status="published",
            )
            session.add(content)
            session.flush()
            embed_texts_jobs.append(("content", content.id, c.content))

    cash = seed.initial_cash
    for f in seed.finances:
        mem = EpisodicMemory(
            persona_id=persona.id, title=f.memory_title, content=f.memory_content,
            occurred_at=f.occurred_at, emotion=f.emotion,
            importance=8, status="published",
        )
        session.add(mem)
        session.flush()
        session.add(FinanceEvent(
            persona_id=persona.id, event_type=f.event_type, title=f.title,
            item_category=f.item_category, amount=f.amount, occurred_at=f.occurred_at,
            memory_id=mem.id, note=None,
        ))
        embed_texts_jobs.append(("memory", mem.id, f.memory_content))
        cash -= f.amount if f.event_type in ("expense_buy", "expense_live") else 0
        cash += f.amount if f.event_type == "income" else 0

    session.add(FinanceBalance(persona_id=persona.id, cash=max(cash, 0)))
    session.flush()

    # 向量 (可选: VH_EMBED_ENABLED=true 才写; 未启用返回 None 直接跳过)
    if embed_texts_jobs:
        vecs = embed_texts([t for _, _, t in embed_texts_jobs], model=EMBED_MODEL)
        if vecs is None:
            pass   # 嵌入未启用/失败: 不写 kb_embeddings, 检索走非向量路径
        else:
            for (etype, eid, _), vec in zip(embed_texts_jobs, vecs):
                session.add(KbEmbedding(
                    entity_type=etype, entity_id=eid, model=EMBED_MODEL, embedding=vec,
                ))

    return persona.id


def ingest_persona(skeleton_desc: str, model: str | None = None) -> str:
    """同步入口 (兼容旧脚本): 骨架描述 → 人设 → 落库+向量化."""
    seed = generate_seed(skeleton_desc, model)
    with session_factory() as session, session.begin():
        persona_id = _persist(session, seed)
    return persona_id


# ---------------------------------------------------------------------
# 落库 + 向量化 (aio)
# ---------------------------------------------------------------------
async def _apersist(session, seed: SeedPersona) -> str:
    """异步落库: async_session + 同步 ORM 写法 (SQLAlchemy async 支持)."""
    from sqlalchemy import delete, select

    # 幂等: 同 slug 重灌
    await session.execute(delete(Persona).where(Persona.slug == seed.slug))

    persona = Persona(
        slug=seed.slug, name=seed.name, status="live",
        core_identity=seed.core_identity, bio_skeleton=seed.bio_skeleton,
        voice_style=seed.voice_style, context_token_budget=4000,
    )
    session.add(persona)
    await session.flush()

    embed_jobs: list[tuple[str, str, str]] = []

    for n in seed.nodes:
        node = KbNode(
            persona_id=persona.id, node_type=n.node_type,
            slug=n.title[:64].replace(" ", "-").replace("，", "").replace(",", ""),
            title=n.title, summary=n.summary,
            load_level=2, importance=n.importance, status="published",
        )
        session.add(node)
        await session.flush()
        for i, c in enumerate(n.contents, start=1):
            content = KbNodeContent(
                node_id=node.id, seq=i, label=c.label, content=c.content,
                token_estimate=len(c.content) // 2, importance=n.importance,
                status="published",
            )
            session.add(content)
            await session.flush()
            embed_jobs.append(("content", content.id, c.content))

    cash = seed.initial_cash
    for f in seed.finances:
        mem = EpisodicMemory(
            persona_id=persona.id, title=f.memory_title, content=f.memory_content,
            occurred_at=f.occurred_at, emotion=f.emotion,
            importance=8, status="published",
        )
        session.add(mem)
        await session.flush()
        session.add(FinanceEvent(
            persona_id=persona.id, event_type=f.event_type, title=f.title,
            item_category=f.item_category, amount=f.amount, occurred_at=f.occurred_at,
            memory_id=mem.id, note=None,
        ))
        embed_jobs.append(("memory", mem.id, f.memory_content))
        cash -= f.amount if f.event_type in ("expense_buy", "expense_live") else 0
        cash += f.amount if f.event_type == "income" else 0

    session.add(FinanceBalance(persona_id=persona.id, cash=max(cash, 0)))
    await session.flush()

    # 向量 (可选, 放线程池; 未启用时 None → 跳过)
    import asyncio

    if embed_jobs:
        vecs = await asyncio.to_thread(
            embed_texts, [t for _, _, t in embed_jobs], EMBED_MODEL
        )
        if vecs is not None:
            for (etype, eid, _), vec in zip(embed_jobs, vecs):
                session.add(KbEmbedding(
                    entity_type=etype, entity_id=eid, model=EMBED_MODEL, embedding=vec,
                ))

    return persona.id


async def ingest_persona_async(skeleton_desc: str, model: str | None = None) -> str:
    """aio 主入口: 骨架描述 → LLM 生成 → 异步落库+向量化. 返回 persona_id."""
    seed = await generate_seed_async(skeleton_desc, model)
    async with async_session_factory() as session:
        persona_id = await _apersist(session, seed)
        await session.commit()
    return persona_id
