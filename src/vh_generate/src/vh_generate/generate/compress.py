"""上下文压缩 (aio) —— 给 PersonaAgent / API 层复用的摘要压缩中间件."""

from __future__ import annotations

import sys

COMPRESS_RATIO = 2.5

COMPRESS_PROMPT = (
    "把以下人设模拟对话压缩为第三人称要点笔记 (300字内)。"
    "保留: 提到的事实(数字/人名/事件)、未解决的话题、情绪基调。删除: 寒暄、重复。"
)


def _stderr_log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)
