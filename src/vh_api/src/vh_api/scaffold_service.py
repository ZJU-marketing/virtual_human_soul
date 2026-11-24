"""scaffold —— 人生骨架事件的存储与渐进式消化.

流程:
    1. save_events(persona_id, raw_json)   保存事件 (容错解析 payload/causes)
    2. 按 start_year 升序取未处理事件 (批处理)
    3. process_batch: 每条事件 → LLM 写一条第一人称情景记忆 → 标记 processed
"""

from __future__ import annotations

import json
import re

from sqlalchemy import func, select

from virtual_human.db import async_session_factory
from virtual_human.models import EpisodicMemory
from virtual_human.scaffold import LifeScaffoldContent, LifeScaffoldEvent


def _parse_year(v) -> int | None:
    if v is None:
        return None
    m = re.search(r"(\d{4})", str(v))
    return int(m.group(1)) if m else None


def _maybe_json(v):
    """payload/causes 常见是「JSON 字符串」, 尽量解码成对象."""
    if v is None:
        return None
    if isinstance(v, (dict, list)):
        return v
    if isinstance(v, str):
        s = v.strip()
        if not s:
            return None
        try:
            return json.loads(s)
        except json.JSONDecodeError:
            return s
    return v


async def save_events(persona_id: str, events: list[dict]) -> int:
    """覆盖式保存事件 (先清空该人物的旧事件), 返回条数."""
    from sqlalchemy import delete

    async with async_session_factory() as session:
        await session.execute(
            delete(LifeScaffoldEvent).where(LifeScaffoldEvent.persona_id == persona_id)
        )
        n = 0
        for i, ev in enumerate(events):
            if not isinstance(ev, dict):
                continue
            session.add(_new_event(persona_id, ev, i))
            n += 1
        await session.commit()
        return n


def _new_event(persona_id: str, ev: dict, ord_: int) -> LifeScaffoldEvent:
    """把外部 JSON 事件转成 ORM 行 (兼容 type/event_type, start/start_year)."""
    start_raw = ev.get("start", ev.get("start_year"))
    end_raw = ev.get("end", ev.get("end_year"))
    return LifeScaffoldEvent(
        persona_id=persona_id,
        ord=ord_,
        event_type=str(ev.get("type") or ev.get("event_type") or "事件")[:32],
        start_raw=str(start_raw)[:32] if start_raw is not None else None,
        start_year=_parse_year(start_raw),
        end_year=_parse_year(end_raw),
        payload=_maybe_json(ev.get("payload")),
        causes=_maybe_json(ev.get("causes")),
        world_event_ref=(str(ev["world_event_ref"])[:64]
                         if ev.get("world_event_ref") else None),
        source=(str(ev["source"])[:32] if ev.get("source") else None),
        confidence=float(ev["confidence"]) if ev.get("confidence") is not None else None,
        visibility=(str(ev["visibility"])[:32] if ev.get("visibility") else None),
        correction_of=(str(ev["correction_of"])[:36] if ev.get("correction_of") else None),
        schema_version=(str(ev["schema_version"])[:16] if ev.get("schema_version") else None),
        processed=False,
    )


def _merge_payload(old, new, mode: str):
    """merge 策略的 payload 处理: replace 整体替换 / merge 浅合并 (新值覆盖)."""
    if mode == "replace":
        return new
    if isinstance(old, dict) and isinstance(new, dict):
        return {**old, **new}
    return new if new is not None else old


