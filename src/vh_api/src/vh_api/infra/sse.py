"""基础设施 —— SSE 通道.

SSE 事件协议 (text/event-stream, data: JSON):
    {"type": "log",   "data": str}      工具调用短日志
    {"type": "chunk", "data": str}      LLM 文本流 (打字机)
    {"type": "recall","data": {...}}    对话: 记忆装载明细
    {"type": "done",  "data": {...}}    完成 (persona_id / session_id / issues / ok)
    {"type": "error", "data": str}      错误

SseChannel 把"同步回调风格"的底层 API (on_log/on_chunk) 桥接成异步流,
后台任务跑用例, 前台 async for 逐事件外推.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any, AsyncIterator, Awaitable, Callable


def sse_encode(event_type: str, data: Any) -> str:
    """一条 SSE 帧."""
    return f"data: {json.dumps({'type': event_type, 'data': data}, ensure_ascii=False)}\n\n"


@dataclass
class SseChannel:
    """回调 → 异步流的桥.

    用法:
        ch = SseChannel()

        async def use_case():
            result = await some_async_api(on_log=ch.log, on_chunk=ch.chunk)
            ch.done({"persona_id": result})

        return ch.stream(use_case())   # FastAPI StreamingResponse
    """

    q: asyncio.Queue = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        self.q = asyncio.Queue()

    # ---- 生产端 (给底层 API 的回调) ----
    def log(self, msg: str) -> None:
        self.q.put_nowait(("log", msg))

    def chunk(self, text: str) -> None:
        self.q.put_nowait(("chunk", text))

    def done(self, data: Any) -> None:
        self.q.put_nowait(("done", data))

    def error(self, msg: str) -> None:
        self.q.put_nowait(("error", msg))

    # ---- 消费端 (FastAPI 流) ----
    async def stream(self, use_case: Awaitable) -> AsyncIterator[str]:
        """驱动用例协程, 直到 done/error. 客户端断开时取消用例."""
        task = asyncio.create_task(use_case)
        try:
            while True:
                event_type, data = await self.q.get()
                yield sse_encode(event_type, data)
                if event_type in ("done", "error"):
                    break
            # 用例异常转 error 帧
            try:
                await task
            except Exception as e:
                yield sse_encode("error", f"{type(e).__name__}: {e}")
        except Exception as e:               # 含客户端断开 (GeneratorExit 等)
            task.cancel()
            raise
