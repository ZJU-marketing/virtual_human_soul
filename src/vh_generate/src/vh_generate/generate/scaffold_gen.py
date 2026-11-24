"""骨架事件 → 情景记忆 (渐进式消化).

按年份批次读取 life_scaffold_events, 每条让 LLM 写成第一人称记忆, 落库后标记 processed.
"""

from __future__ import annotations

import logging
from typing import Callable

from langchain_core.messages import AIMessageChunk, HumanMessage, SystemMessage, ToolMessage
from pydantic import BaseModel, Field

from vh_generate.generate.agent import make_llm
from vh_generate.generate.progressive import (
    RangeFeed,
    astream_merged,
    run_incremental_summary,
    run_progressive_agent,
)

_log = logging.getLogger("vh.scaffold")


class RecordMemoryArgs(BaseModel):
    """为一条人生事件写一条第一人称情景记忆, 并展开事件档案."""

    event_id: str = Field(description="对应事件的 id (原样回传)")
    title: str = Field(description="记忆标题, 如'女儿出生那年冬天'")
    content: str = Field(description="第一人称回忆, 150-300 字, 有细节有情绪, 贴合当年年龄与时代")
    occurred_at: str = Field(description="发生日期 YYYY-MM-DD (只知道年份就用当年年中)")
    emotion: str = Field(default="平静", description="情绪, 如 喜悦/焦虑/怀念")
    summary: str = Field(default="", description="事件档案摘要 30-60 字 (第三人称)")
    detail: str = Field(default="", description="事件档案正文 150-300 字 (第三人称, 补充背景/经过/结果, 与第一人称回忆区分)")


SCAFFOLD_SYSTEM = """你是人物传记作者。下面是人物设定与若干按时间排列的人生事件，
请为每条事件写一条第一人称情景记忆，并调用 record_memory 工具逐条记录。

要求：
1. 第一人称、口语化、有生活质感，避免 AI 腔
2. 把事件里的结构化信息(性别/金额/事件名/渠道等)自然融进叙事，不要罗列字段
3. 贴合事件发生年份时人物的年龄、身份与时代背景
4. 每条 150-300 字，只写这一件事，不要串到别的事件
5. 事件 id 必须原样回传给 record_memory
6. 除第一人称回忆外, 还要给出事件档案: summary (30-60 字摘要) 与 detail (150-300 字第三人称事实展开)
7. 全部写完后输出一句"已记录"即可

【人物性格导向 (必须贯穿叙事语气与反应)】
{personality}"""


SKELETON_SYSTEM = """你是人物档案整理师。根据下面按时间排列的人生履历，归纳出这个人物的基础骨架档案。

输出纯文本 (不要 JSON、不要 markdown 代码块), 按以下顺序逐行书写:
姓名: …
性别/出生: … (如 "女 / 1981年生于河南周口")
籍贯/常住: …
家庭: … (配偶、子女、父母等, 只写履历中能推断的)
职业轨迹: … (按年代简述身份变化)
关键经历: … (3-5 条最重要的节点)
现状: … (截至最后一条履历的年龄、身份、生活状态)
性格初判: … (一句话)

要求:
1. 只依据履历合理推断; 履历没提的具体数字不要编造
2. 姓名: 履历中出现姓名就直接使用; 没出现则依据性别、出生年代与地域起一个自然的中文姓名
   (如 "王秀兰" "陈建国"), 严禁写"未提供/不详/未知"
3. 总长 250-450 字, 语言平实
4. 只输出档案本身, 不要额外解释"""


def _extract_name(text: str) -> str | None:
    """从骨架文本的「姓名: xxx」中取姓名."""
    import re

    m = re.search(r"姓名\s*[:：]\s*([\u4e00-\u9fa5·]{2,8})", text)
    if not m:
        return None
    name = m.group(1).strip()
    if name in ("未提供", "不详", "未知", "无名", "待定", "暂无"):
        return None
    return name or None


async def generate_skeleton_async(
    persona_id: str,
    on_log: Callable[..., None] | None = None,
    on_chunk: Callable[[str], None] | None = None,
) -> str:
    """读全部人生履历, 归纳人物骨架档案 (并写回 persona.core_identity / name).

    渐进式加载: 履历通过 fetch_page 工具分页读取, 读过的页自动折叠,
    上下文只保留最近几页全文, 长履历也不会撑爆 prompt。
    """
    from sqlalchemy import select

    from virtual_human.db import async_session_factory
    from virtual_human.models import Persona
    from virtual_human.scaffold import LifeScaffoldEvent

    log = on_log or (lambda m: _log.info("%s", m))
    emit = on_chunk or (lambda t: None)

    async with async_session_factory() as session:
        p = await session.get(Persona, persona_id)
        if p is None:
            raise KeyError(f"persona not found: {persona_id}")

        rows = (
            (
                await session.execute(
                    select(LifeScaffoldEvent)
                    .where(LifeScaffoldEvent.persona_id == persona_id)
                    .order_by(
                        LifeScaffoldEvent.start_year.asc().nullslast(),
                        LifeScaffoldEvent.ord.asc(),
                    )
                )
            )
            .scalars()
            .all()
        )

    if not rows:
        raise ValueError("该人物暂无人生履历事件, 无法生成骨架")

    log(f"  ⚙ 读取履历 {len(rows)} 条, 渐进式生成人物骨架…")

    def _render(r) -> str:
        import json

        payload = r.payload
        ptxt = json.dumps(payload, ensure_ascii=False) if not isinstance(payload, str) else payload
        return f"- {r.start_year or r.start_raw or '?'} [{r.event_type}] {ptxt[:160]}"

    def _digest(items: list) -> str:
        years = [str(x.start_year or x.start_raw or "?") for x in items]
        types = sorted({x.event_type for x in items})
        return f"{len(items)} 条 · {years[0]}~{years[-1]} · {'/'.join(types[:4])}"

    def _year_of(r) -> int:
        return int(r.start_year or 0)

    feed = RangeFeed(
        title="人生履历", items=rows[:400], year_of=_year_of,
        render=_render, digest=_digest, max_per_call=10,
    )

    llm = make_llm()
    skeleton = await run_incremental_summary(
        llm,
        system_prompt=SKELETON_SYSTEM,
        feed=feed,
        task_desc="归纳人物骨架档案",
        notes_hint="阶段笔记 (累积归纳前几批履历, 不超过 400 字: 姓名线索/出生籍贯/家庭/职业轨迹/关键经历)",
        final_instruction=(
            "全部履历批次已读完。请依据阶段笔记, 直接输出人物骨架档案全文 "
            "(格式: 姓名/性别出生/籍贯常住/家庭/职业轨迹/关键经历/现状/性格初判, 250-450 字)。"
        ),
        batch_size=10,
        on_log=log,
        on_chunk=emit,
    )

    name = _extract_name(skeleton)

    async with async_session_factory() as session:
        p = await session.get(Persona, persona_id)
        p.core_identity = skeleton
        cur_name = (p.name or "").strip()
        is_placeholder = (
            not cur_name or cur_name in ("待命名", "未命名", "未命名人物")
            or "未提供" in cur_name or "不详" in cur_name or len(cur_name) > 12
        )
        if name and is_placeholder:
            p.name = name[:128]
        await session.commit()
        final_name = p.name

    log(f"  ✓ 人物骨架已生成 ({len(skeleton)} 字) · 姓名 {final_name}")
    return skeleton


