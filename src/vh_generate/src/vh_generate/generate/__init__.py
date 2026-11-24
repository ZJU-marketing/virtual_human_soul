"""生成模块 (aio) —— 工具调用式录入 + LangChain 异步流式对话.

录入 (推荐): LLM 调工具直接落库, 流式过程打短日志
    ingest_persona_toolcall / ingest_persona_toolcall_async
对话: achat / astream_chat
"""

from vh_generate.generate.agent import achat, astream_chat, make_llm
from vh_generate.generate.checker import check_persona_async, fix_persona_issues_async
from vh_generate.generate.embeddings import embed_texts, embed_query, register as register_embeddings
from vh_generate.generate.editor import edit_persona_async
from vh_generate.generate.scaffold_gen import (
    expand_scaffold_events_async,
    finalize_persona_async,
    generate_personality_async,
    generate_skeleton_async,
    process_scaffold_events_async,
)
from vh_generate.generate.scaffold_graph import ScaffoldHandlers, compile_scaffold_graph
from vh_generate.generate.seeder_tools import (
    ingest_persona_toolcall,
    ingest_persona_toolcall_async,
)

# 嵌入启用时把查询向量化函数注册进知识库层 (依赖注入, 保持 virtual_human ← vh_generate 单向)
register_embeddings()

__version__ = "0.1.0"

__all__ = [
    # 录入 (tool-calling, 推荐)
    "ingest_persona_toolcall",
    "ingest_persona_toolcall_async",
    # 编辑 (tool-calling)
    "edit_persona_async",
    # 检查 (tool-calling 可选修复)
    "check_persona_async",
    "fix_persona_issues_async",
    # 骨架渐进生成
    "process_scaffold_events_async",
    "generate_personality_async",
    "generate_skeleton_async",
    "expand_scaffold_events_async",
    "finalize_persona_async",
    # 对话 (aio 流式)
    "achat",
    "astream_chat",
    "make_llm",
    # 嵌入 (可选)
    "embed_texts",
    "embed_query",
]
