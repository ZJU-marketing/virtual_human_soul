"""验证 LangGraph 骨架编排: 状态机节点/边 + checkpointer 可用性."""

from vh_generate.generate.scaffold_graph import ScaffoldHandlers, build_scaffold_graph, compile_scaffold_graph


async def _noop(state):
    return {}


handlers = ScaffoldHandlers(
    ensure_skeleton=_noop,
    ensure_personality=_noop,
    fetch_batch=_noop,
    generate_memories=_noop,
    expand_events=_noop,
    finalize=_noop,
)

g = build_scaffold_graph(handlers)
print("graph ok, nodes:", sorted(g.nodes.keys()))

app = compile_scaffold_graph(handlers)
print("compiled ok:", type(app).__name__)

# 简单跑一次: fetch 返回空 batch → 应立即 END
import asyncio


async def main():
    async def h_s(s):
        return {"core_identity": "测试骨架"}

    async def h_p(s):
        return {"personality": "测试性格", "core_identity": s.get("core_identity", "")}

    async def h_f(s):
        return {"batch": [], "total": 0, "processed": 0}

    async def h_g(s):
        return {"generated": 0}

    async def h_fin(s):
        return {"knowledge_nodes": 0, "finance_events": 0}

    async def h_exp(s):
        return {"event_contents": 0}

    h2 = ScaffoldHandlers(
        ensure_skeleton=h_s, ensure_personality=h_p,
        fetch_batch=h_f, generate_memories=h_g,
        expand_events=h_exp, finalize=h_fin,
    )
    compiled = compile_scaffold_graph(h2)
    out = await compiled.ainvoke({"persona_id": "test", "batch_size": 5, "generated": 0})
    print("run ok, core =", out.get("core_identity"), "| personality =", out.get("personality"))


asyncio.run(main())
