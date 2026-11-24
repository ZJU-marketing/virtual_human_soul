"""渐进式铸造的 LangGraph 编排.

状态机:
    START → ensure_skeleton → ensure_personality → fetch_batch ─┬─(有事件)→ generate_memories ─┐
                                                                  └─(无事件)→ expand_events → finalize → END
                                                                       ↑──────────────────────────────┘

expand_events: 为履历事件生成 life_scaffold_contents (摘要 + 正文)
finalize:      从履历+记忆归纳 kb_nodes 与 finance_events

特点:
    - 节点逻辑以 handlers 注入 (本模块不依赖 vh_api, 避免循环依赖)
    - 配 Postgres checkpointer 后, 任务中断可按 thread_id 续跑
    - astream(stream_mode="updates") 逐节点回报进度
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable, TypedDict

from langgraph.graph import END, START, StateGraph


class ScaffoldState(TypedDict, total=False):
    persona_id: str
    batch_size: int
    core_identity: str
    personality: str
    batch: list[dict]
    processed: int
    total: int
    generated: int
    event_contents: int
    error: str
    finished: bool


@dataclass
class ScaffoldHandlers:
    """节点实现 (由上层注入具体 IO)."""

    # 确保人物骨架存在 (为空时读履历生成); 返回 {"core_identity": str}
    ensure_skeleton: Callable[[ScaffoldState], Awaitable[dict]]
    # 确保性格导向存在; 返回 {"personality": str, "core_identity": str}
    ensure_personality: Callable[[ScaffoldState], Awaitable[dict]]
    # 取一批未处理事件; 返回 {"batch": [...], "total": int, "processed": int}
    fetch_batch: Callable[[ScaffoldState], Awaitable[dict]]
    # 消化一批事件; 返回 {"generated": int, "processed": int}
    generate_memories: Callable[[ScaffoldState], Awaitable[dict]]
    # 展开履历事件 (摘要+正文); 返回 {"event_contents": int}
    expand_events: Callable[[ScaffoldState], Awaitable[dict]]
    # 收尾: 归纳知识节点 + 抽取财务大事记; 返回 {"knowledge_nodes": int, "finance_events": int}
    finalize: Callable[[ScaffoldState], Awaitable[dict]]


def build_scaffold_graph(handlers: ScaffoldHandlers) -> StateGraph:
    g = StateGraph(ScaffoldState)

    async def _skeleton(state: ScaffoldState) -> dict:
        return await handlers.ensure_skeleton(state)

    async def _personality(state: ScaffoldState) -> dict:
        return await handlers.ensure_personality(state)

    async def _fetch(state: ScaffoldState) -> dict:
        return await handlers.fetch_batch(state)

    async def _generate(state: ScaffoldState) -> dict:
        return await handlers.generate_memories(state)

    async def _finalize(state: ScaffoldState) -> dict:
        return await handlers.finalize(state)

    async def _expand(state: ScaffoldState) -> dict:
        return await handlers.expand_events(state)

    g.add_node("ensure_skeleton", _skeleton)
    g.add_node("ensure_personality", _personality)
    g.add_node("fetch_batch", _fetch)
    g.add_node("generate_memories", _generate)
    g.add_node("expand_events", _expand)
    g.add_node("finalize", _finalize)

    g.add_edge(START, "ensure_skeleton")
    g.add_edge("ensure_skeleton", "ensure_personality")
    g.add_edge("ensure_personality", "fetch_batch")

    def _route(state: ScaffoldState) -> str:
        # 还有未处理事件 → 继续生成记忆; 否则进入履历展开
        return "generate_memories" if state.get("batch") else "expand_events"

    g.add_conditional_edges(
        "fetch_batch", _route,
        {"generate_memories": "generate_memories", "expand_events": "expand_events"},
    )
    g.add_edge("generate_memories", "fetch_batch")
    g.add_edge("expand_events", "finalize")
    g.add_edge("finalize", END)

    return g


def compile_scaffold_graph(handlers: ScaffoldHandlers, checkpointer: Any = None):
    """编译; 传 checkpointer 则支持断点续跑 (thread_id = scaffold-<persona_id>)."""
    graph = build_scaffold_graph(handlers)
    return graph.compile(checkpointer=checkpointer) if checkpointer is not None else graph.compile()
