"""任务服务 —— 后台任务的状态存储与查询.

运行中: 内存 dict (高频更新, 不写库)
结束/失败: 落 jobs 表 (供任务列表回看)
"""

from __future__ import annotations

import asyncio
import time
import uuid

from sqlalchemy import select

from virtual_human.db import async_session_factory
from virtual_human.jobs import Job

# 运行中任务的内存态: job_id → dict
_LIVE: dict[str, dict] = {}

# 运行中的后台任务: 进程退出时统一取消, 避免 "Task was destroyed but it is pending"
_TASKS: set = set()


def spawn_job(coro) -> asyncio.Task:
    """启动后台任务并登记, 供应用 shutdown 时取消."""
    task = asyncio.create_task(coro)
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)
    return task


async def cancel_running_jobs() -> None:
    """取消所有在跑的后台任务 (应用 shutdown 时调用)."""
    tasks = [t for t in _TASKS if not t.done()]
    # 先把运行中的任务标记为中止, runner 的 finally 会带上这个状态落库
    for st in list(_LIVE.values()):
        if st.get("status") == "running":
            st["status"] = "error"
            st["message"] = "服务关闭, 任务已中止"
    if not tasks:
        return
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    _TASKS.clear()


def new_job_state(job_type: str, payload: dict | None = None) -> dict:
    return {
        "id": str(uuid.uuid4()),
        "job_type": job_type,
        "status": "running",
        "percent": 0,
        "message": "启动中…",
        "persona_id": None,
        "payload": payload or {},
        "result": {},
        "logs": [],
        "report": "",
        "started_at": time.time(),
        "finished_at": None,
    }


def add_log(state: dict, msg: str, limit: int = 300) -> None:
    state["logs"].append(str(msg).rstrip())
    if len(state["logs"]) > limit:
        state["logs"] = state["logs"][-limit:]
    state["message"] = str(msg).strip()[:120]


def add_report(state: dict, text: str, start_pct: int = 15, span: int = 75) -> None:
    """累积报告文本, 并按字数估算进度 (start_pct ~ start_pct+span)."""
    state["report"] += text
    n = len(state["report"])
    state["percent"] = min(start_pct + span, start_pct + int(n / 40))


async def persist_job(state: dict) -> None:
    """把任务终态写入 jobs 表 (新建或更新)."""
    from datetime import datetime

    async with async_session_factory() as session:
        existing = await session.get(Job, state["id"])
        if existing is None:
            row = Job(
                id=state["id"],
                job_type=state["job_type"],
                status=state["status"],
                percent=int(state.get("percent") or 0),
                message=state.get("message"),
                persona_id=state.get("persona_id"),
                payload=state.get("payload"),
                result=state.get("result"),
                logs=state.get("logs"),
                report=state.get("report") or None,
                started_at=datetime.utcfromtimestamp(state["started_at"]) if state.get("started_at") else None,
                finished_at=datetime.utcnow() if state.get("finished_at") else None,
            )
            session.add(row)
        else:
            existing.status = state["status"]
            existing.percent = int(state.get("percent") or 0)
            existing.message = state.get("message")
            existing.persona_id = state.get("persona_id")
            existing.result = state.get("result")
            existing.logs = state.get("logs")
            existing.report = state.get("report") or None
            if state.get("finished_at"):
                existing.finished_at = datetime.utcfromtimestamp(state["finished_at"])
        await session.commit()
    _LIVE.pop(state["id"], None)


def get_live(job_id: str) -> dict | None:
    return _LIVE.get(job_id)


def register(state: dict) -> dict:
    _LIVE[state["id"]] = state
    return state


async def list_jobs(job_type: str | None = None, limit: int = 50) -> list[dict]:
    async with async_session_factory() as session:
        q = select(Job).order_by(Job.created_at.desc()).limit(limit)
        if job_type:
            q = q.where(Job.job_type == job_type)
        rows = (await session.execute(q)).scalars().all()

        live = {jid: st for jid, st in _LIVE.items()}

        out = []
        for r in rows:
            st = live.get(r.id)
            if st is not None:
                out.append(_to_dict(st))
            else:
                out.append({
                    "id": r.id, "job_type": r.job_type, "status": r.status,
                    "percent": r.percent, "message": r.message,
                    "persona_id": r.persona_id, "payload": r.payload,
                    "result": r.result, "logs": r.logs or [],
                    "report": r.report or "",
                    "started_at": r.started_at.timestamp() if r.started_at else None,
                    "finished_at": r.finished_at.timestamp() if r.finished_at else None,
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                })
        # 运行中的任务 (尚未落库) 也置顶展示
        for jid, st in live.items():
            if not any(x["id"] == jid for x in out):
                out.insert(0, _to_dict(st))
        return out


def _to_dict(st: dict) -> dict:
    return {
        "id": st["id"], "job_type": st["job_type"], "status": st["status"],
        "percent": st.get("percent", 0), "message": st.get("message"),
        "persona_id": st.get("persona_id"),
        "payload": st.get("payload"), "result": st.get("result"),
        "logs": st.get("logs") or [], "report": st.get("report") or "",
        "started_at": st.get("started_at"), "finished_at": st.get("finished_at"),
        "created_at": st.get("created_at"),
    }


async def get_job(job_id: str) -> dict | None:
    st = _LIVE.get(job_id)
    if st is not None:
        return _to_dict(st)
    async with async_session_factory() as session:
        r = await session.get(Job, job_id)
        if r is None:
            return None
        return {
            "id": r.id, "job_type": r.job_type, "status": r.status,
            "percent": r.percent, "message": r.message, "persona_id": r.persona_id,
            "payload": r.payload, "result": r.result, "logs": r.logs or [],
            "report": r.report or "",
            "started_at": r.started_at.timestamp() if r.started_at else None,
            "finished_at": r.finished_at.timestamp() if r.finished_at else None,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
