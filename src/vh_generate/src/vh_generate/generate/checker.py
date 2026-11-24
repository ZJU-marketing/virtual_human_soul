"""persona 检查器 —— LLM 根据人物设定, 全面检查库中数据是否合理.

检查项 (LLM 自主判断 + 内置硬规则):
    - 时间线自洽: 年龄/年份/事件先后 (如"52岁"与"教了30年"与"1994毕业"是否吻合)
    - 逻辑一致: 家庭成员关系、职业轨迹、地理变迁
    - 数据完整: 章节缺内容块、记忆缺情绪/时间、财务事件缺对应记忆
    - 数据矛盾: 两个节点对同一事实说法不一、金额与叙事不符
    - 内置硬规则: 现金推算与 finance_events 是否对得上、disclosure_level 范围

三个入口:
    check_persona_async(...)          只检查, 问题写入 qc_issues
    fix_persona_issues_async(...)     按已存在的未处理记录 (qc_issues) 自动修复, 修完标记 resolved
    check_persona_async(..., fix=True) 检查+修复一体 (兼容旧路径)
"""

from __future__ import annotations

import sys
from typing import Callable

from langchain_core.messages import AIMessageChunk, HumanMessage, SystemMessage, ToolMessage
from sqlalchemy import select

from virtual_human.db import async_session_factory
from virtual_human.models import (
    EpisodicMemory,
    FinanceBalance,
    FinanceEvent,
    KbNode,
    KbNodeContent,
    Persona,
)

from vh_generate.generate.agent import make_llm
from vh_generate.generate.editor import _dump_persona
from vh_generate.generate.tools import (
    SeedState,
    TOOL_NAMES,
    TOOL_SCHEMAS,
    execute_tool,
)

LogFn = Callable[..., None]
ChunkFn = Callable[[str], None]


def _stderr_log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


CHECK_SYSTEM = """你是人物数据质检员。下面是一个虚拟人物在数据库里的全部数据, 请对照人物设定与「人生履历」全面检查是否合理。

检查维度:
1. 时间线: 年龄 ↔ 出生/毕业/工作年限 ↔ 各事件年份, 是否自洽 (当前年份 {current_year})
2. 逻辑: 家庭成员(配偶/子女年龄)、职业轨迹、地理变迁是否讲得通
3. 完整性: 章节是否有内容块、记忆是否缺关键要素、大事件是否都有记忆支撑
4. 一致性: 不同节点/记忆对同一事实的描述是否矛盾 (如两处写的买房金额不同)
5. 财务: 事件金额与叙事是否相符、收支与现金结余是否大致合理
6. 文本质量: 是否有 AI 腔、出戏的措辞、明显抄袭模板的痕迹
7. 履历覆盖: 以「人生履历」为事实基准, 检查履历中的重大事件是否有对应记忆/知识节点;
   带金额的事件是否都有 finance_event; 生成内容与履历是否存在年份、金额、亲属关系上的矛盾

输出要求 (两部分, 缺一不可):
A. 问题清单 —— 先输出一个 JSON 代码块 (```json ... ```), 数组元素格式:
   {{"entity_type": "node|content|memory|finance_event|finance_balance|persona",
     "entity_id": "对应id (如 node_id/content_id/memory_id, persona级问题填null)",
     "severity": "high|medium|low",
     "category": "时间线|逻辑|完整性|一致性|财务|文本|履历",
     "message": "一句话问题描述"}}
   只列真问题, 每个问题必须挂到具体实体; 没有问题的维度不编造。
B. 检查报告 —— JSON 块之后, 用 Markdown 逐条展开说明 (标注严重度 [高]/[中]/[低] 和相关 id),
   没有问题的维度明确说"通过", 最后给一句总评。

{fix_clause}"""