PERSONALITY_SYSTEM = """你是性格侧写师。根据下面这个人物的基础设定与全部人生履历，归纳出一份"性格导向提示词"，
供后续书写他的记忆时统一语气与行为逻辑。

输出要求 (纯文本, 不要 JSON):
1. 性格底色 (2-3 个关键词 + 一句解释)
2. 价值观与在意的东西 / 忌讳
3. 说话方式 (口头禅、句式习惯、爱用的比喻, 越具体越好)
4. 行为模式 (遇事的反应、对钱/对家人的态度、冲突处理方式)
5. 情绪触发点 (什么会让他激动、什么会让他沉默)
6. 时代烙印 (成长年代留下的观念)

总长 250-400 字, 用第二人称描述 ("你是一个…")，便于直接作为写作指令使用。
只输出这份提示词本身, 不要额外解释。"""


async def generate_personality_async(
    persona_id: str,
    on_log: Callable[..., None] | None = None,
    on_chunk: Callable[[str], None] | None = None,
) -> str:
    """读人物基础设定 + 全部履历事件, 归纳性格导向提示词 (并写回 persona)."""
    import json

    from sqlalchemy import select

    from virtual_human.db import async_session_factory
    from virtual_human.models import Persona
    from virtual_human.scaffold import LifeScaffoldEvent

    log = on_log or (lambda m: _log.info("%s", m))
    emit = on_chunk or (lambda t: None)

    async with async_session_factory() as session:
        p = await session.get(Persona, persona_id)
        if p is None:
            raise KeyError(f"persona not found: {persona_id}")
        core = p.core_identity or p.bio_skeleton or ""

        rows = (
            (
                await session.execute(
                    select(LifeScaffoldEvent)
                    .where(LifeScaffoldEvent.persona_id == persona_id)
                    .order_by(
                        LifeScaffoldEvent.start_year.asc().nullslast(),
                        LifeScaffoldEvent.ord.asc(),
                    )
                )
            )
            .scalars()
            .all()
        )

    if not rows:
        raise ValueError("该人物暂无人生履历事件, 无法归纳性格")

    log(f"  ⚙ 读取履历 {len(rows)} 条, 渐进式归纳性格导向…")

    def _render(r) -> str:
        payload = r.payload
        ptxt = json.dumps(payload, ensure_ascii=False) if not isinstance(payload, str) else payload
        return f"- {r.start_year or r.start_raw or '?'} [{r.event_type}] {ptxt[:120]}"

    def _digest(items: list) -> str:
        years = [str(x.start_year or x.start_raw or "?") for x in items]
        return f"{len(items)} 条 · {years[0]}~{years[-1]}"

    feed = RangeFeed(
        title="人生履历", items=rows[:120], year_of=lambda r: int(r.start_year or 0),
        render=_render, digest=_digest, max_per_call=10,
    )

    llm = make_llm()
    personality = await run_incremental_summary(
        llm,
        system_prompt=PERSONALITY_SYSTEM,
        feed=feed,
        task_desc=f"归纳性格导向提示词\n\n基础设定:\n{core[:1200]}",
        notes_hint="阶段笔记 (累积归纳前几批履历的性格线索, 不超过 400 字: 性格底色/价值观/说话方式/行为模式/情绪触发点/时代烙印)",
        final_instruction=(
            "全部履历批次已读完。请依据阶段笔记与基础设定, 直接输出性格导向提示词全文 "
            "(第二人称 '你是一个…', 250-400 字)。"
        ),
        batch_size=10,
        on_log=log,
        on_chunk=emit,
    )

    if not personality:
        raise ValueError("LLM 未输出性格导向内容")

    async with async_session_factory() as session:
        p = await session.get(Persona, persona_id)
        p.personality = personality
        await session.commit()

    log(f"  ✓ 性格导向已生成 ({len(personality)} 字) 并写回人物")
    return personality


