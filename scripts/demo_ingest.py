"""seeder 演示: 骨架描述 → 生成人设 → 灌库 → 检索验证.

用法:
    uv run python scripts/demo_ingest.py                 # 无 key 时走 mock
    OPENAI_API_KEY=sk-xxx uv run python scripts/demo_ingest.py  # 真实 LLM 生成
"""

from __future__ import annotations

from vh_generate.generate import ingest_persona
from virtual_human.db import session_factory
from virtual_human.kb import load_l0_identity, load_l1_skeleton, render_finance
from virtual_human.models import EpisodicMemory, KbNode, KbNodeContent, Persona

SKELETON = (
    "陈志强, 52岁, 市一中语文老师, 教了三十年书。妻子周敏是会计, 女儿陈小雨在读大二。"
    "1994年大学毕业入职, 2015年评上高级教师, 2018年在城东买了187万的房子, 2023年送女儿上大学。"
    "性格温和, 爱引经据典, 口头禅是'我跟你说'。"
)


def main() -> None:
    print("[骨架输入]")
    print(SKELETON)

    persona_id = ingest_persona(SKELETON)
    print(f"\n[已灌库] persona_id={persona_id}")

    with session_factory() as session:
        persona = session.get(Persona, persona_id)

        print("\n[L0 身份]")
        print(load_l0_identity(persona))

        print("\n[L1 骨架]")
        print(load_l1_skeleton(session, persona_id))

        print("\n[知识树明细]")
        nodes = session.query(KbNode).filter_by(persona_id=persona_id).all()
        for n in nodes:
            print(f"  {n.title} ({n.node_type}, imp={n.importance}): {n.summary}")
            for c in n.contents:
                print(f"    - {c.label}: {len(c.content)}字")

        print("\n[情景记忆]")
        for m in session.query(EpisodicMemory).filter_by(persona_id=persona_id).all():
            print(f"  {m.occurred_at} {m.title} [{m.emotion}] {len(m.content)}字")

        print("\n[账本]")
        print(render_finance(session, persona.slug))


if __name__ == "__main__":
    main()
