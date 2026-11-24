"""vh_api —— FastAPI 应用: 四大功能 API + SSE 流式.

启动: uv run vh-api [--port 8300]   文档: http://127.0.0.1:8300/docs
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass
from typing import Any, AsyncIterator

# Windows: psycopg async 与 ProactorEventLoop 不兼容.
# 在模块导入最早期切换全局 policy —— 任何入口 (uv run vh-api / uvicorn 直启 /
# reload 子进程) import 本模块时都会执行, 早于任何事件循环创建.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field

from virtual_human.config import settings
from vh_api.job_service import spawn_job


# ---------------------------------------------------------------------
# SSE 工具
# ---------------------------------------------------------------------
def _sse(t: str, d: Any) -> str:
    return f"data: {json.dumps({'type': t, 'data': d}, ensure_ascii=False)}\n\n"


# ---------------------------------------------------------------------
# 会话注册表 (进程内; thread_id 落 checkpoint 后可跨进程续聊)
# ---------------------------------------------------------------------
@dataclass
class ChatSession:
    agent: Any
    thread_id: str


_SESSIONS: dict[str, ChatSession] = {}


async def _get_or_create_session(persona_id: str, session_id: str | None) -> ChatSession:
    if session_id and session_id in _SESSIONS:
        s = _SESSIONS[session_id]
        if s.agent.persona_id == persona_id:
            return s
    from vh_api.checkpointer import make_thread_id
    from vh_simulate import PersonaAgent

    agent = await PersonaAgent.create(persona_id, brief=True)
    sid = session_id or make_thread_id(persona_id)
    s = ChatSession(agent=agent, thread_id=sid)
    _SESSIONS[sid] = s
    return s


# ---------------------------------------------------------------------
# 请求模型
# ---------------------------------------------------------------------
class IngestRequest(BaseModel):
    skeleton: str = Field(description="人物骨架提示词")
    model: str | None = Field(default=None, description="LLM 模型")

class EditRequest(BaseModel):
    instruction: str = Field(description="修改要求")
    node_ids: list[str] = Field(default_factory=list, description="定向修改的节点 id 列表 (空=全人物)")


class CheckRequest(BaseModel):
    fix: bool = Field(default=False, description="检查后自动修复高/中等问题")


class ChatRequest(BaseModel):
    message: str
    session_id: str | None = Field(default=None, description="续聊回传; 缺省新会话")
    docs: list[str] = Field(default_factory=list, description="引用的 agent 文档 (docs 目录相对路径)")


# ---------------------------------------------------------------------
# app
# ---------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    import logging

    import vh_generate  # noqa: F401  (导入时向知识库注册嵌入器)

    from vh_api.checks import CheckpointerCheck, StorageCheck
    from vh_generate.checks import EmbeddingCheck, LLMCheck
    from virtual_human.checks import ConfigCheck, DatabaseCheck
    from virtual_human.healthcheck import register_check, run_checks

    # 启动自检: 注册各模块实现类, 统一执行 (结果写 vh.health 日志, 不阻断启动)
    for impl in (ConfigCheck(), DatabaseCheck(), LLMCheck(), EmbeddingCheck(),
                 CheckpointerCheck(), StorageCheck()):
        register_check(impl)
    app.state.health = await run_checks()

    try:
        yield
    finally:
        # 优雅关闭: 先取消在跑的后台任务, 再 dispose 异步连接池,
        # 避免 asyncio 报 "Task was destroyed but it is pending!"
        from vh_api.job_service import cancel_running_jobs
        from virtual_human.db import dispose_async_engine

        await cancel_running_jobs()
        await dispose_async_engine()
        logging.getLogger("vh.db").info("异步连接池已释放 (dispose)")


app = FastAPI(
    title="virtual_human API",
    description="「模拟人类」知识库: 人设生成 / 检查修改 / 模拟对话 (SSE 流式)",
    version="0.1.0",
    lifespan=lifespan,
)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


# ---------------------------------------------------------------------
# 自检 (启动时的结果缓存在 app.state.health; 本接口实时重跑)
# ---------------------------------------------------------------------
@app.get("/api/health", tags=["health"], summary="自检: 各模块健康状态")
async def health():
    from dataclasses import asdict

    from virtual_human.healthcheck import run_checks

    results = await run_checks()
    return {
        "ok": all(r.ok for r in results),
        "checks": [asdict(r) for r in results],
    }


# ---------------------------------------------------------------------
# 人物列表 / 知识树
# ---------------------------------------------------------------------
@app.get("/api/personas", tags=["persona"], summary="人物列表")
async def list_personas():
    from sqlalchemy import select

    from virtual_human.db import async_session_factory
    from virtual_human.models import Persona

    async with async_session_factory() as session:
        rows = (await session.execute(select(Persona))).scalars().all()
        return [{"id": p.id, "slug": p.slug, "name": p.name, "status": p.status} for p in rows]


@app.get("/api/personas/overview", tags=["persona"], summary="人物概览列表 (卡片/表单预览用)")
async def personas_overview():
    """每位人物的概要: 骨架摘要、性格导向、履历/知识节点/财务/记忆计数."""
    from sqlalchemy import func, select

    from virtual_human.db import async_session_factory
    from virtual_human.models import (
        EpisodicMemory,
        FinanceEvent,
        KbNode,
        LifeScaffoldContent,
        LifeScaffoldEvent,
        Persona,
    )

    async with async_session_factory() as session:
        rows = (await session.execute(select(Persona).order_by(Persona.created_at))).scalars().all()

        async def counts_of(model):
            res = await session.execute(
                select(model.persona_id, func.count()).group_by(model.persona_id)
            )
            return {pid: n for pid, n in res.all()}

        ev = await counts_of(LifeScaffoldEvent)
        ev_done = {
            pid: n for pid, n in (
                await session.execute(
                    select(LifeScaffoldEvent.persona_id, func.count())
                    .where(LifeScaffoldEvent.processed.is_(True))
                    .group_by(LifeScaffoldEvent.persona_id)
                )
            ).all()
        }
        cont = await counts_of(LifeScaffoldContent)
        kb = await counts_of(KbNode)
        fin = await counts_of(FinanceEvent)
        mem = await counts_of(EpisodicMemory)

        out = []
        for p in rows:
            ident = (p.core_identity or "").strip().replace("\n", " ")
            out.append({
                "id": p.id, "slug": p.slug, "name": p.name, "status": p.status,
                "created_at": p.created_at.isoformat() if p.created_at else None,
                "identity": ident,
                "identity_head": ident[:120] + ("…" if len(ident) > 120 else ""),
                "personality_head": (p.personality or "")[:100],
                "has_skeleton": bool((p.core_identity or "").strip()),
                "has_personality": bool((p.personality or "").strip()),
                "counts": {
                    "events": ev.get(p.id, 0), "events_done": ev_done.get(p.id, 0),
                    "event_contents": cont.get(p.id, 0),
                    "kb_nodes": kb.get(p.id, 0),
                    "finance": fin.get(p.id, 0),
                    "memories": mem.get(p.id, 0),
                },
            })
        return out


@app.get("/api/personas/{pid}/tree", tags=["persona"], summary="知识树 (L1: 只含 summary)")
async def persona_tree(pid: str):
    from virtual_human.db import async_session_factory
    from virtual_human.kb.async_api import aget_kb_tree

    async with async_session_factory() as session:
        try:
            tree = await aget_kb_tree(session, pid)
        except KeyError as e:
            raise HTTPException(404, str(e))

        def node(t) -> dict:
            return {
                "slug": t.slug, "id": t.node_id, "node_id": t.node_id,
                "node_type": t.node_type,
                "title": t.title, "summary": t.summary, "importance": t.importance,
                "children": [node(c) for c in t.children],
            }

        return {"slug": tree.slug, "children": [node(c) for c in tree.children]}


@app.get("/api/personas/{pid}/profile", tags=["persona"], summary="Markdown 人物简介 (含 <slug> 标签)")
async def persona_profile(pid: str):
    from virtual_human.db import async_session_factory
    from virtual_human.kb.async_api import arender_persona_md

    async with async_session_factory() as session:
        try:
            return {"markdown": await arender_persona_md(session, pid)}
        except KeyError as e:
            raise HTTPException(404, str(e))


@app.get("/api/personas/{pid}/graph", tags=["persona"], summary="节点关系图 (树 + kb_edges, 供前端渲染)")
async def persona_graph(pid: str):
    """返回 {nodes: [...], edges: [...]} —— 树形层级 + 联想边, 前端可直接喂图组件."""
    from sqlalchemy import select

    from virtual_human.db import async_session_factory
    from virtual_human.models import KbEdge, KbNode
    from virtual_human.kb.async_api import aget_kb_tree

    async with async_session_factory() as session:
        try:
            tree = await aget_kb_tree(session, pid)
        except KeyError as e:
            raise HTTPException(404, str(e))

        # 全部节点 (id/类型/标题/深度)
        nodes: list[dict] = []

        def walk(t, depth: int) -> None:
            if t.node_type == "root":
                for c in t.children:
                    walk(c, depth)
                return
            nodes.append({
                "id": t.node_id, "node_type": t.node_type, "title": t.title,
                "summary": t.summary, "importance": t.importance, "depth": depth,
            })
            for c in t.children:
                walk(c, depth + 1)

        walk(tree, 0)

        # 树边 (parent→child) + 联想边 (kb_edges)
        node_ids = {n["id"] for n in nodes}
        edges: list[dict] = []
        db_nodes = (
            await session.execute(select(KbNode).where(KbNode.persona_id == pid))
        ).scalars().all()
        parent_by_id = {n.id: n.parent_id for n in db_nodes}
        for n in nodes:
            p = parent_by_id.get(n["id"])
            if p and p in node_ids:
                edges.append({"source": p, "target": n["id"], "type": "tree", "label": ""})

        # 兜底: 节点可能全是顶层 (parent_id 为空), 此时树边为 0 → 图无法布局.
        # 补一个虚拟根节点, 星型连到所有顶层节点, 保证图谱始终可渲染.
        has_tree_edge = any(e["type"] == "tree" for e in edges)
        root_id = None
        if nodes and not has_tree_edge:
            root_id = f"{pid}-root"
            nodes.insert(0, {
                "id": root_id, "node_type": "root", "title": "人物",
                "summary": None, "importance": 0, "depth": 0, "virtual": True,
            })
            for n in nodes[1:]:
                edges.append({"source": root_id, "target": n["id"], "type": "tree", "label": ""})

        db_edges = (
            await session.execute(select(KbEdge).where(KbEdge.src_node_id.in_(node_ids)))
        ).scalars().all()
        for e in db_edges:
            if e.dst_node_id in node_ids:
                edges.append({
                    "source": e.src_node_id, "target": e.dst_node_id,
                    "type": e.edge_type, "label": e.edge_type, "weight": float(e.weight),
                })

        return {"nodes": nodes, "edges": edges, "root_id": root_id}


@app.get("/api/personas/{pid}/nodes/{node_id}", tags=["persona"], summary="节点详情 (渐进加载: 内容块全文)")
async def node_detail(pid: str, node_id: str):
    """单个节点的完整视图: 元数据 + 内容块全文 + 联想线索."""
    from sqlalchemy import select

    from virtual_human.db import async_session_factory
    from virtual_human.kb.async_api import aget_node_clues
    from virtual_human.models import KbEdge, KbNode, KbNodeContent

    async with async_session_factory() as session:
        node = (
            await session.execute(
                select(KbNode).where(KbNode.persona_id == pid, KbNode.id == node_id)
            )
        ).scalar_one_or_none()
        if node is None:
            raise HTTPException(404, f"node 不存在: {node_id}")

        contents = (
            (
                await session.execute(
                    select(KbNodeContent)
                    .where(KbNodeContent.node_id == node_id, KbNodeContent.status == "published")
                    .order_by(KbNodeContent.seq)
                )
            )
            .scalars()
            .all()
        )

        # 联想线索 (出边+入边)
        out_edges = (
            await session.execute(
                select(KbEdge, KbNode)
                .join(KbNode, KbEdge.dst_node_id == KbNode.id)
                .where(KbEdge.src_node_id == node_id)
            )
        ).all()
        in_edges = (
            await session.execute(
                select(KbEdge, KbNode)
                .join(KbNode, KbEdge.src_node_id == KbNode.id)
                .where(KbEdge.dst_node_id == node_id)
            )
        ).all()

        def clue(direction, e, n) -> dict:
            return {
                "direction": direction, "edge_type": e.edge_type,
                "weight": float(e.weight), "node_id": n.id,
                "title": n.title, "summary": n.summary,
            }

        return {
            "id": node.id, "title": node.title, "node_type": node.node_type,
            "slug": node.slug, "summary": node.summary,
            "importance": node.importance, "disclosure_level": node.disclosure_level,
            "access_count": node.access_count,
            "contents": [
                {
                    "id": c.id, "seq": c.seq, "label": c.label,
                    "content": c.content, "token_estimate": c.token_estimate,
                    "importance": c.importance, "valid_from": str(c.valid_from) if c.valid_from else None,
                    "valid_to": str(c.valid_to) if c.valid_to else None,
                }
                for c in contents
            ],
            "clues": ([clue("outgoing", e, n) for e, n in out_edges]
                      + [clue("incoming", e, n) for e, n in in_edges]),
        }


@app.get("/api/personas/{pid}/qc-issues", tags=["persona"], summary="质检问题记录 (open=未处理)")
async def list_qc_issues(pid: str, status: str = "open"):
    """质检问题清单: 前端据此渲染树/内容块的红黄绿点.

    status: open(默认)/resolved/dismissed/all
    """
    from sqlalchemy import select

    from virtual_human.db import async_session_factory
    from virtual_human.qc import QcIssue

    async with async_session_factory() as session:
        q = select(QcIssue).where(QcIssue.persona_id == pid)
        if status != "all":
            q = q.where(QcIssue.status == status)
        rows = (await session.execute(q.order_by(QcIssue.checked_at.desc()))).scalars().all()
        return [
            {
                "id": r.id, "entity_type": r.entity_type, "entity_id": r.entity_id,
                "entity_title": r.entity_title, "severity": r.severity, "category": r.category,
                "message": r.message, "status": r.status,
                "checked_at": str(r.checked_at) if r.checked_at else None,
                "check_run_id": r.check_run_id,
            }
            for r in rows
        ]


@app.patch("/api/qc-issues/{issue_id}", tags=["persona"], summary="处理质检问题 (resolved/dismissed)")
async def update_qc_issue(issue_id: str, body: dict):
    """手动标记问题状态: {"status": "resolved"|"dismissed"|"open"}."""
    from datetime import datetime

    from fastapi import HTTPException as _HTTP404
    from sqlalchemy import select

    from virtual_human.db import async_session_factory
    from virtual_human.qc import QcIssue

    new_status = body.get("status")
    if new_status not in ("open", "resolved", "dismissed"):
        raise _HTTP404(400, "status 必须是 open/resolved/dismissed")

    async with async_session_factory() as session:
        issue = await session.get(QcIssue, issue_id)
        if issue is None:
            raise _HTTP404(404, f"qc_issue 不存在: {issue_id}")
        issue.status = new_status
        issue.resolved_at = datetime.utcnow() if new_status == "resolved" else None
        await session.commit()
        return {"ok": True, "status": new_status}


# ---------------------------------------------------------------------
# 1. 骨架 → 生成人设 (后台任务)
# ---------------------------------------------------------------------
@app.post("/api/personas/ingest", summary="生成人设 (后台任务, 立即返回 job_id)")
async def ingest_persona(req: IngestRequest):
    """提交铸造任务后立即返回; 用 GET /api/jobs/{job_id} 轮询进度与日志."""
    from vh_api.job_service import add_log, add_report, new_job_state, persist_job, register

    # 骨架过长会直接超出模型上下文 (实测 >8000 字容易 400 invalid_request)
    if len(req.skeleton) > 8000:
        raise HTTPException(
            400,
            f"骨架提示词过长 ({len(req.skeleton)} 字, 上限 8000)。"
            "骨架应是一段自然语言人设描述 (年龄/职业/家庭/关键事件/性格), 请精简后重试。",
        )

    state = register(new_job_state("ingest", {"skeleton": req.skeleton, "model": req.model}))

    def _log(msg: str) -> None:
        add_log(state, msg)

    def _chunk(text: str) -> None:
        add_report(state, text, start_pct=10, span=85)

    async def _runner() -> None:
        import logging as _logging

        from vh_generate.generate import ingest_persona_toolcall_async

        _log_ = _logging.getLogger("vh.ingest")
        try:
            persona_id = await ingest_persona_toolcall_async(
                req.skeleton, model=req.model, on_log=_log, on_chunk=_chunk,
            )
            state["persona_id"] = persona_id
            state["result"] = {"persona_id": persona_id}
            state["status"] = "done"
            state["percent"] = 100
            state["message"] = f"铸造完成 persona_id={persona_id[:8]}…"
        except Exception as e:
            # 完整错误: 类型 + message + body (OpenAI 400 常带具体原因)
            detail = getattr(e, "message", None) or str(e)
            body = getattr(e, "body", None)
            if body:
                detail = f"{detail} | body={str(body)[:500]}"
            state["status"] = "error"
            state["message"] = f"{type(e).__name__}: {detail}"[:800]
            _log_.exception("[ingest] 铸造失败 persona_input_len=%d", len(req.skeleton or ""))
        finally:
            import time as _t

            state["finished_at"] = _t.time()
            await persist_job(state)

    spawn_job(_runner())
    return {"status": "started", "job_id": state["id"], "state": state}


# ---------------------------------------------------------------------
# 骨架渐进式铸造: 建人物 + 导入事件 + 按年份逐批生成
# ---------------------------------------------------------------------
class ScaffoldCreateRequest(BaseModel):
    skeleton: str = Field(default="", description="人物骨架描述 (可空; 为空时由 agent 从履历生成)")
    slug: str | None = Field(default=None, description="人物 slug (可空, 自动生成)")
    events: list[dict] = Field(default_factory=list, description="人生事件 JSON 数组")


class ScaffoldGenerateRequest(BaseModel):
    batch_size: int = Field(default=5, ge=1, le=10, description="每批消化的事件数")


@app.post("/api/personas/scaffold", tags=["scaffold"], summary="创建人物并导入骨架事件")
async def scaffold_create(req: ScaffoldCreateRequest):
    """左侧骨架 + 右侧事件 JSON → 建人物 + 事件落库 (events 关联 persona_id)."""
    import uuid as _uuid

    from sqlalchemy import select

    from vh_api import scaffold_service
    from virtual_human.db import async_session_factory
    from virtual_human.models import Persona

    if not req.skeleton.strip() and not req.events:
        raise HTTPException(400, "请至少提供人生履历事件, 骨架可由 agent 生成")
    if len(req.events) > 2000:
        raise HTTPException(400, f"事件过多 ({len(req.events)} 条), 上限 2000")

    # 骨架为空时先占位, agent 生成后会回填姓名
    raw = req.skeleton.strip()
    name = raw.split("，")[0].split(",")[0].strip()[:128] if raw else "待命名"
    slug = (req.slug or f"persona-{_uuid.uuid4().hex[:8]}").strip()[:64]

    async with async_session_factory() as session:
        exists = (
            await session.execute(select(Persona).where(Persona.slug == slug))
        ).scalars().first()
        if exists is not None:
            raise HTTPException(409, f"slug 已存在: {slug}")
        p = Persona(
            slug=slug, name=name, status="live",
            core_identity=raw or None,
            bio_skeleton=raw[:2000] or None,
            voice_style=None, context_token_budget=4000,
        )
        session.add(p)
        await session.commit()
        persona_id = p.id

    n = await scaffold_service.save_events(persona_id, req.events)
    st = await scaffold_service.stats(persona_id)
    return {"persona_id": persona_id, "slug": slug, "name": name, "imported": n, "stats": st}


@app.get("/api/personas/{pid}/scaffold", tags=["scaffold"], summary="骨架事件、性格导向与进度")
async def scaffold_list(pid: str):
    from vh_api import scaffold_service
    from virtual_human.db import async_session_factory
    from virtual_human.models import Persona

    async with async_session_factory() as session:
        p = await session.get(Persona, pid)
        if p is None:
            raise HTTPException(404, "人物不存在")
        persona = {
            "id": p.id, "name": p.name, "slug": p.slug,
            "core_identity": p.core_identity,
            "bio_skeleton": p.bio_skeleton,
            "personality": p.personality or "",
        }

    return {
        "persona": persona,
        "stats": await scaffold_service.stats(pid),
        "events": await scaffold_service.list_events(pid),
    }


@app.post("/api/personas/{pid}/skeleton/generate", tags=["scaffold"], summary="重新生成人物骨架 (后台任务)")
async def skeleton_generate(pid: str):
    """读全部履历重新归纳人物骨架; 用 /api/jobs/{job_id} 轮询."""
    from vh_api import scaffold_service
    from vh_api.job_service import add_log, add_report, new_job_state, persist_job, register

    st0 = await scaffold_service.stats(pid)
    if st0["total"] == 0:
        raise HTTPException(400, "该人物没有导入履历事件, 无法生成骨架")

    state = register(new_job_state("scaffold", {"persona_id": pid, "action": "skeleton"}))
    state["persona_id"] = pid
    state["percent"] = 5
    state["message"] = "生成人物骨架…"

    def _log(msg: str) -> None:
        add_log(state, msg)

    def _chunk(text: str) -> None:
        add_report(state, text, start_pct=5, span=85)

    async def _runner() -> None:
        import logging as _lg

        from vh_generate.generate.scaffold_gen import generate_skeleton_async

        log = _lg.getLogger("vh.scaffold")
        try:
            skeleton = await generate_skeleton_async(pid, on_log=_log, on_chunk=_chunk)
            state["status"] = "done"
            state["percent"] = 100
            state["message"] = f"人物骨架已生成 ({len(skeleton)} 字)"
            state["result"] = {"skeleton": skeleton, "length": len(skeleton)}
            log.info("[scaffold] persona=%s 骨架重新生成完成 (%s 字)", pid, len(skeleton))
        except Exception as e:
            state["status"] = "error"
            state["message"] = f"{type(e).__name__}: {str(e)[:300]}"
            log.exception("[scaffold] persona=%s 骨架生成失败", pid)
        finally:
            import time as _t

            state["finished_at"] = _t.time()
            await persist_job(state)

    spawn_job(_runner())
    return {"status": "started", "job_id": state["id"], "state": state, "stats": st0}


class SkeletonUpdateRequest(BaseModel):
    skeleton: str = Field(default="", description="人工修订后的人物骨架; 不能为空")


@app.put("/api/personas/{pid}/skeleton", tags=["scaffold"], summary="保存人工修订的人物骨架")
async def skeleton_update(pid: str, req: SkeletonUpdateRequest):
    from virtual_human.db import async_session_factory
    from virtual_human.models import Persona

    text = req.skeleton.strip()
    if not text:
        raise HTTPException(400, "人物骨架不能为空")
    async with async_session_factory() as session:
        p = await session.get(Persona, pid)
        if p is None:
            raise HTTPException(404, "人物不存在")
        p.core_identity = text
        p.bio_skeleton = text
        await session.commit()
        return {"skeleton": text, "length": len(text)}


class PersonalityUpdateRequest(BaseModel):
    personality: str = Field(default="", description="人工修订后的性格导向提示词; 空串表示清空")


@app.put("/api/personas/{pid}/personality", tags=["scaffold"], summary="保存人工修订的性格导向")
async def personality_update(pid: str, req: PersonalityUpdateRequest):
    from virtual_human.db import async_session_factory
    from virtual_human.models import Persona

    async with async_session_factory() as session:
        p = await session.get(Persona, pid)
        if p is None:
            raise HTTPException(404, "人物不存在")
        p.personality = req.personality.strip() or None
        await session.commit()
        return {"personality": p.personality or "", "length": len(p.personality or "")}


@app.post("/api/personas/{pid}/personality/generate", tags=["scaffold"], summary="重新生成性格导向 (后台任务)")
async def personality_generate(pid: str):
    """读全部履历事件重新归纳性格导向; 用 /api/jobs/{job_id} 轮询."""
    from vh_api import scaffold_service
    from vh_api.job_service import add_log, add_report, new_job_state, persist_job, register

    st0 = await scaffold_service.stats(pid)
    if st0["total"] == 0:
        raise HTTPException(400, "该人物没有导入履历事件, 无法归纳性格导向")

    state = register(new_job_state("scaffold", {"persona_id": pid, "action": "personality"}))
    state["persona_id"] = pid
    state["percent"] = 5
    state["message"] = "归纳性格导向…"

    def _log(msg: str) -> None:
        add_log(state, msg)

    def _chunk(text: str) -> None:
        add_report(state, text, start_pct=5, span=85)

    async def _runner() -> None:
        import logging as _lg

        from vh_generate.generate.scaffold_gen import generate_personality_async

        log = _lg.getLogger("vh.scaffold")
        try:
            personality = await generate_personality_async(pid, on_log=_log, on_chunk=_chunk)
            state["status"] = "done"
            state["percent"] = 100
            state["message"] = f"性格导向已生成 ({len(personality)} 字)"
            state["result"] = {"personality": personality, "length": len(personality)}
            log.info("[scaffold] persona=%s 性格导向重新生成完成 (%s 字)", pid, len(personality))
        except Exception as e:
            state["status"] = "error"
            state["message"] = f"{type(e).__name__}: {str(e)[:300]}"
            log.exception("[scaffold] persona=%s 性格导向生成失败", pid)
        finally:
            import time as _t

            state["finished_at"] = _t.time()
            await persist_job(state)

    spawn_job(_runner())
    return {"status": "started", "job_id": state["id"], "state": state, "stats": st0}


class ScaffoldImportRequest(BaseModel):
    events: list[dict] = Field(default_factory=list, description="人生事件 JSON 数组")


@app.post("/api/personas/{pid}/scaffold/import", tags=["scaffold"], summary="导入/覆盖骨架事件")
async def scaffold_import(pid: str, req: ScaffoldImportRequest):
    from vh_api import scaffold_service

    if not req.events:
        raise HTTPException(400, "事件为空")
    if len(req.events) > 2000:
        raise HTTPException(400, f"事件过多 ({len(req.events)} 条), 上限 2000")
    n = await scaffold_service.save_events(pid, req.events)
    return {"imported": n, "stats": await scaffold_service.stats(pid)}


@app.delete("/api/personas/{pid}/scaffold/events/{event_id}", tags=["scaffold"], summary="删除履历事件")
async def scaffold_event_delete(pid: str, event_id: str):
    from vh_api import scaffold_service

    ok = await scaffold_service.delete_event(pid, event_id)
    if not ok:
        raise HTTPException(404, "履历事件不存在或不属于该人物")
    return {"deleted": 1, "stats": await scaffold_service.stats(pid)}


class ScaffoldInsertRequest(BaseModel):
    events: list[dict] = Field(default_factory=list, description="外部 JSON 事件数组")
    strategy: str = Field(default="insert", description="insert / replace / merge")
    payload_mode: str = Field(default="merge", description="merge 策略下: merge / replace")
    generate: bool = Field(default=True, description="插入后是否后台 LLM 生成记忆/知识/财务")


@app.post("/api/personas/{pid}/scaffold/insert", tags=["scaffold"], summary="按时间策略插入履历事件")
async def scaffold_insert(pid: str, req: ScaffoldInsertRequest):
    """按时间策略写入事件; 可选后台对新增/合并的节点做 LLM 生成."""
    from vh_api import scaffold_service
    from vh_api.job_service import add_log, add_report, new_job_state, persist_job, register

    if not req.events:
        raise HTTPException(400, "事件为空")
    if len(req.events) > 2000:
        raise HTTPException(400, f"事件过多 ({len(req.events)} 条), 上限 2000")
    if req.strategy not in ("insert", "replace", "merge"):
        raise HTTPException(400, "strategy 必须是 insert / replace / merge")
    if req.payload_mode not in ("merge", "replace"):
        raise HTTPException(400, "payload_mode 必须是 merge / replace")

    applied = await scaffold_service.apply_events(
        pid, req.events, strategy=req.strategy, payload_mode=req.payload_mode,
    )
    event_ids = applied.get("event_ids") or []

    if not req.generate or not event_ids:
        return {"status": "inserted", "applied": applied, "stats": await scaffold_service.stats(pid)}

    state = register(new_job_state("scaffold", {
        "persona_id": pid, "action": "insert_generate", "event_ids": event_ids,
    }))
    state["persona_id"] = pid
    state["percent"] = 5
    state["message"] = f"插入 {len(event_ids)} 条履历, 开始生成…"

    def _log(msg: str) -> None:
        add_log(state, msg)

    def _chunk(text: str) -> None:
        add_report(state, text, start_pct=5, span=75)

    async def _runner() -> None:
        import logging as _lg

        from vh_generate.generate.scaffold_gen import (
            finalize_persona_async,
            process_scaffold_events_async,
        )
        from virtual_human.db import async_session_factory
        from virtual_human.models import Persona

        log = _lg.getLogger("vh.scaffold")
        try:
            async with async_session_factory() as s:
                p = await s.get(Persona, pid)
                core = (p.core_identity if p else "") or ""
                personality = (p.personality if p else "") or ""

            events = await scaffold_service.get_events_by_ids(pid, event_ids)
            total = len(events)
            done = 0

            for i in range(0, total, 5):
                batch = events[i:i + 5]
                saved = await process_scaffold_events_async(
                    pid, batch, core, personality,
                    on_log=_log, on_chunk=_chunk,
                )

                # 校验实际落库: LLM 偶发空输出时重试一次
                batch_ids = [e["id"] for e in batch]
                after = await scaffold_service.get_events_by_ids(pid, batch_ids)
                missing = [e for e in after if not e.get("processed")]
                if missing:
                    _log(f"  ⚠ {len(missing)} 条未生成记忆, 重试一次…")
                    saved += await process_scaffold_events_async(
                        pid, missing, core, personality,
                        on_log=_log, on_chunk=_chunk,
                    )
                    after = await scaffold_service.get_events_by_ids(pid, batch_ids)
                    missing = [e for e in after if not e.get("processed")]
                    if missing:
                        _log(f"  ⚠ 仍有 {len(missing)} 条记忆未生成, 补生成展开内容…")
                        from vh_generate.generate.scaffold_gen import expand_scaffold_events_async

                        await expand_scaffold_events_async(
                            pid, batch_size=3, force=True,
                            event_ids=[e["id"] for e in missing],
                            on_log=_log, on_chunk=_chunk,
                        )

                done += len(batch)
                state["percent"] = min(85, 8 + int(done * 75 / max(total, 1)))
                state["message"] = f"已生成 {done}/{total} 条事件 (新增记忆 {saved})"
                _log(f"  批次 {i // 5 + 1}: {len(batch)} 条, 新增记忆 {saved}")

            state["message"] = "收尾: 重建知识节点与财务大事记…"
            state["percent"] = 90
            res = await finalize_persona_async(pid, on_log=_log, on_chunk=_chunk)

            state["status"] = "done"
            state["percent"] = 100
            state["message"] = (
                f"插入完成: {applied.get('inserted', 0)} 新增 / {applied.get('updated', 0)} 合并 / "
                f"{applied.get('deleted', 0)} 删除 · 知识节点 {res.get('knowledge_nodes', 0)} "
                f"· 财务 {res.get('finance_events', 0)}"
            )
            state["result"] = {**applied, **res}
            log.info("[scaffold] persona=%s 插入生成完成 %s", pid, state["result"])
        except Exception as e:
            state["status"] = "error"
            state["message"] = f"{type(e).__name__}: {str(e)[:300]}"
            log.exception("[scaffold] persona=%s 插入生成失败", pid)
        finally:
            import time as _t

            state["finished_at"] = _t.time()
            await persist_job(state)

    spawn_job(_runner())
    return {"status": "started", "job_id": state["id"], "applied": applied}


class ScaffoldExpandRequest(BaseModel):
    batch_size: int = Field(default=8, ge=1, le=20, description="每批展开的事件数")
    force: bool = Field(default=True, description="是否清空已有展开并重新生成")


@app.post("/api/personas/{pid}/scaffold/expand", tags=["scaffold"], summary="重新展开人物履历 (后台任务)")
async def scaffold_expand(pid: str, req: ScaffoldExpandRequest):
    """为履历事件重新生成 life_scaffold_contents (摘要 + 正文)."""
    from vh_api import scaffold_service
    from vh_api.job_service import add_log, add_report, new_job_state, persist_job, register

    st0 = await scaffold_service.stats(pid)
    if st0["total"] == 0:
        raise HTTPException(400, "该人物没有履历事件")

    state = register(new_job_state("scaffold", {"persona_id": pid, "action": "expand"}))
    state["persona_id"] = pid
    state["percent"] = 5
    state["message"] = "展开人物履历…"

    def _log(msg: str) -> None:
        add_log(state, msg)

    def _chunk(text: str) -> None:
        add_report(state, text, start_pct=5, span=85)

    async def _runner() -> None:
        import logging as _lg

        from vh_generate.generate.scaffold_gen import expand_scaffold_events_async

        log = _lg.getLogger("vh.scaffold")
        try:
            n = await expand_scaffold_events_async(
                pid, batch_size=req.batch_size, force=req.force,
                on_log=_log, on_chunk=_chunk,
            )
            state["status"] = "done"
            state["percent"] = 100
            state["message"] = f"履历展开完成: {n} 条内容"
            state["result"] = {"event_contents": n}
            log.info("[scaffold] persona=%s 履历展开完成 %s 条", pid, n)
        except Exception as e:
            state["status"] = "error"
            state["message"] = f"{type(e).__name__}: {str(e)[:300]}"
            log.exception("[scaffold] persona=%s 履历展开失败", pid)
        finally:
            import time as _t

            state["finished_at"] = _t.time()
            await persist_job(state)

    spawn_job(_runner())
    return {"status": "started", "job_id": state["id"], "state": state, "stats": st0}


@app.post(
    "/api/personas/{pid}/scaffold/events/{event_id}/expand",
    tags=["scaffold"], summary="重新生成单条履历展开 (后台任务)",
)
async def scaffold_event_expand(pid: str, event_id: str):
    """只重新生成指定履历事件的摘要与正文, 走独立接口."""
    from vh_api import scaffold_service
    from vh_api.job_service import add_log, add_report, new_job_state, persist_job, register

    events = await scaffold_service.list_events(pid, limit=2000)
    ev = next((e for e in events if e["id"] == event_id), None)
    if ev is None:
        raise HTTPException(404, "履历事件不存在或不属于该人物")

    state = register(new_job_state(
        "scaffold",
        {"persona_id": pid, "action": "expand_event", "event_id": event_id},
    ))
    state["persona_id"] = pid
    state["percent"] = 10
    state["message"] = f"重新生成履历: {ev.get('start_year') or ev.get('start_raw')} {ev.get('event_type')}"

    def _log(msg: str) -> None:
        add_log(state, msg)

    def _chunk(text: str) -> None:
        add_report(state, text, start_pct=10, span=80)

    async def _runner() -> None:
        import logging as _lg

        from vh_generate.generate.scaffold_gen import expand_scaffold_events_async

        log = _lg.getLogger("vh.scaffold")
        try:
            n = await expand_scaffold_events_async(
                pid, batch_size=1, force=True, event_ids=[event_id],
                on_log=_log, on_chunk=_chunk,
            )
            state["status"] = "done"
            state["percent"] = 100
            state["message"] = (
                f"履历已重新生成: {ev.get('start_year') or ev.get('start_raw')} "
                f"{ev.get('event_type')} ({n} 条内容)"
            )
            state["result"] = {"event_contents": n, "event_id": event_id}
            log.info("[scaffold] persona=%s event=%s 展开完成 %s 条", pid, event_id[:8], n)
        except Exception as e:
            state["status"] = "error"
            state["message"] = f"{type(e).__name__}: {str(e)[:300]}"
            log.exception("[scaffold] persona=%s event=%s 展开失败", pid, event_id[:8])
        finally:
            import time as _t

            state["finished_at"] = _t.time()
            await persist_job(state)

    spawn_job(_runner())
    return {"status": "started", "job_id": state["id"], "state": state}


@app.post("/api/personas/{pid}/scaffold/generate", tags=["scaffold"], summary="渐进式生成 (后台任务)")
async def scaffold_generate(pid: str, req: ScaffoldGenerateRequest):
    """按年份顺序逐批消化未处理事件, 生成情景记忆; 用 /api/jobs/{job_id} 轮询."""
    from vh_api.job_service import add_log, add_report, new_job_state, persist_job, register
    from vh_api import scaffold_service

    st0 = await scaffold_service.stats(pid)
    if st0["total"] == 0:
        raise HTTPException(400, "该人物没有导入骨架事件, 请先导入")

    state = register(new_job_state("scaffold", {"persona_id": pid, "batch_size": req.batch_size}))
    state["persona_id"] = pid
    state["percent"] = 5

    def _log(msg: str) -> None:
        add_log(state, msg)

    def _chunk(text: str) -> None:
        add_report(state, text, start_pct=5, span=85)

    async def _runner() -> None:
        import logging as _lg

        from vh_generate.generate import process_scaffold_events_async
        from vh_generate.generate.scaffold_gen import (
            expand_scaffold_events_async,
            finalize_persona_async,
            generate_personality_async,
            generate_skeleton_async,
        )
        from vh_generate.generate.scaffold_graph import ScaffoldHandlers, compile_scaffold_graph
        from virtual_human.db import async_session_factory
        from virtual_human.models import FinanceEvent, KbNode, Persona
        from virtual_human.scaffold import LifeScaffoldContent
        from vh_api.checkpointer import async_checkpointer

        log = _lg.getLogger("vh.scaffold")
        batch_no = 0

        async def h_skeleton(s: dict) -> dict:
            async with async_session_factory() as db:
                p = await db.get(Persona, pid)
                existing = (p.core_identity if p else "") or ""
            if existing.strip():
                _log("  ✓ 已有人物骨架, 跳过生成")
                return {"core_identity": existing}
            _log("  ⚙ 人物骨架为空 → 先读履历生成骨架")
            skeleton = await generate_skeleton_async(pid, on_log=_log, on_chunk=_chunk)
            state["percent"] = max(state.get("percent", 5), 10)
            return {"core_identity": skeleton}

        async def h_personality(s: dict) -> dict:
            async with async_session_factory() as db:
                p = await db.get(Persona, pid)
                core = (p.core_identity if p else "") or ""
                existing = (p.personality if p else "") or ""
            if existing.strip():
                _log("  ✓ 已有人物性格导向, 跳过生成")
                return {"personality": existing, "core_identity": core}
            _log("  ⚙ 性格导向为空 → 先读履历归纳性格")
            personality = await generate_personality_async(pid, on_log=_log, on_chunk=_chunk)
            state["percent"] = max(state.get("percent", 5), 12)
            return {"personality": personality, "core_identity": core}

        async def h_fetch(s: dict) -> dict:
            nonlocal batch_no
            batch = await scaffold_service.fetch_pending_batch(pid, req.batch_size)
            st = await scaffold_service.stats(pid)
            if batch:
                batch_no += 1
                _log(f"  批次 {batch_no}: {len(batch)} 条事件 "
                     f"({batch[0].get('start_year')}~{batch[-1].get('start_year')})")
            state["percent"] = max(state.get("percent", 12), min(98, int(st["processed"] * 100 / max(st["total"], 1))))
            state["message"] = f"已消化 {st['processed']}/{st['total']} 条事件"
            # 清空上次 checkpointer 里遗留的 error, 否则重跑会被条件边直接终止
            return {"batch": batch, "total": st["total"], "processed": st["processed"], "error": ""}

        async def h_generate(s: dict) -> dict:
            batch = s.get("batch") or []
            saved = await process_scaffold_events_async(
                pid, batch,
                s.get("core_identity", ""),
                personality=s.get("personality"),
                on_log=_log, on_chunk=_chunk,
            )
            st = await scaffold_service.stats(pid)
            _log(f"  本批新增记忆 {saved} 条, 累计 {st['processed']}/{st['total']}")
            if saved == 0:
                _log("  ⚠ 本批未产出记忆, 终止以避免死循环")
                return {"generated": 0, "processed": st["processed"], "batch": [], "error": "no_progress"}
            return {"generated": saved, "processed": st["processed"], "batch": []}

        async def h_expand(s: dict) -> dict:
            _log("  ⚙ 展开人物履历: 为每条事件生成摘要与正文…")
            state["percent"] = max(state.get("percent", 12), 90)
            state["message"] = "展开人物履历 (摘要/正文)…"
            n = await expand_scaffold_events_async(pid, on_log=_log, on_chunk=_chunk)
            return {"event_contents": n}

        async def h_finalize(s: dict) -> dict:
            _log("  ⚙ 最后一步: 从履历与记忆归纳知识节点、抽取财务大事记…")
            state["percent"] = max(state.get("percent", 12), 95)
            state["message"] = "收尾: 生成知识节点与财务大事记…"
            res = await finalize_persona_async(pid, on_log=_log, on_chunk=_chunk)
            return res

        try:
            handlers = ScaffoldHandlers(
                ensure_skeleton=h_skeleton,
                ensure_personality=h_personality,
                fetch_batch=h_fetch,
                generate_memories=h_generate,
                expand_events=h_expand,
                finalize=h_finalize,
            )

            # 配 Postgres checkpointer: 中断可按 thread_id 续跑
            async with async_checkpointer() as saver:
                graph = compile_scaffold_graph(handlers, checkpointer=saver)
                cfg = {"configurable": {"thread_id": f"scaffold-{pid}"}}
                init = {
                    "persona_id": pid, "batch_size": req.batch_size,
                    "generated": 0, "finished": False, "error": "",
                }
                async for update in graph.astream(init, cfg, stream_mode="updates"):
                    for node, patch in (update or {}).items():
                        if isinstance(patch, dict) and patch.get("error") == "no_progress":
                            _log("  ⚠ 状态机终止: 无新增记忆")

            st = await scaffold_service.stats(pid)
            from sqlalchemy import func, select

            async with async_session_factory() as db:
                kb_count = (
                    await db.execute(
                        select(func.count()).select_from(KbNode).where(KbNode.persona_id == pid)
                    )
                ).scalar() or 0
                fin_count = (
                    await db.execute(
                        select(func.count()).select_from(FinanceEvent).where(FinanceEvent.persona_id == pid)
                    )
                ).scalar() or 0
                content_count = (
                    await db.execute(
                        select(func.count()).select_from(LifeScaffoldContent).where(
                            LifeScaffoldContent.persona_id == pid
                        )
                    )
                ).scalar() or 0

            state["status"] = "done"
            state["percent"] = 100
            state["message"] = (
                f"生成完成: {st['processed']}/{st['total']} 条履历已生成记忆 · "
                f"履历展开 {content_count} · 知识节点 {kb_count} · 财务大事 {fin_count}"
            )
            state["result"] = {
                "processed": st["processed"], "total": st["total"],
                "event_contents": content_count,
                "knowledge_nodes": kb_count, "finance_events": fin_count,
            }
            log.info("[scaffold] persona=%s 完成 %s/%s (langgraph)", pid, st["processed"], st["total"])
        except Exception as e:
            state["status"] = "error"
            state["message"] = f"{type(e).__name__}: {str(e)[:300]}"
            log.exception("[scaffold] persona=%s 失败", pid)
        finally:
            import time as _t

            state["finished_at"] = _t.time()
            await persist_job(state)

    spawn_job(_runner())
    return {"status": "started", "job_id": state["id"], "state": state, "stats": st0}


@app.get("/api/jobs", tags=["jobs"], summary="任务列表 (铸造/质检/修复/修改/骨架生成)")
async def jobs_list(type: str | None = None, limit: int = 50):
    """按类型过滤: ingest/check/fix/edit; 含任务状态与产出的人物 id."""
    from vh_api.job_service import list_jobs

    return await list_jobs(job_type=type, limit=limit)


@app.get("/api/jobs/{job_id}", tags=["jobs"], summary="任务详情/进度 (前端 5s 轮询)")
async def job_detail(job_id: str):
    from vh_api.job_service import get_job

    st = await get_job(job_id)
    if st is None:
        raise HTTPException(404, f"job 不存在: {job_id}")
    return st


# ---------------------------------------------------------------------
# 2. 检查 / 修改
# ---------------------------------------------------------------------
# 质检任务 (后台执行 + 进度/报告查询)
_CHECK_TASKS: dict[str, dict] = {}


@app.post("/api/personas/{pid}/check", summary="启动质检 (后台任务, 立即返回)")
async def start_check(pid: str):
    """提交质检任务后立即返回; 用 GET /check/status 轮询报告与状态."""
    import time

    cur = _CHECK_TASKS.get(pid)
    if cur and cur["status"] == "running":
        return {"status": "running", "already_running": True, "state": cur}

    _CHECK_TASKS[pid] = {
        "status": "running", "percent": 5, "report": "", "logs": [],
        "message": "启动中…", "issues": [],
        "started_at": time.time(), "finished_at": None,
    }
    state = _CHECK_TASKS[pid]

    def _log(msg: str) -> None:
        state["logs"].append(str(msg).rstrip())
        if len(state["logs"]) > 300:
            state["logs"] = state["logs"][-300:]
        state["message"] = str(msg).strip()[:100]

    def _chunk(text: str) -> None:
        state["report"] += text
        # 粗略进度: 报告流式输出阶段映射 15%~90%
        n = len(state["report"])
        state["percent"] = min(90, 15 + int(n / 40))

    async def _runner() -> None:
        from vh_generate.generate import check_persona_async

        try:
            import logging

            logging.getLogger("vh.check").info("[check] persona=%s 开始", pid)
            issues = await check_persona_async(pid, fix=False, on_log=_log, on_chunk=_chunk)
            state["issues"] = issues
            state["status"] = "done"
            state["percent"] = 100
            state["message"] = f"检查完成: {len(issues)} 项内置规则提示"
            logging.getLogger("vh.check").info("[check] persona=%s 完成, issues=%d", pid, len(issues))
        except Exception as e:
            state["status"] = "error"
            state["message"] = f"{type(e).__name__}: {e}"
            import logging

            logging.getLogger("vh.check").exception("[check] persona=%s 失败", pid)
        finally:
            import time as _t

            state["finished_at"] = _t.time()

    spawn_job(_runner())
    return {"status": "started", "state": state}


@app.get("/api/personas/{pid}/check/status", summary="查询质检状态/报告 (前端 2s 轮询)")
async def check_status(pid: str):
    """状态: idle / running / done / error; report 为累积的 Markdown 报告文本."""
    st = _CHECK_TASKS.get(pid)
    if st is None:
        return {"status": "idle", "percent": 0, "report": "", "logs": [],
                "message": "", "issues": [], "started_at": None, "finished_at": None}
    return st


# ---------------------------------------------------------------------
# 修复任务 (后台执行 + 进度查询)
# ---------------------------------------------------------------------
_FIX_TASKS: dict[str, dict] = {}   # persona_id → 任务状态


def _task_state(pid: str) -> dict:
    st = _FIX_TASKS.get(pid)
    if st is None:
        return {"status": "idle", "progress": 0, "total": 0, "percent": 0,
                "message": "", "logs": [], "started_at": None, "finished_at": None}
    return st


@app.post("/api/personas/{pid}/fix", summary="启动自动修复 (后台任务, 立即返回)")
async def start_fix(pid: str):
    """提交修复任务后立即返回; 用 GET /fix/status 轮询进度与日志."""
    import time

    cur = _FIX_TASKS.get(pid)
    if cur and cur["status"] == "running":
        return {"status": "running", "already_running": True, "state": cur}

    _FIX_TASKS[pid] = {
        "status": "running", "progress": 0, "total": 0, "percent": 0,
        "message": "启动中…", "logs": [], "started_at": time.time(), "finished_at": None,
    }
    state = _FIX_TASKS[pid]

    def _log(msg: str) -> None:
        state["logs"].append(str(msg).rstrip())
        if len(state["logs"]) > 300:
            state["logs"] = state["logs"][-300:]
        state["message"] = str(msg).strip()[:100]

    def _progress(done: int, total: int) -> None:
        state["progress"] = done
        state["total"] = total
        state["percent"] = int(done * 100 / total) if total else 0

    async def _runner() -> None:
        from vh_generate.generate import fix_persona_issues_async

        try:
            res = await fix_persona_issues_async(
                pid, on_log=_log, on_chunk=lambda c: None, on_progress=_progress,
            )
            state["status"] = "done"
            state["percent"] = 100
            state["message"] = (
                f"修复完成: 处理 {res.get('open_count', 0)} 条, "
                f"标记已处理 {res.get('resolved_count', 0)} 条"
            )
        except Exception as e:
            state["status"] = "error"
            state["message"] = f"{type(e).__name__}: {e}"
        finally:
            import time as _t

            state["finished_at"] = _t.time()

    spawn_job(_runner())
    return {"status": "started", "state": state}


@app.get("/api/personas/{pid}/fix/status", summary="查询修复进度/状态 (前端 2s 轮询)")
async def fix_status(pid: str):
    """状态: idle / running / done / error; percent 0-100; logs 为过程日志."""
    return _task_state(pid)


_EDIT_TASKS: dict[str, dict] = {}


@app.post("/api/personas/{pid}/edit", summary="下达修改 (后台任务, 立即返回)")
async def start_edit(pid: str, req: EditRequest):
    """提交修改任务后立即返回; 用 GET /edit/status 轮询进度与过程日志."""
    import time

    cur = _EDIT_TASKS.get(pid)
    if cur and cur["status"] == "running":
        return {"status": "running", "already_running": True, "state": cur}

    _EDIT_TASKS[pid] = {
        "status": "running", "percent": 5, "message": "启动中…",
        "instruction": req.instruction, "logs": [], "report": "",
        "started_at": time.time(), "finished_at": None,
    }
    state = _EDIT_TASKS[pid]

    def _log(msg: str) -> None:
        state["logs"].append(str(msg).rstrip())
        if len(state["logs"]) > 300:
            state["logs"] = state["logs"][-300:]
        state["message"] = str(msg).strip()[:100]

    def _chunk(text: str) -> None:
        state["report"] += text
        n = len(state["report"])
        state["percent"] = min(90, 15 + int(n / 40))

    def _progress(done: int, total: int) -> None:
        if total:
            state["percent"] = max(state["percent"], min(95, int(done * 100 / total)))

    async def _runner() -> None:
        from vh_generate.generate import edit_persona_async

        try:
            ok = await edit_persona_async(
                pid, req.instruction,
                node_ids=req.node_ids or None,
                on_log=_log, on_chunk=_chunk, on_progress=_progress,
            )
            state["status"] = "done"
            state["percent"] = 100
            state["message"] = "修改完成" if ok else "修改结束 (轮数用尽)"
        except Exception as e:
            state["status"] = "error"
            state["message"] = f"{type(e).__name__}: {e}"
        finally:
            import time as _t

            state["finished_at"] = _t.time()

    spawn_job(_runner())
    return {"status": "started", "state": state}


@app.get("/api/personas/{pid}/edit/status", summary="查询修改任务进度 (前端 5s 轮询)")
async def edit_status(pid: str):
    st = _EDIT_TASKS.get(pid)
    if st is None:
        return {"status": "idle", "percent": 0, "message": "", "logs": [], "report": "",
                "started_at": None, "finished_at": None}
    return st


# ---------------------------------------------------------------------
# 3. 模拟对话 (SSE: recall → chunk×N → done) + checkpoint 持久化
# ---------------------------------------------------------------------
def _restore_agent_history(agent, rows: list[dict]) -> None:
    """把 DB 里的消息恢复进 agent.history (summary → SystemMessage)."""
    from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

    history = []
    for r in rows:
        if r["role"] == "user":
            history.append(HumanMessage(content=r["content"]))
        elif r["role"] == "assistant":
            history.append(AIMessage(content=r["content"]))
        elif r["role"] == "summary":
            history.append(SystemMessage(content=f"[既往对话要点]\n{r['content']}"))
    agent.history = history


@app.post("/api/personas/{pid}/chat", summary="模拟对话 (SSE: recall=记忆装载, chunk=正文, done=session_id)")
async def chat_persona(pid: str, req: ChatRequest) -> StreamingResponse:
    try:
        s = await _get_or_create_session(pid, req.session_id)
    except KeyError:
        raise HTTPException(404, f"persona 不存在: {pid}")

    async def gen() -> AsyncIterator[str]:
        from vh_api import chat_service

        sid = s.thread_id

        # ① checkpoint 恢复: 首次进入该会话时从 DB 载回历史
        if not s.agent.history and not getattr(s, "_restored", False):
            rows = await chat_service.load_history(pid, sid)
            if rows:
                _restore_agent_history(s.agent, rows)
            s._restored = True

        # ② 落库 user 消息 (记录引用的文档, 供历史恢复时回显)
        await chat_service.append_message(
            pid, sid, "user", req.message,
            {"docs": req.docs} if req.docs else None,
        )

        # ③ 上下文压缩 (超预算×2.5 → LLM 摘要折叠旧消息, 并写回 checkpoint)
        try:
            ev = await s.agent.compress_history_if_needed()
            if ev.get("summarized"):
                from langchain_core.messages import SystemMessage

                summary_msg = next(
                    (m for m in s.agent.history if isinstance(m, SystemMessage) and "既往对话要点" in str(m.content)),
                    None,
                )
                if summary_msg is not None:
                    removed = await chat_service.replace_with_summary(
                        pid, sid, str(summary_msg.content).replace("[既往对话要点]\n", "")
                    )
                    yield _sse("compress", {"before": ev["before"], "after": ev["after"], "removed": removed})
        except Exception as e:
            logging.getLogger("vh.chat").warning("[chat] 压缩失败(忽略): %s", e)

        # ④ 渐进式工具流: thinking / tool_start / tool_end / chunk
        #    引用的文档以提示块附在问题后, 让 agent 按需 ReadDocArgs 读取
        user_input = req.message
        if req.docs:
            refs = "、".join(req.docs[:8])
            user_input = (
                f"{req.message}\n\n[引用文档] {refs}"
                "（在本人 docs 目录，请按需用 ReadDocArgs 读取）"
            )
        answer_parts: list[str] = []
        trace: list[dict] = []
        tool_names: list[str] = []
        tool_blocks: list[dict] = []

        async for ev in s.agent.stream_tools(user_input, skip_compress=True):
            t = ev.get("type")
            if t == "chunk":
                text = str(ev.get("text") or "")
                answer_parts.append(text)
                yield _sse("chunk", text)
            elif t == "thinking":
                yield _sse("thinking", {"text": ev.get("text") or ""})
            elif t == "thinking_start":
                yield _sse("thinking_start", {"turn": ev.get("turn")})
            elif t == "thinking_end":
                yield _sse("thinking_end", {
                    "turn": ev.get("turn"),
                    "duration_ms": ev.get("duration_ms"),
                })
            elif t == "tool_start":
                yield _sse("tool_start", ev)
            elif t == "tool_end":
                yield _sse("tool_end", ev)
                name = ev.get("name") or ""
                if name not in tool_names:
                    tool_names.append(name)
                tool_blocks.extend(ev.get("blocks") or [])
            elif t == "trace":
                trace = ev.get("trace") or []

        # ⑤ 落库 assistant 消息 (带本轮轨迹: 思考/工具/耗时/命中块)
        answer = "".join(answer_parts)
        recall_payload = {
            "trace": trace,
            "tools": tool_names,
            "blocks": tool_blocks,
        }
        await chat_service.append_message(pid, sid, "assistant", answer, recall_payload)

        yield _sse("done", {"session_id": sid})

    return StreamingResponse(gen(), media_type="text/event-stream")


@app.get("/api/personas/{pid}/chat/history", tags=["chat"], summary="对话历史 (checkpoint 恢复)")
async def chat_history(pid: str, session_id: str | None = None):
    """不传 session_id 时返回该人物最近一次会话."""
    from vh_api import chat_service

    sid = session_id or await chat_service.latest_session_id(pid)
    if not sid:
        return {"session_id": None, "messages": []}
    rows = await chat_service.load_history(pid, sid)
    return {"session_id": sid, "messages": rows}


@app.delete("/api/personas/{pid}/chat/history", tags=["chat"], summary="清空对话 (checkpoint)")
async def clear_chat_history(pid: str, session_id: str):
    from vh_api import chat_service

    n = await chat_service.clear_session(pid, session_id)
    return {"deleted": n}


@app.delete("/api/sessions/{session_id}", tags=["chat"], summary="结束会话")
async def close_session(session_id: str):
    s = _SESSIONS.pop(session_id, None)
    return {"closed": s is not None}


# ---------------------------------------------------------------------
# Agent 知识文档 (RustFS 对象存储: {persona_id}/docs/...)
# 读: docx 抽文本 / 下载原始文件; 写: 原始字节覆盖上传; agent 侧可读可覆盖
# ---------------------------------------------------------------------
def _doc_storage():
    """公共入口: 延迟导入 storage; 未配置对象存储时统一 503."""
    from virtual_human import storage

    if not settings.object_storage_enabled:
        raise HTTPException(503, "对象存储未配置: 需要 RUSTFS_ENDPOINT / RUSTFS_ACCESS_KEY")
    return storage


@app.get("/api/personas/{pid}/docs", tags=["docs"], summary="文档列表 (agent docs 目录)")
async def list_agent_docs(pid: str, path: str = ""):
    storage = _doc_storage()
    try:
        metas = await storage.alist_docs(pid, path)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"读取对象存储失败: {e}")
    return {"path": path, "docs": [asdict(m) for m in metas]}


@app.put("/api/personas/{pid}/docs", tags=["docs"], summary="上传/覆盖文档 (原始字节 body)")
async def put_agent_doc(pid: str, path: str, request: Request):
    storage = _doc_storage()
    data = await request.body()
    if not data:
        raise HTTPException(400, "空文件")
    if len(data) > 20 * 1024 * 1024:
        raise HTTPException(413, "文件过大 (上限 20MB)")
    try:
        rel = await storage.awrite_doc(pid, path, data, storage.guess_content_type(path))
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"写入对象存储失败: {e}")
    return {"ok": True, "path": rel, "size": len(data)}


@app.get("/api/personas/{pid}/docs/text", tags=["docs"], summary="抽取文档正文 (docx 转文本)")
async def get_agent_doc_text(pid: str, path: str):
    storage = _doc_storage()
    try:
        data = await storage.aread_doc(pid, path)
        return {"path": path, "text": storage.extract_text(path, data)}
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(404, f"文档不存在或读取失败: {e}")


@app.get("/api/personas/{pid}/docs/download", tags=["docs"], summary="下载文档原始文件")
async def download_agent_doc(pid: str, path: str):
    from urllib.parse import quote

    storage = _doc_storage()
    try:
        data = await storage.aread_doc(pid, path)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(404, f"文档不存在: {e}")
    name = path.rsplit("/", 1)[-1]
    return Response(
        content=data,
        media_type=storage.guess_content_type(path),
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}"},
    )


@app.delete("/api/personas/{pid}/docs", tags=["docs"], summary="删除文档")
async def delete_agent_doc(pid: str, path: str):
    storage = _doc_storage()
    try:
        await storage.adelete_doc(pid, path)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"删除失败: {e}")
    return {"ok": True}


# ---------------------------------------------------------------------
# 启动
# ---------------------------------------------------------------------
def serve() -> int:
    from vh_cli.io_setup import force_utf8_stdio

    force_utf8_stdio()

    # 服务器控制台日志: 时间戳 + 模块 + 级别 (排查 LLM 调用问题)
    import logging

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)-12s | %(message)s",
        datefmt="%H:%M:%S",
        force=True,
    )
    logging.getLogger("vh.llm").setLevel(logging.INFO)
    logging.getLogger("vh.check").setLevel(logging.INFO)

    parser = argparse.ArgumentParser(prog="vh-api", description="「模拟人类」REST API")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8300)
    parser.add_argument("--reload", action="store_true", help="开发热重载 (子进程同样走本入口)")
    args = parser.parse_args()

    import uvicorn

    print(f"vh-api: http://{args.host}:{args.port}  (docs: /docs)", file=sys.stderr)

    if args.reload:
        # reload 模式: 子进程重新 import 本模块 (头部 policy 已切), 主进程只负责 watch
        uvicorn.run("vh_api.main:app", host=args.host, port=args.port, reload=True)
        return 0

    # 常规模式: 自建 SelectorEventLoop 交给 uvicorn (Windows 下 psycopg async 必需;
    # uvicorn 的 loop=auto 在 Windows 会强制 Proactor, 无视 policy)
    config = uvicorn.Config(
        "vh_api.main:app", host=args.host, port=args.port, loop="asyncio",
        # SSE 长连接会拖住优雅关闭: 最多等 3 秒, 超时强制断开
        timeout_graceful_shutdown=3,
    )
    server = uvicorn.Server(config)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(server.serve())
    except KeyboardInterrupt:
        pass
    finally:
        # 兜底: lifespan 已 dispose 过则秒回; 防止异常退出时连接池 worker 悬挂
        try:
            from virtual_human.db import dispose_async_engine

            loop.run_until_complete(dispose_async_engine())
        except Exception:
            pass
        try:
            loop.run_until_complete(loop.shutdown_asyncgens())
        except Exception:
            pass
        loop.close()
    return 0


if __name__ == "__main__":
    sys.exit(serve())