async def process_scaffold_events_async(
    persona_id: str,
    events: list[dict],
    core_identity: str,
    personality: str | None = None,
    on_log: Callable[..., None] | None = None,
    on_chunk: Callable[[str], None] | None = None,
) -> int:
    """为一批事件生成记忆; 返回成功条数."""
    import json

    from virtual_human.db import async_session_factory
    from virtual_human.models import EpisodicMemory
    from virtual_human.scaffold import LifeScaffoldContent
    from vh_api.scaffold_service import mark_processed

    log = on_log or (lambda m: _log.info("%s", m))
    emit = on_chunk or (lambda t: None)

    if not events:
        return 0

    llm = make_llm().bind_tools([RecordMemoryArgs])

    def _render_event(e: dict) -> str:
        payload = e.get("payload")
        payload_txt = json.dumps(payload, ensure_ascii=False) if not isinstance(payload, str) else payload
        return (
            f"- event_id={e['id']} | {e.get('start_year') or e.get('start_raw') or '未知年份'} "
            f"| 类型={e.get('event_type')} | 明细={payload_txt}"
        )

    def _messages_for(batch: list[dict]) -> list:
        return [
            SystemMessage(content=SCAFFOLD_SYSTEM),
            HumanMessage(
                content=f"人物设定:\n{core_identity[:1500]}\n\n人生事件 (按时间顺序):\n"
                        + "\n".join(_render_event(e) for e in batch)
            ),
        ]

    async def _collect_ai(msgs: list) -> AIMessageChunk | None:
        """流式收一批的完整 AI 消息 (合并 tool_calls) + 超限压缩重试 + 审核拦截重试."""
        return await astream_merged(llm, msgs, emit, log, label="本批")

    def _tool_calls_of(ai_msg) -> list[dict]:
        return list(getattr(ai_msg, "tool_calls", None) or [])

    async def _with_retry(batch: list[dict], label: str) -> AIMessageChunk | None:
        """统一入口: 超限→压缩重试, 审核→重试→返回 None (调用方逐条降级)."""
        return await astream_merged(llm, _messages_for(batch), emit, log, label=label)

    log(f"  ⚙ 本批事件 {len(events)} 条, 调用 LLM 生成记忆…")

    # 先整批; 被拦截则逐条 (每条单独一次 tool-call 调用, 上下文最小)
    fallback_events: list[dict] = []   # LLM 拒绝/拦截的事件 → 本地模板兜底落库
    ai_msg = await _with_retry(events, f"整批 {len(events)} 条")
    if ai_msg is None:
        log(f"  ⤷ 整批被拦截, 降级为逐条生成 ({len(events)} 条)…")
        collected: list[AIMessageChunk] = []
        for j, ev in enumerate(events, 1):
            one = await _with_retry([ev], f"第 {j}/{len(events)} 条")
            if one is None:
                log(f"  ⚠ 单条仍被拦截 → 标记敏感(黄色) + 模板兜底 event_id={ev['id'][:8]}")
                fallback_events.append(ev)
                continue
            collected.append(one)
        ai_msg = collected[0] if collected else None
        if ai_msg is not None and len(collected) > 1:
            for c in collected[1:]:
                ai_msg = ai_msg + c   # 合并各条的消息以便统一提取 tool_calls

    if ai_msg is None:
        if not fallback_events:
            log("  ⚠ 本批全部被拦截, 未产出记忆")
            return 0
        tool_calls = []
    else:
        tool_calls = _tool_calls_of(ai_msg)

    # LLM 拒绝/拦截的事件 → 本地模板兜底: 从事件字段直接拼一条基础记忆, 保证进度不卡死
    saved = 0
    handled: set[str] = set()
    if fallback_events:
        from datetime import datetime as _dt

        async with async_session_factory() as session:
            for ev in fallback_events:
                payload = ev.get("payload")
                if isinstance(payload, str):
                    try:
                        payload = json.loads(payload)
                    except (ValueError, TypeError):
                        payload = {"明细": payload}
                fields = "，".join(
                    f"{k}{'是' if isinstance(v, (int, float)) else '为'}{v}"
                    for k, v in (payload or {}).items()
                ) or "细节未能展开"
                year = ev.get("start_year") or 2000
                etype = ev.get("event_type") or "人生片段"
                content = f"那一年发生了{etype}。我记得当时的情况：{fields}。这件事在我的人生里留下了印记，具体的感受一时说不清，但时间是确定的。"
                occurred_dt = _dt(year=year, month=7, day=1)
                mem = EpisodicMemory(
                    persona_id=persona_id,
                    title=f"{year}年·{etype}",
                    content=content,
                    summary=content[:80],
                    occurred_at=occurred_dt,
                    emotion="平静",
                    importance=5,
                    status="published",
                )
                session.add(mem)
                await session.flush()
                session.add(LifeScaffoldContent(
                    event_id=ev["id"],
                    persona_id=persona_id,
                    seq=0,
                    summary=f"{year}年{etype} (敏感事件·模板记录)",
                    content=content,
                ))
                await session.commit()
                await mark_processed(ev["id"], mem.id)
                from vh_api.scaffold_service import mark_sensitive as _mark_sensitive

                await _mark_sensitive(ev["id"])
                handled.add(ev["id"])
                saved += 1
                log(f"  → [兜底] 记忆《{mem.title}》 {occurred_dt:%Y-%m} 🟡敏感 ✓")

    # 事件 id → 事件信息 (校验 + 取年份)
    by_id = {e["id"]: e for e in events}

    async with async_session_factory() as session:
        for tc in tool_calls:
            if tc.get("name") != "RecordMemoryArgs":
                continue
            args = tc.get("args") or {}
            eid = str(args.get("event_id") or "").strip()
            ev = by_id.get(eid)
            if ev is None:
                log(f"  ⚠ 未知 event_id={eid}, 跳过")
                continue
            content = str(args.get("content") or "").strip()
            if len(content) < 20:
                log(f"  ⚠ 记忆内容过短, 跳过 event_id={eid[:8]}")
                continue

            from datetime import datetime

            occurred = args.get("occurred_at") or ""
            try:
                occurred_dt = datetime.strptime(str(occurred)[:10], "%Y-%m-%d")
            except ValueError:
                year = ev.get("start_year")
                occurred_dt = datetime(year=year or 2000, month=7, day=1)

            mem = EpisodicMemory(
                persona_id=persona_id,
                title=str(args.get("title") or ev.get("event_type") or "人生片段")[:255],
                content=content,
                summary=content[:80],
                occurred_at=occurred_dt,
                emotion=(str(args.get("emotion"))[:32] if args.get("emotion") else None),
                importance=6,
                status="published",
            )
            session.add(mem)
            await session.flush()

            # 履历事件展开: 摘要 + 第三人称正文 (供质检页人物履历展示)
            detail = str(args.get("detail") or content).strip()
            summary = str(args.get("summary") or content[:60]).strip()
            session.add(LifeScaffoldContent(
                event_id=eid,
                persona_id=persona_id,
                seq=0,
                summary=(summary[:500] or None),
                content=detail,
            ))

            await session.commit()
            await mark_processed(eid, mem.id)
            handled.add(eid)
            saved += 1
            log(f"  → 记忆《{mem.title}》 {occurred_dt:%Y-%m} ✓")

    # LLM 未覆盖到的事件 (没调工具/漏事件) → 模板兜底, 保证全部落库并标记 processed
    uncovered = [e for e in events if e["id"] not in handled]
    if uncovered:
        log(f"  ⚠ {len(uncovered)} 条事件 LLM 未覆盖, 使用模板兜底…")
        from datetime import datetime as _dt2

        async with async_session_factory() as session:
            for ev in uncovered:
                payload = ev.get("payload")
                if isinstance(payload, str):
                    try:
                        payload = json.loads(payload)
                    except (ValueError, TypeError):
                        payload = {"明细": payload}
                fields = "，".join(
                    f"{k}{'是' if isinstance(v, (int, float)) else '为'}{v}"
                    for k, v in (payload or {}).items()
                ) or "细节未能展开"
                year = ev.get("start_year") or 2000
                etype = ev.get("event_type") or "人生片段"
                content = (
                    f"那一年发生了{etype}。我记得当时的情况：{fields}。"
                    "这件事在我的人生里留下了印记，具体的感受一时说不清，但时间是确定的。"
                )
                occurred_dt = _dt2(year=year, month=7, day=1)
                mem = EpisodicMemory(
                    persona_id=persona_id,
                    title=f"{year}年·{etype}",
                    content=content,
                    summary=content[:80],
                    occurred_at=occurred_dt,
                    emotion="平静",
                    importance=5,
                    status="published",
                )
                session.add(mem)
                await session.flush()
                session.add(LifeScaffoldContent(
                    event_id=ev["id"],
                    persona_id=persona_id,
                    seq=0,
                    summary=f"{year}年{etype}",
                    content=content,
                ))
                await session.commit()
                await mark_processed(ev["id"], mem.id)
                saved += 1
                log(f"  → [兜底] 记忆《{mem.title}》 {occurred_dt:%Y-%m} ✓")

    return saved


