"""vh_cli —— 命令行工具.

Windows 终端强制 UTF-8 输出 (sys.stdout.reconfigure), 避免中文乱码.
"""

from __future__ import annotations

import io
import sys


def force_utf8_stdio() -> None:
    """把 stdout/stderr 重配为 UTF-8, Windows GBK 控制台也能正常显示中文."""
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name)
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except AttributeError:   # 非文本流 (被重定向等) 时包一层
            setattr(sys, stream_name, io.TextIOWrapper(
                stream.buffer, encoding="utf-8", errors="replace",
            ) if hasattr(stream, "buffer") else stream)