def _hard_rules_check(session_data: dict) -> list[str]:
    """内置硬规则: 程序化检查 (不依赖 LLM, 不会漏)."""
    issues: list[str] = []

    # 现金推算
    events = session_data["events"]
    balance = session_data["balance"]
    if balance is not None:
        expect = 0.0
        for e in events:
            if e.event_type in ("expense_buy", "expense_live"):
                expect -= float(e.amount)
            elif e.event_type == "income":
                expect += float(e.amount)
            elif e.event_type == "loan_taken":
                expect += float(e.amount)
        real_cash = float(balance.cash)
        # loan_taken 进了现金又买了房, 推算只做提示性核对
        issues.append(
            f"[低] 财务推算: finance_events 净流量 {expect:,.0f} vs 库存现金 {real_cash:,.0f}"
            f" (差异大时核对 initial_cash 与贷款流向)"
        )

    # 空节点
    empty_nodes = [n for n in session_data["nodes"] if not n.contents]
    for n in empty_nodes:
        issues.append(f"[高] 节点《{n.title}》(node_id={n.id}) 没有任何内容块")

    # 记忆缺时间
    for m in session_data["memories"]:
        if m.occurred_at is None:
            issues.append(f"[中] 记忆《{m.title}》(memory_id={m.id}) 缺发生时间")

    # 披露度越界 (schema 有 CHECK, 这里只提示语义)
    for n in session_data["nodes"]:
        if n.disclosure_level == 0 and n.importance >= 8:
            issues.append(f"[中] 节点《{n.title}》重要度{n.importance}但披露度为0(隐藏), 重要记忆不外显是否有意?")

    return issues