# ---------------------------------------------------------------------
# 履历事件展开: agent 为每条 life_scaffold_event 生成摘要 + 正文
# ---------------------------------------------------------------------
class RecordEventContentArgs(BaseModel):
    """为一条人生履历事件生成摘要与展开正文."""

    event_id: str = Field(description="事件 id, 原样回传")
    summary: str = Field(description="事件摘要 30-60 字 (第三人称, 概括这件事)")
    detail: str = Field(description="事件展开正文 120-300 字 (第三人称, 补充背景/经过/结果)")


EXPAND_SYSTEM = """你是人物传记资料整理员。下面是一个人物的人生履历事件 (按时间排列)。
请为每条事件生成事件档案, 并调用 record_event_content 逐条记录:

1. summary: 30-60 字摘要, 第三人称, 概括发生了什么事
2. detail: 120-300 字展开正文, 第三人称, 补充背景、经过、结果与时代语境

要求:
- 只使用履历里已有的信息, 不编造具体数字、机构名或人名
- 不要原样贴 payload, 要写成通顺的叙述
- 每条事件至少一条记录, event_id 必须原样回传
- 全部完成后输出"已展开\""""


async def expand_scaffold_events_async(
    persona_id: str,
    batch_size: int = 8,
    force: bool = False,
    event_ids: list[str] | None = None,
    on_log: Callable[..., None] | None = None,
    on_chunk: Callable[[str], None] | None = None,
) -> int:
    """为履历事件生成摘要与展开正文 (1 event : N contents).

    force=True: 删除已有展开后全部重新生成; 否则只补缺失的事件。
    event_ids: 只处理指定事件 (单条重生成时用)。
    """
    import json

    from sqlalchemy import delete, select

    from virtual_human.db import async_session_factory
    from virtual_human.models import Persona
    from virtual_human.scaffold import LifeScaffoldContent, LifeScaffoldEvent

    log = on_log or (lambda m: _log.info("%s", m))
    emit = on_chunk or (lambda t: None)

    async with async_session_factory() as session:
        events = (
            (
                await session.execute(
                    select(LifeScaffoldEvent)
                    .where(LifeScaffoldEvent.persona_id == persona_id)
                    .order_by(
                        LifeScaffoldEvent.start_year.asc().nullslast(),
                        LifeScaffoldEvent.ord.asc(),
                    )
                )
            )
            .scalars()
            .all()
        )
        if event_ids:
            wanted = set(event_ids)
            events = [e for e in events if e.id in wanted]

        if not events:
            log("  ⚠ 没有可展开的履历事件, 跳过")
            return 0

        if force:
            q = delete(LifeScaffoldContent).where(LifeScaffoldContent.persona_id == persona_id)
            if event_ids:
                q = q.where(LifeScaffoldContent.event_id.in_(event_ids))
            await session.execute(q)
            await session.commit()

        existing = set(
            (
                await session.execute(
                    select(LifeScaffoldContent.event_id).where(
                        LifeScaffoldContent.persona_id == persona_id
                    )
                )
            ).scalars().all()
        )

        persona = await session.get(Persona, persona_id)
        core = (persona.core_identity if persona else "") or ""

    targets = [e for e in events if force or e.id not in existing]
    if not targets:
        log("  ✓ 所有履历事件都已有展开内容, 跳过")
        return 0

    log(f"  ⚙ 履历展开: 待生成 {len(targets)} 条事件…")

    llm = make_llm().bind_tools([RecordEventContentArgs])
    saved = 0

    for start in range(0, len(targets), batch_size):
        batch = targets[start:start + batch_size]
        lines = []
        for e in batch:
            payload = e.payload
            ptxt = json.dumps(payload, ensure_ascii=False) if not isinstance(payload, str) else payload
            lines.append(
                f"- event_id={e.id} | {e.start_year or e.start_raw or '?'} "
                f"| 类型={e.event_type} | 明细={ptxt[:220]}"
            )

        messages: list = [
            SystemMessage(content=EXPAND_SYSTEM),
            HumanMessage(content=(
                f"人物设定:\n{core[:1000]}\n\n"
                "人生履历事件:\n" + "\n".join(lines)
            )),
        ]

        log(f"  批次 {start // batch_size + 1}: {len(batch)} 条事件, 调用 LLM 展开…")

        ai_msg = await astream_merged(llm, messages, emit, log, label=f"展开批次 {start // batch_size + 1}")

        calls = ai_msg.tool_calls if ai_msg else []
        if not calls:
            log("  ⚠ LLM 未调用 record_event_content (或被拦截), 本批跳过")
            continue

        by_id = {e.id: e for e in batch}
        seq_by_event: dict[str, int] = {}
        async with async_session_factory() as session:
            for tc in calls:
                if tc.get("name") != "RecordEventContentArgs":
                    continue
                args = tc.get("args") or {}
                eid = str(args.get("event_id") or "").strip()
                ev = by_id.get(eid)
                if ev is None:
                    log(f"  ⚠ 未知 event_id={eid[:8]}, 跳过")
                    continue
                summary = str(args.get("summary") or "").strip()
                detail = str(args.get("detail") or "").strip()
                if len(detail) < 40:
                    log(f"  ⚠ 展开正文过短, 跳过 event_id={eid[:8]}")
                    continue
                seq = seq_by_event.get(eid, 0)
                seq_by_event[eid] = seq + 1
                session.add(LifeScaffoldContent(
                    event_id=eid,
                    persona_id=persona_id,
                    seq=seq,
                    summary=(summary[:500] or None),
                    content=detail,
                ))
                saved += 1
                log(f"  → 履历展开《{ev.event_type} {ev.start_year or ''}》 {len(detail)} 字 ✓")
            await session.commit()

    # LLM 偶尔漏掉部分事件: 有进展时重试剩余的
    async with async_session_factory() as session:
        existing = set(
            (
                await session.execute(
                    select(LifeScaffoldContent.event_id).where(
                        LifeScaffoldContent.persona_id == persona_id
                    )
                )
            ).scalars().all()
        )
    remaining = [e for e in events if e.id not in existing]
    if remaining and saved > 0:
        log(f"  ⚠ 仍有 {len(remaining)} 条事件未展开, 继续重试…")
        saved += await expand_scaffold_events_async(
            persona_id,
            batch_size=batch_size,
            force=False,
            event_ids=[e.id for e in remaining],
            on_log=on_log,
            on_chunk=on_chunk,
        )

    log(f"  ✓ 履历展开完成: 新增 {saved} 条内容")
    return saved


