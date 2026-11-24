"""检索层 —— virtual_human 知识库 API.

渐进式加载的入口:
  get_node()            单节点渐进加载: persona_id+slug+load_level, 1=summary, 2=content
  get_node_clues()      节点关联线索: persona_id+slug → kb_edges 出的关系与对端 slug
  load_l0_identity()    L0 常驻身份, 每次会话必进 system prompt
  load_l1_skeleton()    L1 人生骨架 (全部节点 title+summary)
  retrieve_context()    向量检索 (召回 + 预算装载 + 访问记录)

财务问题走账本 (list_finance_events / get_finance_balance), 不走向量.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from pgvector.sqlalchemy import Vector
from sqlalchemy import select, update
from sqlalchemy.orm import Session

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


@dataclass
class MemoryBlock:
    """装载进上下文的一个记忆单元."""

    source: str          # content / memory / summary
    entity_id: str
    title: str           # 节点标题或记忆标题
    label: str | None    # 内容块 label, 记忆为 None
    text: str
    token_estimate: int
    score: float = 0.0   # load_score, 排序用

    def render(self) -> str:
        head = f"[{self.title}]" + (f"({self.label})" if self.label else "")
        return f"{head}\n{self.text}"


@dataclass
class RetrievedContext:
    """一次检索的结果: 预算内装载的块 + 骨架摘要."""

    blocks: list[MemoryBlock] = field(default_factory=list)
    used_tokens: int = 0
    budget: int = 0

    def render(self) -> str:
        parts = [b.render() for b in self.blocks]
        return "\n\n---\n\n".join(parts)


# ---------------------------------------------------------------------
# L0 / L1
# ---------------------------------------------------------------------
def get_persona(session: Session, slug: str) -> Persona:
    return session.execute(
        select(Persona).where(Persona.slug == slug)
    ).scalar_one()


def load_l0_identity(persona: Persona) -> str:
    """L0: 常驻身份, 直接进 system prompt."""
    parts = [persona.core_identity or ""]
    if persona.voice_style:
        parts.append(f"(说话风格: {persona.voice_style})")
    return "\n".join(p for p in parts if p)


def load_l1_skeleton(session: Session, persona_id: str) -> str:
    """L1: 人生骨架 —— 全部 published 节点的 title+summary, 不碰正文."""
    rows = session.execute(
        select(KbNode.title, KbNode.summary)
        .where(KbNode.persona_id == persona_id, KbNode.status == "published")
        .order_by(KbNode.load_level, KbNode.title)
    ).all()
    lines = []
    for title, summary in rows:
        lines.append(f"- {title}" + (f": {summary}" if summary else ""))
    return "\n".join(lines)


# ---------------------------------------------------------------------
# 单节点渐进加载 API
# ---------------------------------------------------------------------
def find_node(session: Session, persona_id: str, slug: str) -> KbNode:
    """persona_id + slug 定位节点, 不存在抛 KeyError."""
    node = session.execute(
        select(KbNode).where(KbNode.persona_id == persona_id, KbNode.slug == slug)
    ).scalar_one_or_none()
    if node is None:
        raise KeyError(f"node not found: persona_id={persona_id} slug={slug}")
    return node


@dataclass
class NodeView:
    """节点视图: load_level=1 给摘要, =2 给全文."""

    node_id: str
    slug: str
    title: str
    node_type: str
    load_level: int            # 请求层级
    summary: str | None        # level>=1 恒有
    contents: list[dict] | None  # level>=2 才有: [{id,seq,label,content,token_estimate}]
    token_estimate: int        # 实际返回内容的 token 估算

    def render(self) -> str:
        parts = [f"[{self.title}]"]
        if self.load_level >= 2 and self.contents:
            for c in self.contents:
                head = f"({c['label']})" if c.get("label") else ""
                parts.append(f"{head}\n{c['content']}")
        elif self.summary:
            parts.append(self.summary)
        return "\n".join(parts)


def get_node(
    session: Session,
    persona_id: str,
    slug: str,
    load_level: int = 1,
    record_access: bool = True,
) -> NodeView:
    """渐进式加载单个节点.

    load_level=1: 只返回 title + summary (便宜, 骨架层)
    load_level=2: 额外返回全部内容块全文 (贵, 命中才用)

    命中内容块时联动 access_count/last_accessed_at (节点级与块级), 并写访问日志.
    """
    from datetime import datetime, timezone

    from virtual_human.models import MemoryAccessLog

    if load_level not in (1, 2):
        raise ValueError(f"load_level 必须是 1 或 2, 得到 {load_level}")

    node = find_node(session, persona_id, slug)

    contents: list[dict] | None = None
    token_total = 0

    if load_level >= 2:
        rows = (
            session.execute(
                select(KbNodeContent)
                .where(
                    KbNodeContent.node_id == node.id,
                    KbNodeContent.status == "published",
                )
                .order_by(KbNodeContent.seq)
            )
            .scalars()
            .all()
        )
        contents = [
            {
                "id": c.id,
                "seq": c.seq,
                "label": c.label,
                "content": c.content,
                "token_estimate": c.token_estimate,
            }
            for c in rows
        ]
        token_total = sum(c["token_estimate"] for c in contents)

        if record_access and contents:
            now = datetime.now(timezone.utc)
            content_ids = [c["id"] for c in contents]
            session.execute(
                update(KbNodeContent)
                .where(KbNodeContent.id.in_(content_ids))
                .values(access_count=KbNodeContent.access_count + 1, last_accessed_at=now)
            )
            session.execute(
                update(KbNode)
                .where(KbNode.id == node.id)
                .values(access_count=KbNode.access_count + 1, last_accessed_at=now)
            )
            session.add(
                MemoryAccessLog(
                    persona_id=persona_id,
                    entity_type="content",
                    entity_id=node.id,
                    trigger="session",
                    query_text=f"get_node {slug} level={load_level}",
                    load_score=1.0,
                )
            )
            session.commit()

    return NodeView(
        node_id=node.id,
        slug=node.slug,
        title=node.title,
        node_type=node.node_type,
        load_level=load_level,
        summary=node.summary,
        contents=contents,
        token_estimate=token_total or len(node.summary or "") // 2,
    )


# ---------------------------------------------------------------------
# 关联线索 API (kb_edges)
# ---------------------------------------------------------------------
@dataclass
class NodeClue:
    """一条关联线索."""

    direction: str        # outgoing / incoming
    edge_type: str        # related/causes/contradicts/elaborates/part_of
    weight: float
    neighbor_slug: str
    neighbor_title: str
    neighbor_summary: str | None
    note: str | None

    def render(self) -> str:
        arrow = {"related": "相关", "causes": "引发", "contradicts": "矛盾",
                 "elaborates": "展开", "part_of": "属于"}.get(self.edge_type, self.edge_type)
        if self.direction == "outgoing":
            return f"- {arrow} → {self.neighbor_title} ({self.neighbor_slug})" + (f": {self.neighbor_summary}" if self.neighbor_summary else "")
        return f"- ← {arrow} — {self.neighbor_title} ({self.neighbor_slug})" + (f": {self.neighbor_summary}" if self.neighbor_summary else "")


def get_node_clues(session: Session, persona_id: str, slug: str) -> list[NodeClue]:
    """查节点的关联线索: kb_edges 双向, 按权重降序, 返回关系类型与对端 slug/summary.

    Agent 用法: 联想加载 —— 看到线索后按需对感兴趣的 slug 再调 get_node(level=2).
    """
    node = find_node(session, persona_id, slug)

    # 出边: node → 邻居
    out_rows = session.execute(
        select(KbEdge, KbNode)
        .join(KbNode, KbEdge.dst_node_id == KbNode.id)
        .where(KbEdge.src_node_id == node.id)
        .order_by(KbEdge.weight.desc())
    ).all()

    # 入边: 邻居 → node
    in_rows = session.execute(
        select(KbEdge, KbNode)
        .join(KbNode, KbEdge.src_node_id == KbNode.id)
        .where(KbEdge.dst_node_id == node.id)
        .order_by(KbEdge.weight.desc())
    ).all()

    clues = [
        NodeClue(
            direction="outgoing", edge_type=e.edge_type, weight=float(e.weight),
            neighbor_slug=n.slug, neighbor_title=n.title, neighbor_summary=n.summary,
            note=e.note,
        )
        for e, n in out_rows
    ]
    clues += [
        NodeClue(
            direction="incoming", edge_type=e.edge_type, weight=float(e.weight),
            neighbor_slug=n.slug, neighbor_title=n.title, neighbor_summary=n.summary,
            note=e.note,
        )
        for e, n in in_rows
    ]
    return clues


# ---------------------------------------------------------------------
# 整棵树 API
# ---------------------------------------------------------------------
@dataclass
class TreeNode:
    """树上的一个节点: slug = persona_id/node_type."""

    slug: str                  # persona_id/node_type
    node_id: str
    node_type: str
    title: str
    summary: str | None
    importance: int
    parent_id: str | None
    children: list["TreeNode"] = field(default_factory=list)

    def walk(self):
        """深度优先遍历."""
        yield self
        for child in self.children:
            yield from child.walk()

    def render(self, indent: int = 0) -> str:
        """树形文本, Agent 可直接读."""
        lines = []
        pad = "  " * indent
        lines.append(f"{pad}- [{self.slug}] {self.title}" + (f": {self.summary}" if self.summary else ""))
        for child in self.children:
            lines.append(child.render(indent + 1))
        return "\n".join(lines)


def get_kb_tree(session: Session, persona_id: str) -> TreeNode:
    """查整棵知识树, 返回虚拟根 (slug=persona_id/root), children 按 parent_id 组装.

    每个物理节点的 slug = persona_id/node_type; 同 node_type 多节点时以 node_id/层级物理区分.
    无父 (或父不在本 persona 集合内) 的节点挂到虚拟根下.
    """
    nodes = (
        session.execute(
            select(KbNode)
            .where(KbNode.persona_id == persona_id, KbNode.status == "published")
            .order_by(KbNode.load_level, KbNode.title)
        )
        .scalars()
        .all()
    )
    if not nodes:
        raise KeyError(f"no nodes for persona_id={persona_id}")

    by_id = {n.id: n for n in nodes}
    children_map: dict[str | None, list[KbNode]] = {}
    for n in nodes:
        pid = n.parent_id if n.parent_id in by_id else None
        children_map.setdefault(pid, []).append(n)

    def build(n: KbNode) -> TreeNode:
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

    root = TreeNode(
        slug=f"{persona_id}/root",
        node_id="root",
        node_type="root",
        title=persona_id,
        summary=None,
        importance=0,
        parent_id=None,
        children=[build(n) for n in children_map.get(None, [])],
    )
    return root


# ---------------------------------------------------------------------
# Markdown 人物简介 API
# ---------------------------------------------------------------------
def render_persona_md(
    session: Session,
    persona_id: str,
    min_disclosure: int = 1,
    include_finance: bool = True,
) -> str:
    """输出 Markdown 人物简介.

    summary 用 <slug id="persona_id/node_type">...</slug> 标签包裹, 供下游解析定位;
    节点按 disclosure_level (重要披露度) 披露: disclosure_level >= min_disclosure 才出现 summary,
    disclosure_level = 0 视为隐藏, 节点只列标题不摘内容.

    参数:
        min_disclosure: 披露阈值, 默认 1 (disclosure>=1 都披露); 调高只保留高披露度节点.
        include_finance: 是否附财务大事记.
    """
    persona = session.get(Persona, persona_id)
    if persona is None:
        raise KeyError(f"persona not found: {persona_id}")

    lines: list[str] = []
    lines.append(f"# {persona.name}")
    lines.append("")

    # L0 身份
    if persona.core_identity:
        lines.append(persona.core_identity)
        lines.append("")

    if persona.voice_style:
        lines.append(f"> 说话风格: {persona.voice_style}")
        lines.append("")

    # 知识树 (人生骨架索引与记忆图谱共用)
    tree = get_kb_tree(session, persona_id)
    nodes = [t for t in tree.walk() if t.node_type != "root"]

    # 有披露内容的节点: 按 disclosure 降序, 组章节
    disclosed = [t for t in nodes if t.importance >= 1]
    disclosed.sort(key=lambda t: (-t.importance, t.title))

    from virtual_human.models import KbNode

    db_nodes = {
        n.id: n
        for n in session.execute(
            select(KbNode).where(KbNode.persona_id == persona_id)
        ).scalars()
    }

    # L1 人生骨架 = 知识节点标题索引 (不是身份自述; 用 <slug> 引导探索)
    lines.append("## 人生骨架")
    lines.append("")
    if nodes:
        for t in disclosed:
            db_node = db_nodes.get(t.node_id)
            slug = f"{persona_id}/{db_node.slug}" if db_node is not None else t.slug
            lines.append(f'- <slug id="{slug}">{t.title}</slug>')
    else:
        lines.append("（暂无知识节点）")
    lines.append("")

    lines.append("## 记忆图谱")
    lines.append("")
    for t in disclosed:
        db_node = db_nodes.get(t.node_id)
        if db_node is None:
            continue
        d = db_node.disclosure_level
        lines.append(f"### {t.title}")
        lines.append("")
        if d >= min_disclosure and d > 0 and t.summary:
            lines.append(f'<slug id="{t.slug}">{t.summary}</slug>')
        else:
            lines.append(f'<slug id="{t.slug}" disclosed="false"/>')
        lines.append("")

    # 财务大事记
    if include_finance:
        finance_lines = render_finance(session, persona.slug)
        if finance_lines:
            lines.append("## 财务大事记")
            lines.append("")
            lines.append(finance_lines)
            lines.append("")

    return "\n".join(lines).strip() + "\n"


# ---------------------------------------------------------------------
# 向量召回
# ---------------------------------------------------------------------
def _embed_query(query: str, model: str):
    """查询向量化 (可选).

    依赖注入: 由上层 (vh_generate) 在启动时调用 register_embedder() 注册;
    未注册 (嵌入未启用) 返回 None, 检索走结构化降级路径.
    知识库层不 import 生成模块 —— 保持依赖单向: vh_generate → virtual_human.
    """
    if _EMBEDDER["fn"] is None:
        return None
    return _EMBEDDER["fn"](query, model)


_EMBEDDER: dict = {"fn": None}


def register_embedder(fn) -> None:
    """注册查询向量化函数 (签名: (query: str, model: str) -> list[float] | None)."""
    _EMBEDDER["fn"] = fn


def _vector_search(
    session: Session,
    persona_id: str,
    query_vec: list[float],
    model: str,
    top_k: int,
) -> list[tuple[str, str, float]]:
    """在 kb_embeddings 里近邻召回, 返回 (entity_type, entity_id, similarity).

    相似度 = 1 - cosine distance, 归一到 0-1.
    """
    from sqlalchemy import cast, type_coerce

    rows = session.execute(
        select(
            KbEmbedding.entity_type,
            KbEmbedding.entity_id,
            (1 - KbEmbedding.embedding.cosine_distance(type_coerce(query_vec, Vector))).label("sim"),
        )
        .where(KbEmbedding.model == model)
        .order_by(KbEmbedding.embedding.cosine_distance(type_coerce(query_vec, Vector)))
        .limit(top_k)
    ).all()
    return [(r[0], r[1], float(r[2])) for r in rows]


# ---------------------------------------------------------------------
# load_score: 相似度 × 重要度 × 衰减 × 强化
# ---------------------------------------------------------------------
def _load_score(similarity: float, importance: int, last_accessed_at, access_count: int) -> float:
    """w1·相似度 + w2·重要度 + w3·新近度 + w4·强化, 权重可调."""
    import math
    from datetime import datetime, timezone

    w_sim, w_imp, w_rec, w_freq = 0.5, 0.2, 0.2, 0.1

    recency = 0.0
    if last_accessed_at is not None:
        la = last_accessed_at
        if la.tzinfo is None:            # DB 存的是 naive UTC
            la = la.replace(tzinfo=timezone.utc)
        days = max((datetime.now(timezone.utc) - la).days, 0)
        recency = math.exp(-days / 30.0)   # 30 天半衰期
    freq = min(access_count / 20.0, 1.0)   # 20 次"想起"封顶

    return w_sim * similarity + w_imp * (importance / 10.0) + w_rec * recency + w_freq * freq


# ---------------------------------------------------------------------
# 检索装载策略: 词面相似度兜底 + 阈值/配额/去重
# ---------------------------------------------------------------------
LOAD_MIN_SCORE = 0.16          # 绝对最低分
LOAD_RELATIVE_CUTOFF = 0.6     # 低于 top_score × 0.6 不再装
LOAD_MAX_CONTENT = 6           # 知识内容块上限
LOAD_MAX_MEMORY = 10           # 情景记忆上限
LOAD_DUP_SIM_SAME = 0.80       # 同类近重复阈值
LOAD_DUP_SIM_CROSS = 0.55      # 跨类 (内容 vs 记忆) 近重复阈值


def _text_tokens(text: str) -> set[str]:
    """中文二元组 + 英文/数字词, 用于无 embedding 时的相似度计算."""
    import re

    t = (text or "").lower()
    out = set(re.findall(r"[a-z0-9]+", t))
    for run in re.findall(r"[\u4e00-\u9fff]+", t):
        if len(run) == 1:
            out.add(run)
        else:
            out.update(run[i:i + 2] for i in range(len(run) - 1))
    return out


def _lexical_similarity(query: str, title: str, label: str | None, text: str) -> float:
    """词面覆盖度: query 的 token 有多少出现在标题/正文里 (0~1)."""
    q = _text_tokens(query)
    if not q:
        return 0.0
    doc = _text_tokens(f"{title or ''} {label or ''} {(text or '')[:1200]}")
    hit = len(q & doc)
    coverage = hit / len(q)
    title_hit = len(q & _text_tokens(f"{title or ''} {label or ''}"))
    return min(1.0, coverage + 0.25 * (title_hit / len(q)))


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _fill_context(ctx: "RetrievedContext", candidates: list["MemoryBlock"]) -> list["MemoryBlock"]:
    """按分数装载: 相对阈值 + 每类配额 + 近重复过滤 + 预算."""
    real = [b for b in candidates if b.source != "summary"]
    if not real:
        return []

    ranked = sorted(real, key=lambda b: -b.score)
    top = ranked[0].score
    threshold = max(LOAD_MIN_SCORE, top * LOAD_RELATIVE_CUTOFF)

    chosen: list[MemoryBlock] = []
    counts = {"content": 0, "memory": 0}
    token_sets = {id(b): _text_tokens((b.text or "")[:600]) for b in ranked}

    for b in ranked:
        if b.score < threshold:
            break
        cap = LOAD_MAX_CONTENT if b.source == "content" else LOAD_MAX_MEMORY
        if counts.get(b.source, 0) >= cap:
            continue
        if b.token_estimate > ctx.budget - ctx.used_tokens:
            continue

        bt = token_sets[id(b)]
        dup = False
        for x in chosen:
            sim = _jaccard(bt, token_sets[id(x)])
            limit = LOAD_DUP_SIM_SAME if x.source == b.source else LOAD_DUP_SIM_CROSS
            if sim >= limit:
                dup = True
                break
        if dup:
            continue

        ctx.blocks.append(b)
        ctx.used_tokens += b.token_estimate
        counts[b.source] = counts.get(b.source, 0) + 1
        chosen.append(b)

    return chosen


# ---------------------------------------------------------------------
# L2+: 检索并按预算装载
# ---------------------------------------------------------------------
def retrieve_context(
    session: Session,
    persona_slug: str,
    query: str,
    embedding_model: str = "text-embedding-3-small",
    top_k: int = 20,
    with_skeleton: bool = True,
) -> RetrievedContext:
    """渐进式检索主入口: 向量召回 → 打分 → 预算内装载 → 记录访问."""
    persona = get_persona(session, persona_slug)
    ctx = RetrievedContext(budget=persona.context_token_budget)

    if with_skeleton:
        # 骨架先行, 也占预算
        skeleton = load_l1_skeleton(session, persona.id)
        skel_tokens = len(skeleton) // 2   # 中文粗估: 2 字符 ≈ 1 token
        ctx.used_tokens += skel_tokens
        ctx.blocks.append(MemoryBlock(
            source="summary", entity_id=persona.id, title="人生骨架", label=None,
            text=skeleton, token_estimate=skel_tokens, score=1.0,
        ))

    query_vec = _embed_query(query, embedding_model)
    hits = _vector_search(session, persona.id, query_vec, embedding_model, top_k) if query_vec else []

    # 组装候选块
    candidates: list[MemoryBlock] = []
    content_ids = [eid for etype, eid, _ in hits if etype == "content"]
    memory_ids = [eid for etype, eid, _ in hits if etype == "memory"]

    # 嵌入未启用 (query_vec None → hits 空): 结构化降级, 全量候选按 importance/新近度打分
    if not hits:
        rows = session.execute(
            select(KbNodeContent, KbNode)
            .join(KbNode, KbNodeContent.node_id == KbNode.id)
            .where(
                KbNode.persona_id == persona.id,
                KbNodeContent.status == "published",
                KbNode.load_level >= 2,
            )
        ).all()
        for content, node in rows:
            candidates.append(MemoryBlock(
                source="content", entity_id=content.id,
                title=node.title, label=content.label,
                text=content.content, token_estimate=content.token_estimate,
                score=_load_score(
                    _lexical_similarity(query, node.title, content.label, content.content),
                    content.importance, content.last_accessed_at, content.access_count,
                ),
            ))
        mems = session.execute(
            select(EpisodicMemory)
            .where(
                EpisodicMemory.persona_id == persona.id,
                EpisodicMemory.status == "published",
            )
        ).scalars().all()
        for mem in mems:
            candidates.append(MemoryBlock(
                source="memory", entity_id=mem.id,
                title=mem.title, label=None,
                text=mem.content, token_estimate=len(mem.content) // 2,
                score=_load_score(
                    _lexical_similarity(query, mem.title, None, mem.content),
                    mem.importance, mem.last_accessed_at, mem.access_count,
                ),
            ))

    if content_ids:
        rows = session.execute(
            select(KbNodeContent, KbNode, KbEmbedding.entity_id)
            .join(KbNode, KbNodeContent.node_id == KbNode.id)
            .join(KbEmbedding, KbEmbedding.entity_id == KbNodeContent.id)
            .where(
                KbNodeContent.id.in_(content_ids),
                KbNodeContent.status == "published",
                KbNode.load_level >= 2,          # L2+ 才给全文
            )
        ).all()
        sim_by_id = {eid: s for etype, eid, s in hits if etype == "content"}
        for content, node, eid in rows:
            candidates.append(MemoryBlock(
                source="content", entity_id=content.id,
                title=node.title, label=content.label,
                text=content.content, token_estimate=content.token_estimate,
                score=_load_score(
                    sim_by_id.get(eid, 0.0), content.importance,
                    content.last_accessed_at, content.access_count,
                ),
            ))

    if memory_ids:
        rows = session.execute(
            select(EpisodicMemory, KbEmbedding.entity_id)
            .join(KbEmbedding, KbEmbedding.entity_id == EpisodicMemory.id)
            .where(
                EpisodicMemory.id.in_(memory_ids),
                EpisodicMemory.status == "published",
            )
        ).all()
        sim_by_id = {eid: s for etype, eid, s in hits if etype == "memory"}
        for mem, eid in rows:
            candidates.append(MemoryBlock(
                source="memory", entity_id=mem.id,
                title=mem.title, label=None,
                text=mem.content, token_estimate=len(mem.content) // 2,
                score=_load_score(
                    sim_by_id.get(eid, 0.0), mem.importance,
                    mem.last_accessed_at, mem.access_count,
                ),
            ))

    # 预算装载: 相对阈值 + 每类配额 + 近重复过滤
    _fill_context(ctx, candidates)

    # 记录访问: 强化计数 + 衰减时间戳 + 访问日志
    _record_access(session, persona.id, ctx.blocks, query)

    return ctx


def _record_access(session: Session, persona_id: str, blocks: list[MemoryBlock], query: str) -> None:
    """块命中 → access_count+1 / last_accessed_at 更新; 节点联动."""
    from virtual_human.models import MemoryAccessLog
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    content_ids = [b.entity_id for b in blocks if b.source == "content"]
    memory_ids = [b.entity_id for b in blocks if b.source == "memory"]

    if content_ids:
        session.execute(
            update(KbNodeContent)
            .where(KbNodeContent.id.in_(content_ids))
            .values(access_count=KbNodeContent.access_count + 1, last_accessed_at=now)
        )
        node_ids = session.execute(
            select(KbNodeContent.node_id).where(KbNodeContent.id.in_(content_ids))
        ).scalars().all()
        session.execute(
            update(KbNode)
            .where(KbNode.id.in_(node_ids))
            .values(access_count=KbNode.access_count + 1, last_accessed_at=now)
        )
    if memory_ids:
        session.execute(
            update(EpisodicMemory)
            .where(EpisodicMemory.id.in_(memory_ids))
            .values(access_count=EpisodicMemory.access_count + 1, last_accessed_at=now)
        )

    for b in blocks:
        if b.source == "summary":
            continue   # 骨架不记
        session.add(MemoryAccessLog(
            persona_id=persona_id, entity_type=b.source, entity_id=b.entity_id,
            trigger="retrieval", query_text=query, load_score=b.score,
        ))
    session.commit()


# ---------------------------------------------------------------------
# 财务账本 (精确查询, 不走向量)
# ---------------------------------------------------------------------
def list_finance_events(session: Session, persona_slug: str) -> list[FinanceEvent]:
    persona = get_persona(session, persona_slug)
    return list(session.execute(
        select(FinanceEvent)
        .where(FinanceEvent.persona_id == persona.id)
        .order_by(FinanceEvent.occurred_at)
    ).scalars())


def get_finance_balance(session: Session, persona_slug: str) -> FinanceBalance | None:
    persona = get_persona(session, persona_slug)
    return session.get(FinanceBalance, persona.id)


def render_finance(session: Session, persona_slug: str) -> str:
    """账本概览, 给 Agent 当上下文."""
    events = list_finance_events(session, persona_slug)
    bal = get_finance_balance(session, persona_slug)
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