async def check_persona_async(
    persona_id: str,
    fix: bool = False,
    model: str | None = None,
    on_log: LogFn | None = None,
    on_chunk: ChunkFn | None = None,
    max_turns: int = 20,
) -> list[str]:
    """aio 主入口: 检查人物数据合理性. 返回问题清单 (含硬规则+LLM 发现).

    fix=False: 只输出检查报告
    fix=True:  检查后让 LLM 调工具逐项修复 (实时落库+日志)
    """
    import datetime as _dt

    log = on_log or _stderr_log
    emit = on_chunk or (lambda t: None)

    async with async_session_factory() as session:
        persona = await session.get(Persona, persona_id)
        if persona is None:
            raise KeyError(f"persona not found: {persona_id}")

        dump = await _dump_persona(session, persona_id)

        # 人生履历: 质检的事实基准 (原始骨架事件)
        import json as _json

        from virtual_human.scaffold import LifeScaffoldEvent as _LSE

        resume_rows = (
            (
                await session.execute(
                    select(_LSE)
                    .where(_LSE.persona_id == persona_id)
                    .order_by(_LSE.start_year.asc().nullslast(), _LSE.ord.asc())
                )
            )
            .scalars()
            .all()
        )
        resume_lines = []
        for r in resume_rows[:200]:
            payload = r.payload
            ptxt = _json.dumps(payload, ensure_ascii=False) if not isinstance(payload, str) else payload
            resume_lines.append(f"- {r.start_year or r.start_raw or '?'} [{r.event_type}] {ptxt[:160]}")

        # 内置硬规则先跑 (程序化, 必查)
        nodes = (
            (await session.execute(select(KbNode).where(KbNode.persona_id == persona_id)))
            .scalars().all()
        )
        node_contents = {
            n.id: (
                (await session.execute(
                    select(KbNodeContent).where(KbNodeContent.node_id == n.id).order_by(KbNodeContent.seq)
                )).scalars().all()
            )
            for n in nodes
        }
        data = {
            "nodes": [type("N", (), {"title": n.title, "id": n.id,
                                     "disclosure_level": n.disclosure_level,
                                     "importance": n.importance,
                                     "contents": node_contents[n.id]})() for n in nodes],
            "memories": (await session.execute(
                select(EpisodicMemory).where(EpisodicMemory.persona_id == persona_id)
            )).scalars().all(),
            "events": (await session.execute(
                select(FinanceEvent).where(FinanceEvent.persona_id == persona_id)
            )).scalars().all(),
            "balance": await session.get(FinanceBalance, persona_id),
        }

        hard_issues = _hard_rules_check(data)
        if hard_issues:
            log("  ⚙ 内置规则检查:")
            for i in hard_issues:
                log(f"    {i}")

        # LLM 深度检查
        fix_clause = (
            "检查完成后, 对 [高]/[中] 问题调用工具逐项修复 (保持人物核心设定不变), 修完输出修复清单。"
            if fix else "本次只检查不修改。"
        )
        system = CHECK_SYSTEM.format(
            current_year=_dt.date.today().year,
            fix_clause=fix_clause,
        )

        llm = make_llm(model)
        if fix:
            llm = llm.bind_tools(TOOL_SCHEMAS)

        messages: list = [
            SystemMessage(content=system),
            HumanMessage(
                content=f"人物数据:\n\n{dump}\n\n"
                        + (f"人生履历 (原始事实基准):\n" + "\n".join(resume_lines) + "\n\n"
                           if resume_lines else "人生履历: (该人物没有导入履历)\n\n")
                        + ("内置规则已发现:\n" + "\n".join(hard_issues) if hard_issues else "内置规则未发现问题")
            ),
        ]

        all_issues: list[str] = list(hard_issues)
        ai_msg = None

        full_report = ""
        import logging as _logging
        import time as _time

        _log = _logging.getLogger("vh.llm")

        for _turn in range(max_turns):
            chunks: list[AIMessageChunk] = []
            _t0 = _time.time()
            _log.info("[check] LLM turn=%d 请求中… model=%s", _turn + 1, model or "default")
            async for chunk in llm.astream(messages):
                chunks.append(chunk)
                if chunk.content:
                    emit(chunk.content)
                    full_report += chunk.content
            if not chunks:
                _log.warning("[check] LLM turn=%d 无输出", _turn + 1)
                break
            emit("\n")
            ai_msg = _merge(chunks)
            messages.append(ai_msg)
            _log.info(
                "[check] LLM turn=%d 完成 %.2fs, 文本 %d 字, tool_calls=%d",
                _turn + 1, _time.time() - _t0, len(full_report),
                len(ai_msg.tool_calls or []),
            )

            if not fix:
                break   # 只检查: 一轮报告即结束

            tool_calls = ai_msg.tool_calls or []
            if not tool_calls:
                break
            state = SeedState(session=session, log=log)
            state.persona_id = persona_id
            for tc in tool_calls:
                name = tc["name"]
                if name not in TOOL_NAMES or name == "CreatePersonaArgs":
                    result = f"该工具不可用: {name}"
                else:
                    result = await execute_tool(state, name, tc["args"] or {})
                messages.append(ToolMessage(content=str(result), tool_call_id=tc["id"]))

        # ---- 问题条目落库 (qc_issues) ----
        try:
            structured = _parse_issue_json(full_report)
            saved = await _save_issues(session, persona_id, structured, fix)
            log(f"  ⚙ 质检记录: 新增 {saved} 条问题到检查记录表")
        except Exception as e:
            log(f"  ⚙ 问题落库失败(不影响报告): {type(e).__name__}: {str(e)[:60]}")

        return all_issues


def _parse_issue_json(report_text: str) -> list[dict]:
    """从 LLM 报告文本中提取 ```json [...] ``` 问题清单."""
    import json as _json
    import re

    m = re.search(r"```json\s*(\[.*?\])\s*```", report_text, re.DOTALL)
    if not m:
        return []
    try:
        items = _json.loads(m.group(1))
    except _json.JSONDecodeError:
        return []
    out = []
    for it in items if isinstance(items, list) else []:
        if not isinstance(it, dict):
            continue
        et = str(it.get("entity_type") or "").strip()
        sev = str(it.get("severity") or "").strip().lower()
        msg = str(it.get("message") or "").strip()
        if et in ("node", "content", "memory", "finance_event", "finance_balance", "persona") \
                and sev in ("high", "medium", "low") and msg:
            out.append({
                "entity_type": et,
                "entity_id": (str(it.get("entity_id")).strip()
                              if it.get("entity_id") and str(it.get("entity_id")).lower() != "null" else None),
                "severity": sev,
                "category": str(it.get("category") or "").strip()[:32] or None,
                "message": msg,
            })
    return out