# ---------------------------------------------------------------------
# 最后一步: 从履历+记忆归纳知识节点, 抽取财务大事记
# ---------------------------------------------------------------------
class RecordKnowledgeArgs(BaseModel):
    """归纳一个知识节点 (摘要 + 一段正文)."""

    title: str = Field(description="节点标题, 如 '职业与工作' '家庭与婚姻' '金钱观'")
    node_type: str = Field(default="semantic", description="identity/chapter/semantic/procedural 之一")
    summary: str = Field(description="节点摘要 40-100 字, 供人物简介展示")
    content: str = Field(description="节点正文 200-500 字, 事实清楚、贴合年代")
    importance: int = Field(default=6, description="重要度 1-10")
    disclosure_level: int = Field(default=6, description="披露度 0-9, 0 表示隐藏")


class RecordFinanceArgs(BaseModel):
    """记录一笔人生大额财务事件."""

    event_ref: str = Field(default="", description="履历财务事件编号, 如 F1/F2 (优先使用)")
    title: str = Field(description="事件标题, 如 '彩礼支出' '购置婚房' '读研学费'")
    event_type: str = Field(description="income/expense_buy/expense_live/loan_taken/loan_repay")
    amount: float = Field(description="金额(元), 必须为正数")
    occurred_year: int = Field(description="发生年份, 如 2022")
    category: str = Field(default="", description="real_estate/vehicle/medical/wedding/education/other")
    memory_title: str = Field(default="", description="最接近的情景记忆标题, 可为空")
    note: str = Field(default="", description="补充说明")


FINALIZE_SYSTEM = """你是人物资料整理员。人物设定在消息开头; 下面是人生履历与情景记忆的一个批次。
请只为这一批做两件事, 并调用工具逐条记录:

一、知识节点 (record_knowledge):
   - 从本批履历/记忆中归纳 0-3 个知识节点 (无新信息就一个都不记)
   - 主题参考: 家庭与亲密关系 / 职业与工作 / 收入与金钱观 / 性格与处事方式 /
     健康与生活习惯 / 居住与资产 / 人际与社会关系 / 时代与地域烙印
   - 标题简短 (6 字以内), 摘要 40-100 字, 正文 200-500 字
   - 只写本批确有依据的内容, 不要编造具体数字
   - 与其他批次主题重复时写得更具体, 不要泛泛而谈

二、财务大事 (record_finance):
   只能从本批「可入账财务事件清单」中逐条记录, 不得自己新增事件。
   - event_ref 必须填清单里的编号 (F1/F2…)
   - 金额、年份必须与清单完全一致, 不得改写
   - title 要贴合该事件 (婚宴/彩礼 → "彩礼支出"; 买房 → "购置房产")
   - event_type 只能是 income/expense_buy/expense_live/loan_taken/loan_repay
   - memory_title 填最接近的情景记忆标题, 便于挂账
   - 清单为空时不要调用 record_finance

全部完成后输出"已整理"。"""


