"""vh_generate (aio) —— 生成模块.

主路径全部为 Python async API; 入口统一 run_async().
"""

from vh_generate.generate import (
    achat,
    astream_chat,
    make_llm,
    ingest_persona_toolcall,
    ingest_persona_toolcall_async,
)

__version__ = "0.1.0"

__all__ = [
    "achat",
    "astream_chat",
    "make_llm",
    "ingest_persona_toolcall",
    "ingest_persona_toolcall_async",
]