async def _save_issues(session, persona_id: str, issues: list[dict], fixed: bool) -> int:
    """写 qc_issues: 同 persona 旧 open 记录标记 resolved (本次是新批次), 再插入新条目."""
    import uuid as _uuid
    from datetime import datetime as _dt

    from sqlalchemy import update as _update

    from virtual_human.qc import QcIssue

    run_id = _uuid.uuid4().hex[:16]
    now = _dt.utcnow()

    # 旧 open → resolved (fix 模式) / dismissed 重检覆盖 (非 fix 也重置, 保持最新一轮)
    await session.execute(
        _update(QcIssue)
        .where(QcIssue.persona_id == persona_id, QcIssue.status == "open")
        .values(status="resolved", resolved_at=now)
    )

    from virtual_human.models import (
        EpisodicMemory as _EM,
        FinanceEvent as _FE,
        KbNode as _KN,
        KbNodeContent as _KNC,
    )

    async def _title_of(entity_type: str, entity_id: str | None) -> str | None:
        if not entity_id:
            return None
        model = {"node": _KN, "content": _KNC, "memory": _EM, "finance_event": _FE}.get(entity_type)
        if model is None:
            return None
        obj = await session.get(model, entity_id)
        return getattr(obj, "title", None) or getattr(obj, "label", None)

    for it in issues:
        session.add(QcIssue(
            persona_id=persona_id,
            entity_type=it["entity_type"],
            entity_id=it["entity_id"],
            entity_title=await _title_of(it["entity_type"], it["entity_id"]),
            severity=it["severity"],
            category=it["category"],
            message=it["message"],
            status="open",
            check_run_id=run_id,
        ))
    await session.commit()
    return len(issues)


def _merge(chunks: list[AIMessageChunk]) -> AIMessageChunk:
    merged = chunks[0]
    for c in chunks[1:]:
        merged = merged + c
    return merged


# ---------------------------------------------------------------------
# 基于检查记录 (qc_issues) 的自动修复
# ---------------------------------------------------------------------
FIX_SYSTEM = """你是人物设定修复员。用户会给出当前人物设定清单 + 一份已确认的问题清单 (来自质检)。
请调用工具逐条修复这些问题。

规则:
1. 只修问题清单里列出的问题, 不改无关内容
2. 保持人物核心设定 (姓名/年龄/身份/家庭结构) 不变, 除非问题本身指向它们
3. 修改正文时保持第一人称、口语化、贴合原人物年代感与性格
4. 同类问题一次性批量修正 (例如所有金额矛盾一并调整)
5. 数字类问题必须自查: 改完后各金额/年份/月供要能对上
6. 全部修完输出简短总结: 修了哪几条、怎么修的
7. 以「人生履历」为事实基准: 履历有而库中缺的重大事件, 用工具补记忆/知识节点/财务事件;
   与履历冲突的描述以履历为准

可用工具: UpdatePersonaArgs / UpdateNodeArgs / UpdateContentArgs / UpdateMemoryArgs /
          DeleteContentArgs / DeleteNodeArgs / DeleteMemoryArgs /
          AddKbNodeArgs / AddContentBlockArgs / AddMemoryArgs / AddFinanceEventArgs
(CreatePersonaArgs 不需要, 人物已存在)"""


