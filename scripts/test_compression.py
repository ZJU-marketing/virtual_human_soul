"""compression 中间件单测: 截断/丢块/结构保持."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from vh_generate.generate.compression import chars_of, compress_messages


def mk(n_pages: int = 12, page_len: int = 1200):
    msgs = [
        SystemMessage(content="SYS " * 50),
        HumanMessage(content="TASK " * 40),
    ]
    for i in range(n_pages):
        tc_id = f"call_{i}"
        msgs.append(AIMessage(content="", tool_calls=[
            {"name": "fetch_page", "args": {"source": "events", "page": i + 1}, "id": tc_id},
        ]))
        msgs.append(ToolMessage(content=f"P{i} " + "x" * page_len, tool_call_id=tc_id))
        msgs.append(AIMessage(content=f"读了第{i + 1}页, " + "y" * 800))
    return msgs


def test_short_history_untouched():
    msgs = mk(2)
    out = compress_messages(msgs)
    assert out is msgs  # 历史短直接原样返回


def test_old_tool_truncated_structure_kept():
    msgs = mk(12)
    out = compress_messages(msgs)
    assert len(out) == len(msgs)                     # 不丢块
    assert chars_of(out) < chars_of(msgs)            # 但变短
    # tool_call 配对保持: 每个 AI(tool_calls) 后仍跟同 id 的 ToolMessage
    pending = {}
    for m in out:
        if isinstance(m, AIMessage) and getattr(m, "tool_calls", None):
            for tc in m.tool_calls:
                pending[tc["id"]] = True
        elif isinstance(m, ToolMessage):
            assert m.tool_call_id in pending, "ToolMessage 失去配对 AI"
            del pending[m.tool_call_id]
    assert not pending
    # 头部 system/human 原样
    assert out[0].content == msgs[0].content
    assert out[1].content == msgs[1].content


def test_drop_blocks_when_over_budget():
    msgs = mk(20)
    out = compress_messages(msgs, budget_chars=8000, drop_old_blocks=True)
    assert chars_of(out) < 9000
    assert any("已省略" in m.content for m in out if isinstance(m, HumanMessage))
    # tool_call 配对仍完整
    pending = set()
    for m in out:
        if isinstance(m, AIMessage) and getattr(m, "tool_calls", None):
            pending |= {tc["id"] for tc in m.tool_calls}
        elif isinstance(m, ToolMessage):
            assert m.tool_call_id in pending
            pending.discard(m.tool_call_id)
    assert not pending


if __name__ == "__main__":
    test_short_history_untouched()
    test_old_tool_truncated_structure_kept()
    test_drop_blocks_when_over_budget()
    print("all compression tests passed")
