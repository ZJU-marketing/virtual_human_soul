"""progressive (RangeFeed + 渐进 agent) 单测."""

import asyncio
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from vh_generate.generate.progressive import RangeFeed, _is_content_blocked


@dataclass
class Ev:
    start_year: int
    event_type: str
    payload: str


def mk_feed(n=53):
    items = [
        Ev(start_year=1970 + i * 2, event_type=f"事件{i}", payload=f"内容{i}")
        for i in range(n)
    ]
    return RangeFeed(
        title="人生履历", items=items,
        year_of=lambda x: x.start_year,
        render=lambda x: f"- {x.start_year} [{x.event_type}] {x.payload}",
        digest=lambda b: f"{len(b)} 条 · {b[0].start_year}~{b[-1].start_year}",
        max_per_call=10,
    )


def test_overview():
    f = mk_feed(30)
    ov = f.overview()
    assert "共 30 条" in ov
    assert "1970" in ov and "×1" in ov


def test_query_limit_capped_at_10():
    f = mk_feed(53)
    full, stub = f.query(None, None, 10)
    assert "返回 10/53" in full
    assert "还有 43 条未读" in full
    assert "start_from=" in full


def test_query_range_then_next():
    f = mk_feed(53)
    # 只查 1980-2000 范围: 年份 1970+2i → i=5..15 共 6 条... 实际 1980~2000 含 11 条
    full, _ = f.query(1980, 2000, 99)  # limit 会被压到 10
    assert "返回 10/11" in full
    assert "还有 1 条未读" in full
    # 小范围一次读完
    full2, stub2 = f.query(1980, 1990, 10)
    assert "返回 6/6" in full2
    assert "未读" not in full2
    assert stub2.startswith("【人生履历 1980~1990")


def test_query_empty():
    f = mk_feed(10)
    full, stub = f.query(2999, 3000, 10)
    assert "没有数据" in full
    assert stub == ""


def test_content_blocked_detect():
    class E(Exception):
        pass

    assert _is_content_blocked(E("Error code: 400 - Input text data may contain inappropriate content."))
    assert not _is_content_blocked(E("Error code: 429 - rate limit"))


if __name__ == "__main__":
    test_overview()
    test_query_limit_capped_at_10()
    test_query_range_then_next()
    test_query_empty()
    test_content_blocked_detect()
    print("all progressive tests passed")
