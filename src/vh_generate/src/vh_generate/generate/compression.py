"""上下文压缩中间件 —— 每次调用 LLM 前压缩消息历史, 控制上下文长度.

多轮 tool-calling 循环里, 历史消息 (尤其 ToolMessage 的页数据/落库结果) 会
越积越多, 最终撑爆上下文窗口。本模块在「发给模型前」做一层无损优先的压缩:

策略 (代价从小到大):
1. 旧块内的长文本只保留头尾 (工具结果开头通常是 id=xxx, 头部保留即不丢引用)
2. 带 tool_calls 的 AI 消息结构不动 (截断会破坏调用配对)
3. 超出预算且允许丢块时, 从最旧的中间块开始整块丢弃
   (system + 首条 human 任务指令 + 最近 keep_recent 块永远保留)

用法:
    payload = compress_messages(messages)          # 压缩副本
    llm.astream(payload)                           # 用压缩后的发给 LLM
    # messages 本体不动, 完整历史仍留在对话里
"""

from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

__all__ = ["compress_messages", "chars_of"]


def _content_len(m) -> int:
    c = m.content
    return len(c) if isinstance(c, str) else len(str(c or ""))


def chars_of(messages: list) -> int:
    """消息列表的总字符量 (粗略上下文长度)."""
    return sum(_content_len(m) for m in messages)


def _truncate(text: str, head: int, tail: int) -> str:
    if len(text) <= head + tail + 60:
        return text
    return f"{text[:head]}\n…(中间 {len(text) - head - tail} 字符已压缩)…\n{text[-tail:]}"


def _to_blocks(messages: list) -> list[list]:
    """消息 → 块. system/human 独立成块; AI 与其后续 ToolMessage 合为一块 (不可拆)."""
    blocks: list[list] = []
    cur: list | None = None
    for m in messages:
        if isinstance(m, (SystemMessage, HumanMessage)):
            blocks.append([m])
            cur = None
        elif isinstance(m, AIMessage):
            cur = [m]
            blocks.append(cur)
        else:
            if cur is None:
                cur = []
                blocks.append(cur)
            cur.append(m)
    return blocks


def _flatten(blocks: list[list]) -> list:
    return [m for b in blocks for m in b]


def compress_messages(
    messages: list,
    *,
    keep_recent: int = 6,
    head: int = 200,
    tail: int = 240,
    max_old_tool_chars: int = 500,
    budget_chars: int = 24000,
    drop_old_blocks: bool = False,
) -> list:
    """返回压缩后的消息副本 (原列表不改动).

    keep_recent:        最近 N 块完整保留
    head/tail:          旧消息截断时保留的头/尾字符数
    max_old_tool_chars: 旧工具结果超过该长度才截断
    budget_chars:       总字符预算, 超出才触发丢块
    drop_old_blocks:    True=允许整块丢弃最旧的中间块 (分页读取场景安全,
                        模型可重新 fetch_page; 引用旧 id 的编辑场景应设 False)
    """
    blocks = _to_blocks(messages)

    # 头部: 连续的 system / 首条 human (任务指令) 永远原样保留
    i = 0
    head_blocks: list[list] = []
    while i < len(blocks) and isinstance(blocks[i][0], (SystemMessage, HumanMessage)):
        head_blocks.append(blocks[i])
        i += 1
    body = blocks[i:]

    if len(body) <= keep_recent:
        return messages  # 历史还很短, 无需压缩

    recent = body[-keep_recent:]
    old = body[:-keep_recent]

    def _compress_block(block: list) -> list:
        out = []
        for m in block:
            if isinstance(m, ToolMessage) and _content_len(m) > max_old_tool_chars:
                out.append(ToolMessage(
                    content=_truncate(str(m.content), head, tail),
                    tool_call_id=m.tool_call_id,
                ))
            elif (
                isinstance(m, AIMessage)
                and not getattr(m, "tool_calls", None)   # 带 tool_calls 的保持结构
                and _content_len(m) > head + tail + 60
            ):
                m2 = m.model_copy()
                m2.content = _truncate(str(m.content), head, tail)
                out.append(m2)
            else:
                out.append(m)
        return out

    kept = [_compress_block(b) for b in old]
    total = chars_of(_flatten(head_blocks + kept + recent))

    dropped = 0
    if drop_old_blocks and total > budget_chars:
        idx = 0
        while total > budget_chars and idx < len(kept):
            total -= chars_of(kept[idx])
            idx += 1
        dropped = idx
        kept = kept[idx:]

    out = _flatten(head_blocks + kept + recent)
    if dropped:
        out = _flatten(head_blocks) + [
            HumanMessage(content=f"(更早的 {dropped} 轮中间过程已省略, 如需细节可重新分页读取)")
        ] + _flatten(kept + recent)
    return out
