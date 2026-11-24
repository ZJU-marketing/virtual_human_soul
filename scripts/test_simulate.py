"""simulate —— 人物模拟模块 (测试: persona_id 初始化 system prompt + 多轮对话结构)."""

from __future__ import annotations

from virtual_human.db import session_factory
from virtual_human.models import Persona
from vh_simulate import PersonaAgent


def main() -> None:
    with session_factory() as session:
        persona = session.query(Persona).first()
        if persona is None:
            print("库中无 persona, 先跑 scripts/demo_ingest.py")
            return
        pid = persona.id
        print(f"[persona] {persona.name} ({pid})")

    # 初始化: persona_id → system prompt
    agent = PersonaAgent.init(pid)
    print(f"[agent] name={agent.name} slug={agent.persona_slug}")
    print(f"[system prompt] {len(agent.system_prompt)} 字符")
    print("-" * 40)
    print(agent.system_prompt[:600])
    print("...")
    print("-" * 40)

    # 对话结构验证 (无 OPENAI_API_KEY 时 make_llm 会失败, 所以只测 prompt 组装与历史管理)
    try:
        answer = agent.say("你好, 最近怎么样?")
        print(f"[回复] {answer}")
        print(f"[history] {len(agent.history)} 条")
        agent.reset()
        print(f"[reset 后 history] {len(agent.history)} 条")
    except Exception as e:
        print(f"[LLM 未配置, 跳过真实对话] {type(e).__name__}: {str(e)[:80]}")
        print("配置 OPENAI_API_KEY 后即可完整对话")


if __name__ == "__main__":
    main()