def _norm(s: str | None) -> str:
    return (s or "").strip().lower()


def _money_candidates(events) -> list[dict]:
    """从履历事件里挑出带金额的, 生成财务大事候选 (供 LLM 引用编号)."""
    import json

    money_keys = (
        "金额", "彩礼", "花费", "费用", "价格", "收入", "工资", "贷款", "借款",
        "还款", "学费", "支出", "首付", "总价", "成本", "赔偿", "礼金",
    )
    # 这些字段即使数值大也不是钱 (身高/体重/面积等)
    non_money_keys = (
        "身高", "体重", "年龄", "年份", "面积", "数量", "次数", "胎次",
        "人口", "温度", "距离", "时长",
    )
    out: list[dict] = []
    for e in events:
        payload = e.payload
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except (ValueError, TypeError):
                payload = None
        if not isinstance(payload, dict):
            continue
        numbers = [
            float(v) for v in payload.values()
            if isinstance(v, (int, float)) and not isinstance(v, bool) and float(v) > 0
        ]
        matched = [
            (str(k), float(v))
            for k, v in payload.items()
            if isinstance(v, (int, float)) and not isinstance(v, bool)
            and not any(t in str(k) for t in non_money_keys)
            and any(t in str(k) for t in money_keys) and float(v) > 0
        ]
        if matched:
            hits = [f"{k}={v:g}" for k, v in matched]
            amounts = [v for _, v in matched]
        else:
            # 字段名不认识时, 只把 >=1000 的数值按金额处理 (避开身高/面积等)
            amounts = [v for v in numbers if v >= 1000]
            if not amounts:
                continue
            hits = [f"金额={v:g}" for v in amounts]

        out.append({
            "ref": f"F{len(out) + 1}",
            "year": e.start_year,
            "type": e.event_type,
            "payload": payload,
            "hits": hits,
            "amount": max(amounts),
        })
    return out