async def apply_events(
    persona_id: str,
    events: list[dict],
    strategy: str = "insert",
    payload_mode: str = "merge",
) -> dict:
    """按时间策略把外部事件写入 life_scaffold_events.

    strategy:
        insert  仅插入 (允许重复)
        replace 删除入参年份范围内的旧事件, 再插入
        merge   同年份+同类型的事件合并 payload; 找不到则插入
    """
    from sqlalchemy import delete, select

    evs = [e for e in events if isinstance(e, dict)]
    if not evs:
        return {"inserted": 0, "updated": 0, "deleted": 0, "event_ids": [], "years": [None, None]}

    years = [y for y in (_parse_year(e.get("start", e.get("start_year"))) for e in evs) if y is not None]
    min_y = min(years) if years else None
    max_y = max(years) if years else None

    inserted = updated = deleted = 0
    event_ids: list[str] = []

    async with async_session_factory() as session:
        existing = (
            (
                await session.execute(
                    select(LifeScaffoldEvent)
                    .where(LifeScaffoldEvent.persona_id == persona_id)
                    .order_by(LifeScaffoldEvent.ord)
                )
            )
            .scalars()
            .all()
        )
        max_ord = max((e.ord for e in existing), default=-1)
        next_ord = max_ord + 1

        async def drop_event(e: LifeScaffoldEvent) -> None:
            nonlocal deleted
            if e.memory_id:
                m = await session.get(EpisodicMemory, e.memory_id)
                if m is not None:
                    await session.delete(m)
            await session.execute(
                delete(LifeScaffoldContent).where(LifeScaffoldContent.event_id == e.id)
            )
            await session.delete(e)
            deleted += 1

        if strategy == "replace":
            if min_y is not None and max_y is not None:
                for e in existing:
                    if e.start_year is not None and min_y <= e.start_year <= max_y:
                        await drop_event(e)
            for ev in evs:
                e = _new_event(persona_id, ev, next_ord)
                next_ord += 1
                session.add(e)
                await session.flush()
                event_ids.append(e.id)
                inserted += 1

        elif strategy == "merge":
            by_key = {
                (e.start_year, e.event_type): e
                for e in existing
                if e.start_year is not None
            }
            for ev in evs:
                year = _parse_year(ev.get("start", ev.get("start_year")))
                etype = str(ev.get("type") or ev.get("event_type") or "事件")[:32]
                old = by_key.get((year, etype))
                if old is None:
                    e = _new_event(persona_id, ev, next_ord)
                    next_ord += 1
                    session.add(e)
                    await session.flush()
                    event_ids.append(e.id)
                    inserted += 1
                else:
                    old.start_raw = (str(ev.get("start"))[:32]
                                     if ev.get("start") is not None else old.start_raw)
                    old.end_year = (_parse_year(ev.get("end", ev.get("end_year")))
                                    if (ev.get("end") is not None or ev.get("end_year") is not None)
                                    else old.end_year)
                    old.payload = _merge_payload(
                        old.payload, _maybe_json(ev.get("payload")), payload_mode,
                    )
                    if ev.get("visibility"):
                        old.visibility = str(ev["visibility"])[:32]
                    old.processed = False
                    old_memory = old.memory_id
                    old.memory_id = None
                    if old_memory:
                        m = await session.get(EpisodicMemory, old_memory)
                        if m is not None:
                            await session.delete(m)
                    await session.execute(
                        delete(LifeScaffoldContent).where(LifeScaffoldContent.event_id == old.id)
                    )
                    event_ids.append(old.id)
                    updated += 1

        else:  # insert
            for ev in evs:
                e = _new_event(persona_id, ev, next_ord)
                next_ord += 1
                session.add(e)
                await session.flush()
                event_ids.append(e.id)
                inserted += 1

        await session.commit()

    return {
        "inserted": inserted, "updated": updated, "deleted": deleted,
        "event_ids": event_ids, "years": [min_y, max_y],
    }


async def delete_event(persona_id: str, event_id: str) -> bool:
    """删除一条履历事件 (连带展开内容与关联记忆/财务)."""
    from sqlalchemy import delete, select

    async with async_session_factory() as session:
        ev = (
            await session.execute(
                select(LifeScaffoldEvent).where(
                    LifeScaffoldEvent.persona_id == persona_id,
                    LifeScaffoldEvent.id == event_id,
                )
            )
        ).scalar_one_or_none()
        if ev is None:
            return False

        if ev.memory_id:
            m = await session.get(EpisodicMemory, ev.memory_id)
            if m is not None:
                await session.delete(m)
        await session.execute(
            delete(LifeScaffoldContent).where(LifeScaffoldContent.event_id == event_id)
        )
        await session.delete(ev)
        await session.commit()
        return True


async def get_events_by_ids(persona_id: str, ids: list[str]) -> list[dict]:
    """按 id 取事件 (供插入后的 LLM 生成任务)."""
    if not ids:
        return []
    async with async_session_factory() as session:
        rows = (
            (
                await session.execute(
                    select(LifeScaffoldEvent)
                    .where(
                        LifeScaffoldEvent.persona_id == persona_id,
                        LifeScaffoldEvent.id.in_(ids),
                    )
                    .order_by(LifeScaffoldEvent.start_year.asc().nullslast(), LifeScaffoldEvent.ord)
                )
            )
            .scalars()
            .all()
        )
        return [
            {
                "id": r.id, "event_type": r.event_type,
                "start_year": r.start_year, "start_raw": r.start_raw,
                "payload": r.payload, "visibility": r.visibility,
                "processed": r.processed, "memory_id": r.memory_id,
            }
            for r in rows
        ]