async def fix_persona_issues_async(
    persona_id: str,
    model: str | None = None,
    on_log: Callable[..., None] | None = None,
    on_chunk: Callable[[str], None] | None = None,
    on_progress: Callable[[int, int], None] | None = None,
    max_turns: int = 24,
) -> dict:
    """按 qc_issues 里的未处理记录自动修复.

    on_progress(done, total): 每完成一次工具调用回报进度 (total=问题数)
    返回 {fixed: bool, open_count: int, resolved_count: int}
    """
    from sqlalchemy import select as _select

    from virtual_human.qc import QcIssue

    log = on_log or (lambda m: print(m, file=sys.stderr, flush=True))
    emit = on_chunk or (lambda t: None)

    async with async_session_factory() as session:
        issues = (
            (
                await session.execute(
                    _select(QcIssue)
                    .where(QcIssue.persona_id == persona_id, QcIssue.status == "open")
                    .order_by(QcIssue.severity)
                )
            )
            .scalars()
            .all()
        )
        if not issues:
            return {"fixed": False, "open_count": 0, "resolved_count": 0}

        log(f"  ⚙ 待修复问题: {len(issues)} 条")
        for i in issues[:20]:
            log(f"    [{i.severity}] {i.entity_type} {i.entity_title or i.entity_id or ''}: {i.message[:60]}")

        total_issues = len(issues)
        done_units = 0
        if on_progress:
            on_progress(0, total_issues)

        dump = await _dump_persona(session, persona_id)

        import json as _json

        from virtual_human.scaffold import LifeScaffoldEvent as _LSE

        resume_rows = (
            (
                await session.execute(
                    _select(_LSE)
                    .where(_LSE.persona_id == persona_id)
                    .order_by(_LSE.start_year.asc().nullslast(), _LSE.ord.asc())
                )
            )
            .scalars()
            .all()
        )
        resume_lines = []
        for r in resume_rows[:200]:
            payload = r.payload
            ptxt = _json.dumps(payload, ensure_ascii=False) if not isinstance(payload, str) else payload
            resume_lines.append(f"- {r.start_year or r.start_raw or '?'} [{r.event_type}] {ptxt[:160]}")

        issue_lines = "\n".join(
            f"{n}. [{i.severity}] {i.entity_type} (id={i.entity_id or '-'}, "
            f"title={i.entity_title or '-'}) 类别={i.category or '-'}\n   问题: {i.message}"
            for n, i in enumerate(issues, 1)
        )

        llm = make_llm(model).bind_tools(TOOL_SCHEMAS)
        messages: list = [
            SystemMessage(content=FIX_SYSTEM),
            HumanMessage(content=f"当前设定清单:\n\n{dump}\n\n"
                                  + (f"人生履历 (事实基准):\n" + "\n".join(resume_lines) + "\n\n"
                                     if resume_lines else "")
                                  + f"问题清单:\n{issue_lines}"),
        ]

        state = SeedState(session=session, log=log)
        state.persona_id = persona_id
        fixed_any = False
        import logging as _logging
        import time as _time

        _log = _logging.getLogger("vh.llm")

        for _turn in range(max_turns):
            chunks: list[AIMessageChunk] = []
            _t0 = _time.time()
            _log.info("[fix] LLM turn=%d 请求中… model=%s", _turn + 1, model or "default")
            async for chunk in llm.astream(messages):
                chunks.append(chunk)
                if chunk.content:
                    emit(chunk.content)
            if not chunks:
                _log.warning("[fix] LLM turn=%d 无输出", _turn + 1)
                break
            emit("\n")
            ai_msg = _merge(chunks)
            messages.append(ai_msg)
            _log.info(
                "[fix] LLM turn=%d 完成 %.2fs, tool_calls=%d",
                _turn + 1, _time.time() - _t0, len(ai_msg.tool_calls or []),
            )

            tool_calls = ai_msg.tool_calls or []
            if not tool_calls:
                break

            for tc in tool_calls:
                name = tc["name"]
                if name not in TOOL_NAMES or name == "CreatePersonaArgs":
                    result = f"该工具不可用: {name}"
                else:
                    result = await execute_tool(state, name, tc["args"] or {})
                    fixed_any = True
                    done_units += 1
                    if on_progress:
                        on_progress(min(done_units, total_issues), total_issues)
                messages.append(ToolMessage(content=str(result), tool_call_id=tc["id"]))

        # 修复完成后把本轮处理过的 open 记录标记 resolved
        resolved = 0
        if fixed_any:
            from datetime import datetime as _dt

            from sqlalchemy import update as _update

            r = await session.execute(
                _update(QcIssue)
                .where(QcIssue.persona_id == persona_id, QcIssue.status == "open")
                .values(status="resolved", resolved_at=_dt.utcnow())
            )
            resolved = r.rowcount or 0
            await session.commit()
            log(f"  ✓ 已标记 {resolved} 条检查记录为已处理")

        return {"fixed": fixed_any, "open_count": len(issues), "resolved_count": resolved}