async def finalize_persona_async(
    persona_id: str,
    on_log: Callable[..., None] | None = None,
    on_chunk: Callable[[str], None] | None = None,
) -> dict:
    """生成知识节点 (kb_nodes + kb_node_contents) 与财务大事记 (finance_events).

    幂等策略: 先删掉上次由本流程生成的数据 (tags.source=scaffold / note=scaffold),
    再重新生成, 避免重复堆积, 同时不影响其他途径录入的节点。
    """
    import json
    import re
    from datetime import datetime

    from sqlalchemy import delete, select, text

    from virtual_human.db import async_session_factory
    from virtual_human.models import EpisodicMemory, FinanceEvent, KbNode, KbNodeContent, Persona
    from virtual_human.scaffold import LifeScaffoldEvent

    log = on_log or (lambda m: _log.info("%s", m))
    emit = on_chunk or (lambda t: None)

    async with async_session_factory() as session:
        p = await session.get(Persona, persona_id)
        if p is None:
            raise KeyError(f"persona not found: {persona_id}")
        core = p.core_identity or p.bio_skeleton or ""
        personality = p.personality or ""

        events = (
            (
                await session.execute(
                    select(LifeScaffoldEvent)
                    .where(LifeScaffoldEvent.persona_id == persona_id)
                    .order_by(LifeScaffoldEvent.start_year.asc().nullslast(), LifeScaffoldEvent.ord.asc())
                )
            )
            .scalars()
            .all()
        )
        memories = (
            (
                await session.execute(
                    select(EpisodicMemory)
                    .where(EpisodicMemory.persona_id == persona_id)
                    .order_by(EpisodicMemory.occurred_at.asc().nullslast())
                )
            )
            .scalars()
            .all()
        )

    if not events and not memories:
        log("  ⚠ 没有履历/记忆, 跳过知识节点与财务大事记")
        return {"knowledge_nodes": 0, "finance_events": 0}

    # 敏感事件 (曾被审核拦截) 不再送审, 只留概要行, 前端黄色显示
    n_sensitive = sum(1 for e in events if getattr(e, "sensitive", False))
    if n_sensitive:
        log(f"  ⚙ {n_sensitive} 条敏感事件已过滤 (黄色标记, 不参与 LLM 归纳)")
    ev_lines = []
    for e in events[:200]:
        if getattr(e, "sensitive", False):
            ev_lines.append(f"- {e.start_year or e.start_raw or '?'} [{e.event_type}] (敏感事件, 略)")
            continue
        payload = e.payload
        ptxt = json.dumps(payload, ensure_ascii=False) if not isinstance(payload, str) else payload
        ev_lines.append(f"- {e.start_year or e.start_raw or '?'} [{e.event_type}] {ptxt[:160]}")

    mem_lines = []
    for m in memories[:200]:
        when = f"{m.occurred_at:%Y-%m}" if m.occurred_at else "?"
        mem_lines.append(f"- {when} 《{m.title}》: {(m.summary or m.content or '')[:140]}")

    # 带金额的履历事件 → 财务大事候选 (编号供 LLM 引用, 防止张冠李戴)
    fin_candidates = _money_candidates(events)

    fin_lines = [
        f"- [{c['ref']}] {c['year'] or '?'} [{c['type']}] " + ", ".join(c["hits"])
        for c in fin_candidates
    ] or ["(无带金额的履历事件, 不要记录任何财务大事)"]

    # ---- 敏感事件不送审 ----
    usable = [e for e in events if not getattr(e, "sensitive", False)]
    n_sensitive = len(events) - len(usable)
    if n_sensitive:
        log(f"  ⚙ {n_sensitive} 条敏感事件已过滤 (黄色标记, 不参与归纳)")

    fin_candidates = _money_candidates(usable)

    # 幂等: 先清掉上次本流程生成的数据
    async with async_session_factory() as session:
        await session.execute(
            text("DELETE FROM kb_nodes WHERE persona_id = :pid AND tags->>'source' = 'scaffold'"),
            {"pid": persona_id},
        )
        await session.execute(
            delete(FinanceEvent).where(
                FinanceEvent.persona_id == persona_id,
                FinanceEvent.note == "scaffold",
            )
        )
        await session.commit()

    # ---- 组批: 履历 10 条/批, 带上同年段记忆与财务候选; 剩余记忆单独成批 ----
    batch_size = 10
    batches: list[tuple[list, list, list]] = []
    used_mem: set[str] = set()
    for i in range(0, len(usable), batch_size):
        ev_b = usable[i:i + batch_size]
        years = [e.start_year for e in ev_b if e.start_year]
        lo, hi = (min(years), max(years)) if years else (None, None)
        mem_b = []
        if lo is not None:
            for m in memories:
                if m.id in used_mem or m.occurred_at is None:
                    continue
                if lo <= m.occurred_at.year <= hi:
                    mem_b.append(m)
                    used_mem.add(m.id)
        fin_b = [
            c for c in fin_candidates
            if c["year"] and lo is not None and lo <= c["year"] <= hi
        ]
        batches.append((ev_b, mem_b, fin_b))

    leftover_mem = [m for m in memories if m.id not in used_mem]
    for i in range(0, len(leftover_mem), batch_size):
        batches.append(([], leftover_mem[i:i + batch_size], []))

    covered_refs = {c["ref"] for _, _, fb in batches for c in fb}
    leftover_fin = [c for c in fin_candidates if c["ref"] not in covered_refs]

    total_batches = max(len(batches), 1)
    log(
        f"  ⚙ 最后一步: {len(usable)} 条履历 + {len(memories)} 条记忆 → "
        f"分 {total_batches} 批归纳知识节点/财务大事…"
    )

    llm = make_llm().bind_tools([RecordKnowledgeArgs, RecordFinanceArgs])
    valid_node_types = {"identity", "chapter", "semantic", "procedural"}
    valid_fin_types = {"income", "expense_buy", "expense_live", "loan_taken", "loan_repay"}

    def _pick_memory(title: str, year: int | None):
        t = _norm(title)
        if t:
            for m in memories:
                mt = _norm(m.title)
                if mt and (t in mt or mt in t):
                    return m
        if not memories:
            return None
        y = year or 0
        return min(
            memories,
            key=lambda m: abs((m.occurred_at.year if m.occurred_at else 9999) - y),
        )

    def _infer_fin(c: dict) -> tuple[str, str, str]:
        """按事件 payload 推断标题/类型/分类 (不依赖 LLM)."""
        blob = " ".join([str(k) for k in c["payload"].keys()] + [str(v) for v in c["payload"].values()])
        rules = [
            (("还款", "偿还"), "还款", "loan_repay", "loan"),
            (("贷款", "借款", "房贷", "负债"), "借贷", "loan_taken", "loan"),
            (("彩礼", "礼金", "婚礼", "婚宴", "嫁妆"), "婚礼/彩礼支出", "expense_live", "wedding"),
            (("买房", "购房", "首付", "房价", "房产", "房子"), "购置房产", "expense_buy", "real_estate"),
            (("买车", "购车", "车价", "汽车", "车辆"), "购置车辆", "expense_buy", "vehicle"),
            (("学费", "培训", "课程", "教育", "读研", "上学"), "教育支出", "expense_live", "education"),
            (("住院", "手术", "医疗", "看病", "大病"), "医疗支出", "expense_live", "medical"),
            (("收入", "工资", "奖金", "薪"), "收入", "income", "income"),
        ]
        for keys, f_title, f_type, f_cat in rules:
            if any(k in blob for k in keys):
                return f_title, f_type, f_cat
        return f"{c['type']}相关支出", "expense_live", "other"

    n_nodes = 0
    n_fin = 0
    created_nodes: list[KbNode] = []
    seen_titles: set[str] = set()
    seen_fin: set[tuple] = set()
    llm_fin: dict[str, dict] = {}
    generic_fin: list[dict] = []

    def _create_fin(
        session, title: str, etype: str, amount: float, year: int,
        category: str, memory_title: str,
    ) -> None:
        nonlocal n_fin
        if not title or amount <= 0 or etype not in valid_fin_types:
            log(f"  ⚠ 财务事件参数无效, 跳过: {title or '(无标题)'}")
            return
        key = (title, round(amount, 2), year)
        if key in seen_fin:
            return
        seen_fin.add(key)

        mem = _pick_memory(memory_title, year)
        if mem is None:
            log(f"  ⚠ 无可用记忆挂账, 跳过财务事件: {title}")
            return

        occurred = mem.occurred_at
        if occurred is None and year:
            occurred = datetime(year=year, month=7, day=1)

        session.add(FinanceEvent(
            persona_id=persona_id,
            event_type=etype,
            title=title,
            item_category=(category.strip()[:32] or None),
            amount=amount,
            currency="CNY",
            occurred_at=occurred,
            memory_id=mem.id,
            note="scaffold",
        ))
        n_fin += 1
        log(f"  → 财务大事《{title}》 {amount:,.0f} 元 · 挂记忆《{mem.title}》 ✓")

    def _create_fin_from_cand(session, cand: dict, args: dict) -> None:
        f_title, f_type, f_cat = _infer_fin(cand)
        title = str(args.get("title") or "").strip()[:255] or f_title
        etype = str(args.get("event_type") or "").strip()
        if etype not in valid_fin_types:
            etype = f_type
        category = str(args.get("category") or "").strip() or f_cat
        year = int(cand["year"] or args.get("occurred_year") or 0)
        _create_fin(
            session, title, etype, float(cand["amount"]), year, category,
            str(args.get("memory_title") or ""),
        )

    for no, (ev_b, mem_b, fin_b) in enumerate(batches, 1):
        ev_lines = []
        for e in ev_b:
            payload = e.payload
            ptxt = json.dumps(payload, ensure_ascii=False) if not isinstance(payload, str) else payload
            ev_lines.append(f"- {e.start_year or e.start_raw or '?'} [{e.event_type}] {ptxt[:160]}")

        mem_lines = [
            f"- {(m.occurred_at.strftime('%Y-%m') if m.occurred_at else '?')} 《{m.title}》: "
            f"{(m.summary or m.content or '')[:140]}"
            for m in mem_b
        ]
        fin_lines = [
            f"- [{c['ref']}] {c['year'] or '?'} [{c['type']}] " + ", ".join(c["hits"])
            for c in fin_b
        ] or ["(本批没有可入账的财务事件, 不要记录 record_finance)"]

        human = (
            f"人物设定:\n{core[:900]}\n\n"
            f"性格导向:\n{personality[:500]}\n\n"
            f"【本批 第 {no}/{total_batches} 批】\n"
            "人生履历:\n" + ("\n".join(ev_lines) or "(本批无履历, 仅有情景记忆)") + "\n\n"
            "可入账财务事件清单 (只能引用这些编号):\n" + "\n".join(fin_lines) + "\n\n"
            "情景记忆:\n" + ("\n".join(mem_lines) or "(无)")
        )
        messages: list = [
            SystemMessage(content=FINALIZE_SYSTEM),
            HumanMessage(content=human),
        ]

        ai_msg = await astream_merged(
            llm, messages, emit, log, label=f"finalize 第 {no}/{total_batches} 批",
        )
        if ai_msg is None:
            log(f"  ⚠ 第 {no} 批被拦截, 跳过 (其它批继续)")
        calls = (ai_msg.tool_calls if ai_msg else None) or []

        async with async_session_factory() as session:
            for tc in calls:
                name = tc.get("name")
                args = tc.get("args") or {}
                if name == "RecordFinanceArgs":
                    ref = str(args.get("event_ref") or "").strip().upper()
                    if ref:
                        llm_fin[ref] = args
                    elif not fin_candidates:
                        generic_fin.append(args)
                    else:
                        log(f"  ⚠ 财务事件未引用履历编号, 忽略: {args.get('title') or '-'}")
                    continue
                if name != "RecordKnowledgeArgs":
                    continue
                title = str(args.get("title") or "").strip()[:255]
                summary = str(args.get("summary") or "").strip()
                content = str(args.get("content") or "").strip()
                if not title or len(content) < 40 or title in seen_titles:
                    continue
                if n_nodes >= 12:
                    continue   # 节点总量封顶, 避免分批重复堆叠
                seen_titles.add(title)

                node_type = str(args.get("node_type") or "semantic").strip()
                if node_type not in valid_node_types:
                    node_type = "semantic"
                try:
                    importance = max(1, min(10, int(args.get("importance") or 6)))
                except (TypeError, ValueError):
                    importance = 6
                try:
                    disclosure = max(0, min(9, int(args.get("disclosure_level") or 6)))
                except (TypeError, ValueError):
                    disclosure = 6

                idx = n_nodes + 1
                slug = re.sub(r"[^\w\u4e00-\u9fa5]+", "-", title).strip("-")[:80] or f"kb-{idx}"
                node = KbNode(
                    persona_id=persona_id,
                    parent_id=None,
                    node_type=node_type,
                    slug=f"scaffold-kb-{idx}-{slug}"[:128],
                    title=title,
                    summary=summary or content[:120],
                    tags={"source": "scaffold"},
                    load_level=2,
                    importance=importance,
                    disclosure_level=disclosure,
                    token_estimate=max(1, len(content) // 2),
                    content_count=1,
                    status="published",
                )
                session.add(node)
                await session.flush()
                created_nodes.append(node)
                session.add(KbNodeContent(
                    node_id=node.id,
                    seq=0,
                    label=title,
                    content=content,
                    content_format="markdown",
                    token_estimate=max(1, len(content) // 2),
                    importance=importance,
                    status="published",
                ))
                n_nodes += 1
                log(f"  → [第{no}批] 知识节点《{title}》 {len(content)} 字 ✓")

            # 本批财务候选: 每个都落一笔 (LLM 描述优先, 否则规则推断)
            for cand in fin_b:
                _create_fin_from_cand(session, cand, llm_fin.get(cand["ref"], {}))
            await session.commit()

    # 批次范围外的财务候选 (无年份等) 仍确定性落库
    if leftover_fin:
        async with async_session_factory() as session:
            for cand in leftover_fin:
                _create_fin_from_cand(session, cand, llm_fin.get(cand["ref"], {}))
            await session.commit()

    # 没有可提取金额的履历时, 才接受 LLM 自己给出的财务事件
    for args in generic_fin:
        try:
            amount = float(args.get("amount") or 0)
            year = int(args.get("occurred_year") or 0)
        except (TypeError, ValueError):
            continue
        async with async_session_factory() as session:
            _create_fin(
                session,
                str(args.get("title") or "").strip()[:255],
                str(args.get("event_type") or "").strip(),
                amount, year,
                str(args.get("category") or ""),
                str(args.get("memory_title") or ""),
            )
            await session.commit()

    # 人生骨架 = 知识节点标题索引 (与身份自述不同, 用 <slug> 引导 agent 探索)
    async with async_session_factory() as session:
        p = await session.get(Persona, persona_id)
        if p is not None:
            p.bio_skeleton = "\n".join(
                f'- <slug id="{persona_id}/{n.slug}">{n.title}</slug>'
                for n in sorted(created_nodes, key=lambda x: (-x.importance, x.title))
            ) or None
            await session.commit()

    log(f"  ✓ 知识节点 {n_nodes} 个 · 财务大事 {n_fin} 笔 已入库")
    return {"knowledge_nodes": n_nodes, "finance_events": n_fin}