async def stats(persona_id: str) -> dict:
    async with async_session_factory() as session:
        total = (
            await session.execute(
                select(func.count()).select_from(LifeScaffoldEvent).where(
                    LifeScaffoldEvent.persona_id == persona_id
                )
            )
        ).scalar() or 0
        done = (
            await session.execute(
                select(func.count()).select_from(LifeScaffoldEvent).where(
                    LifeScaffoldEvent.persona_id == persona_id,
                    LifeScaffoldEvent.processed.is_(True),
                )
            )
        ).scalar() or 0
        years = (
            await session.execute(
                select(func.min(LifeScaffoldEvent.start_year), func.max(LifeScaffoldEvent.start_year))
                .where(LifeScaffoldEvent.persona_id == persona_id)
            )
        ).first()
        return {
            "total": total, "processed": done, "pending": total - done,
            "year_from": years[0] if years else None,
            "year_to": years[1] if years else None,
        }


async def list_events(persona_id: str, limit: int = 500) -> list[dict]:
    from virtual_human.scaffold import LifeScaffoldContent

    async with async_session_factory() as session:
        rows = (
            (
                await session.execute(
                    select(LifeScaffoldEvent)
                    .where(LifeScaffoldEvent.persona_id == persona_id)
                    .order_by(
                        LifeScaffoldEvent.start_year.asc().nullslast(),
                        LifeScaffoldEvent.ord.asc(),
                    )
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )
        contents = (
            (
                await session.execute(
                    select(LifeScaffoldContent)
                    .where(LifeScaffoldContent.persona_id == persona_id)
                    .order_by(LifeScaffoldContent.event_id, LifeScaffoldContent.seq)
                )
            )
            .scalars()
            .all()
        )
        by_event: dict[str, list[dict]] = {}
        for c in contents:
            by_event.setdefault(c.event_id, []).append({
                "id": c.id, "seq": c.seq,
                "summary": c.summary, "content": c.content,
            })

        out = []
        for r in rows:
            blocks = by_event.get(r.id, [])
            out.append({
                "id": r.id, "ord": r.ord, "event_type": r.event_type,
                "start_year": r.start_year, "start_raw": r.start_raw, "end_year": r.end_year,
                "payload": r.payload, "visibility": r.visibility,
                "processed": r.processed, "sensitive": r.sensitive, "memory_id": r.memory_id,
                "contents": blocks,
                "summary": blocks[0]["summary"] if blocks else None,
                "content": blocks[0]["content"] if blocks else None,
            })
        return out


async def fetch_pending_batch(persona_id: str, size: int = 5) -> list[dict]:
    """按年份升序取一批未处理事件."""
    async with async_session_factory() as session:
        rows = (
            (
                await session.execute(
                    select(LifeScaffoldEvent)
                    .where(
                        LifeScaffoldEvent.persona_id == persona_id,
                        LifeScaffoldEvent.processed.is_(False),
                        LifeScaffoldEvent.sensitive.is_(False),
                    )
                    .order_by(
                        LifeScaffoldEvent.start_year.asc().nullslast(),
                        LifeScaffoldEvent.ord.asc(),
                    )
                    .limit(size)
                )
            )
            .scalars()
            .all()
        )
        return [
            {
                "id": r.id, "event_type": r.event_type,
                "start_year": r.start_year, "start_raw": r.start_raw,
                "payload": r.payload, "visibility": r.visibility,
                "sensitive": r.sensitive,
            }
            for r in rows
        ]


async def mark_sensitive(event_id: str) -> None:
    """把被内容审核拦截的事件标为敏感 (前端黄色显示, 不再送审)."""
    async with async_session_factory() as session:
        ev = await session.get(LifeScaffoldEvent, event_id)
        if ev is None:
            return
        ev.sensitive = True
        await session.commit()


async def mark_processed(event_id: str, memory_id: str | None) -> None:
    async with async_session_factory() as session:
        ev = await session.get(LifeScaffoldEvent, event_id)
        if ev is None:
            return
        ev.processed = True
        ev.memory_id = memory_id
        await session.commit()
