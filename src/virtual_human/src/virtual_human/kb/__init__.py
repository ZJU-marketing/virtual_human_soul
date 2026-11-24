"""kb —— 知识库 API.

同步: kb.retrieval (session_factory)
异步: kb.async_api (async_session_factory, aio 主路径)

LLM 流式对话请统一用异步层, 配合 virtual_human.db.run_async (Windows SelectorEventLoop).
"""

from virtual_human.kb.retrieval import (
    RetrievedContext,
    MemoryBlock,
    NodeView,
    NodeClue,
    TreeNode,
    get_persona,
    find_node,
    get_node,
    get_node_clues,
    get_kb_tree,
    render_persona_md,
    load_l0_identity,
    load_l1_skeleton,
    retrieve_context,
    list_finance_events,
    get_finance_balance,
    render_finance,
)

__all__ = [
    # 同步
    "RetrievedContext",
    "MemoryBlock",
    "NodeView",
    "NodeClue",
    "TreeNode",
    "get_persona",
    "find_node",
    "get_node",
    "get_node_clues",
    "get_kb_tree",
    "render_persona_md",
    "load_l0_identity",
    "load_l1_skeleton",
    "retrieve_context",
    "list_finance_events",
    "get_finance_balance",
    "render_finance",
]
