"""渐进式加载 agent 循环: 按时间范围分批读取 + 旧批折叠 + 压缩中间件.

背景: 人生履历/记忆动辄上百条, 一次性塞进 prompt 既容易超上下文,
也容易触发内容审核误判 (大段拼接文本)。

做法:
    - 大列表注册成 RangeFeed, 暴露 fetch_events(source, start_from, start_to, limit)
      工具; agent 自己按 start_time 范围渐进读取, 每次最多 limit (≤10) 条
    - 年份分布直方图 (overview) 直接放进首条 human 消息, agent 不读明细
      也能规划读取范围
    - 每读一批, 之前读过的批会被原地折叠成一行概要, 上下文只保留最近
      keep_full_pages 批全文
    - 发给 LLM 前再过一遍压缩中间件 (compression.compress_messages)
    - 若被内容审核拦截 (400 inappropriate), 自动把已读批全部折叠成占位符
      后继续, 让 agent 换个范围重试, 而不是整个任务失败
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from langchain_core.messages import AIMessageChunk, HumanMessage, SystemMessage, ToolMessage
from pydantic import BaseModel, Field

from vh_generate.generate.compression import chars_of, compress_messages

LogFn = Callable[..., None]
ChunkFn = Callable[[str], None]


class _StopSentinel:
    """execute_extra 返回该对象 = 任务完成, 结束循环."""

    __slots__ = ()


STOP = _StopSentinel()


class FetchEventsArgs(BaseModel):
    """按年份范围读取大文本数据 (人生履历/记忆等)。每次最多 10 条, 从 start_from 起顺序返回; 没读完会提示下一批起始年份, 据此渐进读取。"""

    source: str = Field(description="数据源名, 见系统提示, 如 events/memories")
    start_from: int | None = Field(
        default=None, description="起始年份 (含); 省略表示从最早开始", examples=[1981],
    )
    start_to: int | None = Field(
        default=None, description="结束年份 (含); 省略表示一直到最新", examples=[1990],
    )
    limit: int = Field(default=10, ge=1, le=10, description="本批条数, 最多 10")


@dataclass
class RangeFeed:
    """一个大列表的范围查询视图: render 出条目行, digest 出折叠概要."""

    title: str                                    # 展示名, 如 "人生履历"
    items: list                                   # 原始条目 (调用方保证有序)
    year_of: Callable[[Any], int]                 # 条目 -> 年份 (未知给 0)
    render: Callable[[Any], str]                  # 单条 -> 一行文本
    digest: Callable[[list], str]                 # 一批 -> 一行概要 (折叠后保留)
    max_per_call: int = 10

    def overview(self) -> str:
        """年份分布直方图 (每行一年), 供 agent 规划读取范围, 不含明细."""
        from collections import Counter

        years = sorted({self.year_of(x) for x in self.items})
        if not years:
            return f"{self.title}: 空"
        counter = Counter(self.year_of(x) for x in self.items)
        lines = []
        for y in years:
            label = str(y) if y else "未知年份"
            span = years[-1] - years[0]
            bar = "#" * counter[y]
            lines.append(f"{label}: {bar} ×{counter[y]}")
        head = f"{self.title} 共 {len(self.items)} 条, 年份分布 ({years[0] if years[0] else '未知'} ~ {years[-1]}):"
        return head + "\n" + "\n".join(lines)

    def query(
        self, start_from: int | None, start_to: int | None, limit: int,
    ) -> tuple[str, str]:
        """按范围取一批。返回 (批全文, 折叠概要)。"""
        lo = start_from if start_from is not None else -(10 ** 9)
        hi = start_to if start_to is not None else 10 ** 9
        limit = max(1, min(int(limit or self.max_per_call), self.max_per_call))
        matched = [x for x in self.items if lo <= self.year_of(x) <= hi]
        if not matched:
            rng = f"{start_from or '最早'}~{start_to or '最新'}"
            return f"【{self.title} {rng} 范围内没有数据】", ""
        batch = matched[:limit]
        rest = matched[limit:]
        body = "\n".join(self.render(x) for x in batch)
        rng = f"{start_from if start_from is not None else '最早'}~{start_to if start_to is not None else '最新'}"
        full = f"【{self.title} {rng} · 返回 {len(batch)}/{len(matched)} 条】\n{body}"
        if rest:
            ny = self.year_of(rest[0])
            full += (
                f"\n(该范围还有 {len(rest)} 条未读, 继续请再调一次 "
                f"start_from={ny}{f', start_to={start_to}' if start_to is not None else ''})"
            )
        stub = f"【{self.title} {rng} · {self.digest(batch)} · 已折叠, 需要可重读】"
        return full, stub


def _is_content_blocked(e: Exception) -> bool:
    """识别可跳过的拦截 (审核 400 / 上下文超限 / 输出被掐): 跳过当前单元即可."""
    name = type(e).__name__
    if "BadRequest" in name or "InvalidRequest" in name:
        return True
    s = str(e).lower()
    return (
        " 400" in s or "inappropriate" in s or "content_policy" in s or "content filter" in s
        or "context length" in s or "max tokens" in s or "too long" in s or "exceeds" in s
    )


async def astream_merged(
    llm: Any,
    messages: list,
    emit: ChunkFn,
    log: LogFn,
    label: str,
    retries: int = 1,
) -> AIMessageChunk | None:
    """流式收整消息。被拦截/超限重试一次, 仍失败返回 None (调用方跳过)."""
    for attempt in range(retries + 1):
        try:
            chunks: list[AIMessageChunk] = []
            async for c in llm.astream(messages):
                chunks.append(c)
                if c.content:
                    emit(c.content)
            if not chunks:
                return None
            merged = chunks[0]
            for x in chunks[1:]:
                merged = merged + x
            return merged
        except Exception as e:
            if _is_content_blocked(e) and attempt < retries:
                log(f"  ⚠ {label} 被拦截 (400), 重试一次…")
                continue
            if _is_content_blocked(e):
                log(f"  ⚠ {label} 再次被拦截, 跳过")
                return None
            raise


async def _astream_text(llm: Any, messages: list, emit: ChunkFn) -> str:
    """流式收一次补全, 返回全文."""
    parts: list[str] = []
    async for c in llm.astream(messages):
        if c.content:
            parts.append(c.content if isinstance(c.content, str) else str(c.content))
            emit(c.content)
    return "".join(parts).strip()


def _batched(items: list, size: int):
    for i in range(0, len(items), size):
        yield i, items[i:i + size]


async def _try_llm_text(
    llm: Any,
    messages: list,
    emit: ChunkFn,
    log: LogFn,
    label: str,
    retries: int = 1,
) -> str | None:
    """带 400 重试的流式补全。被拦截且重试仍拦截 → 返回 None (调用方决定降级策略)."""
    last: Exception | None = None
    for attempt in range(retries + 1):
        try:
            return await _astream_text(llm, messages, emit)
        except Exception as e:
            if not _is_content_blocked(e):
                raise
            last = e
            log(f"  ⚠ {label} 被拦截 (400){', 重试一次…' if attempt < retries else ''}")
    assert last is not None
    return None


async def run_incremental_summary(
    llm: Any,
    system_prompt: str,
    feed: "RangeFeed",
    task_desc: str,
    *,
    notes_hint: str = "阶段笔记 (累积归纳, 每批后更新, 不超过 400 字)",
    final_instruction: str = "全部批次已读完。请依据阶段笔记, 直接输出最终结果全文。",
    batch_size: int = 10,
    max_batches: int = 40,
    on_log: LogFn | None = None,
    on_chunk: ChunkFn | None = None,
) -> str:
    """「读取→生成→再读下一批」的增量归并 (map-reduce).

    每次调用 LLM 的上下文 = system + 运行笔记 + 当前一批 (≤ batch_size 条),
    长度基本恒定。某批被 400 拦截时逐级降面:
        整批 (10 条) → 重试 → 仍拦 → 一条一条载入 (单条笔记) → 单条仍拦 → 跳过该条
    返回最终输出文本。
    """
    log = on_log or (lambda *a, **k: None)
    emit = on_chunk or (lambda t: None)

    items = feed.items
    if not items:
        raise ValueError(f"{feed.title} 为空, 无法{task_desc}")

    total_batches = min(max_batches, (len(items) + batch_size - 1) // batch_size)
    log(f"  ⚙ {feed.title} {len(items)} 条 → {total_batches} 批, 逐批读取→归纳→再读下一批")

    notes = ""
    for no, (offset, batch) in enumerate(_batched(items[: batch_size * max_batches], batch_size), 1):
        body = "\n".join(feed.render(x) for x in batch)
        user = (
            f"任务: {task_desc}\n\n"
            f"{feed.overview()}\n\n"
            f"【{notes_hint}】\n{notes or '(暂无, 这是第一批)'}\n\n"
            f"【{feed.title} 第 {no}/{total_batches} 批 · 第 {offset + 1}~{offset + len(batch)} 条】\n{body}\n\n"
            f"请读取这一批, 并把新信息合并进阶段笔记后直接输出更新版笔记。"
            f"{'这是最后一批之前的一批, 笔记要为最终输出备齐要素。' if no < total_batches else ''}"
        )
        messages: list = [SystemMessage(content=system_prompt), HumanMessage(content=user)]
        text = await _try_llm_text(llm, messages, emit, log, f"第 {no} 批")

        if text is None:
            # 整批被拦截 → 降级: 一条一条载入 (上下文更小, 逐条归纳)
            log(f"  ⤷ 第 {no} 批整批被拦截, 降级为逐条载入 ({len(batch)} 条)…")
            for j, item in enumerate(batch, 1):
                one = feed.render(item)
                u1 = (
                    f"任务: {task_desc}\n\n"
                    f"【{notes_hint}】\n{notes or '(暂无)'}\n\n"
                    f"【{feed.title} 第 {no} 批 · 逐条载入 {j}/{len(batch)}】\n{one}\n\n"
                    "请读取这一条, 把新信息合并进阶段笔记后输出更新版笔记。"
                )
                m1: list = [SystemMessage(content=system_prompt), HumanMessage(content=u1)]
                t1 = await _try_llm_text(llm, m1, emit, log, f"第 {no} 批第 {j} 条")
                if t1 is None:
                    line = feed.digest([item])
                    log(f"  ⚠ 单条仍被拦截, 跳过: {line}")
                    notes = f"{notes}\n(该条被审核拦截, 仅保留概要: {line})".strip()
                    continue
                notes = (t1 or feed.digest([item]))[-2000:]
            log(f"  ⤷ 第 {no}/{total_batches} 批逐条降级完成 → 笔记 {len(notes)} 字")
            continue

        if not text:
            text = feed.digest(batch)
        notes = text[-2000:]   # 只留更新版笔记本身
        log(f"  ⤷ 第 {no}/{total_batches} 批已消化 → 笔记 {len(notes)} 字")

    final_messages: list = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=f"【{notes_hint}】\n{notes}\n\n{final_instruction}"),
    ]
    result = await _try_llm_text(llm, final_messages, emit, log, "最终输出")
    if not result:
        raise ValueError("LLM 未输出最终结果")
    log(f"  ✓ 增量归并完成, 最终输出 {len(result)} 字")
    return result


async def run_progressive_agent(
    llm: Any,
    system_prompt: str,
    human_text: str,
    feeds: dict[str, RangeFeed],
    extra_tools: list[type[BaseModel]] = (),
    execute_extra: Callable[[str, dict], Awaitable[Any]] | None = None,
    on_log: LogFn | None = None,
    on_chunk: ChunkFn | None = None,
    max_turns: int = 80,
    keep_full_pages: int = 2,
    max_blocks: int = 3,
) -> list:
    """跑一个「范围渐进读取 + 工具落库」的 agent 循环, 返回完整消息列表.

    - fetch_events 每次最多取 10 条; 旧批折叠 + 压缩中间件双重控制上下文
    - execute_extra(name, args) 执行业务工具; 返回 STOP 表示任务完成
    - 连续被内容审核拦截 max_blocks 次才放弃, 期间自动折叠已读内容降面
    """
    log = on_log or (lambda *a, **k: None)
    emit = on_chunk or (lambda t: None)

    bound = llm.bind_tools([FetchEventsArgs, *extra_tools])
    messages: list = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=human_text),
    ]
    fetch_stubs: list[tuple[str, str]] = []   # (tool_call_id, 折叠概要), 按读取顺序

    def _fold(keep_last: int = keep_full_pages, override: str | None = None) -> None:
        """把较早读取的批替换成折叠概要; override 非空时把全部已读批压成该占位符."""
        if not fetch_stubs:
            return
        if override is not None:
            keep_ids: set[str] = set()
            mapping = {tid: override for tid, _ in fetch_stubs}
        else:
            keep_ids = {tid for tid, _ in fetch_stubs[-keep_last:]} if keep_last > 0 else set()
            mapping = {tid: stub for tid, stub in fetch_stubs if tid not in keep_ids}
        for m in messages:
            if isinstance(m, ToolMessage) and m.tool_call_id in mapping:
                if m.content != mapping[m.tool_call_id]:
                    m.content = mapping[m.tool_call_id]

    blocks_hit = 0
    for _turn in range(max_turns):
        # ---- 压缩中间件: 发给 LLM 前压缩历史, 本体不动 ----
        payload = compress_messages(messages, drop_old_blocks=True)
        log(f"  ◔ 上下文 {chars_of(messages)} → {chars_of(payload)} 字符")

        chunks: list[AIMessageChunk] = []
        try:
            async for c in bound.astream(payload):
                chunks.append(c)
                if c.content:
                    emit(c.content)
        except Exception as e:
            if _is_content_blocked(e) and blocks_hit < max_blocks:
                blocks_hit += 1
                _fold(override="[该批内容无法通过安全审核, 已折叠跳过; 请换一个更小的年份范围重试]")
                log(f"  ⚠ 内容被安全审核拦截 (第 {blocks_hit}/{max_blocks} 次), 已折叠已读批, 换范围重试…")
                continue
            raise
        if not chunks:
            break
        ai = chunks[0]
        for c in chunks[1:]:
            ai = ai + c
        messages.append(ai)

        calls = ai.tool_calls or []
        if not calls:
            break   # 模型不再调工具 = 自然结束

        stop = False
        for tc in calls:
            name = tc.get("name") or ""
            args = tc.get("args") or {}
            tid = tc.get("id") or ""

            if name == "FetchEventsArgs":
                src = str(args.get("source") or "").strip()
                feed = feeds.get(src)
                if feed is None:
                    result = f"未知数据源: {src}。可用: {', '.join(feeds) or '(无)'}"
                else:
                    result, stub = feed.query(
                        args.get("start_from"), args.get("start_to"),
                        args.get("limit") or feed.max_per_call,
                    )
                    log(f"  ⤷ fetch_events {feed.title} {args.get('start_from') or '最早'}~{args.get('start_to') or '最新'}")
                    if stub:
                        fetch_stubs.append((tid, stub))
                messages.append(ToolMessage(content=result, tool_call_id=tid))
                _fold()
            elif execute_extra is not None:
                result = await execute_extra(name, args)
                if result is STOP:
                    messages.append(ToolMessage(content="已完成, 任务结束。", tool_call_id=tid))
                    stop = True
                else:
                    messages.append(ToolMessage(content=str(result), tool_call_id=tid))
            else:
                messages.append(ToolMessage(content=f"未知工具: {name}", tool_call_id=tid))
        if stop:
            break
    else:
        log(f"  ⚠ 达到最大轮数 {max_turns}, 提前结束")

    return messages
