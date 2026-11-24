"""端到端演示: 灌入演示数据 (含向量) → 渐进式检索 → LLM 生成对话.

需要 OPENAI_API_KEY (或兼容端点). 只测检索链路时用 --no-llm.
"""

from __future__ import annotations

import argparse

from vh_generate.generate import embed_texts
from vh_generate.generate.agent import chat
from virtual_human.db import session_factory
from virtual_human.kb import retrieve_context, render_finance, load_l0_identity
from virtual_human.models import (
    EpisodicMemory,
    FinanceBalance,
    FinanceEvent,
    KbEmbedding,
    KbNode,
    KbNodeContent,
    Persona,
)

SLUG = "chen-zhiqiang"


def seed(session) -> str:
    """灌入演示 persona, 返回 persona_id."""
    session.query(Persona).filter_by(slug=SLUG).delete(synchronize_session=False)

    p = Persona(
        slug=SLUG,
        name="陈志强",
        status="live",
        core_identity="我叫陈志强, 52 岁, 在市一中教了三十年语文。妻子周敏, 女儿陈小雨在读大二。住在城东的两居室。",
        bio_skeleton="1994 年大学毕业当老师; 2015 年评上高级; 2018 年买城东两居室; 2023 年送女儿上大学。",
        voice_style="温和, 爱引经据典, 口头禅'我跟你说'",
        context_token_budget=1500,
    )
    session.add(p)
    session.flush()

    node = KbNode(
        persona_id=p.id, node_type="semantic", slug="family-values",
        title="对家的看重", summary="家比什么都重, 买房和女儿上大学是这辈子最得意两件事。",
        load_level=2, importance=9, status="published",
    )
    session.add(node)
    session.flush()

    c1 = KbNodeContent(
        node_id=node.id, seq=1, label="买房的心路",
        content="2018 年买房那会儿, 我和周敏把存折翻来覆去数了三遍。首付掏空了积蓄, 但签完合同那天我们在售楼部门口坐了很久, 她说'终于有个家了'。我跟你说, 那不是房子, 是我们后半辈子的根。",
        token_estimate=90, importance=9, status="published",
    )
    c2 = KbNodeContent(
        node_id=node.id, seq=2, label="女儿的教育",
        content="小雨考上大学那天, 我把她小时候的作文翻出来看了一晚上。孩子妈哭得不行, 我嘴上说'哭什么', 转头也红了眼眶。",
        token_estimate=70, importance=8, status="published",
    )
    session.add_all([c1, c2])
    session.flush()

    mem = EpisodicMemory(
        persona_id=p.id, chapter_node_id=node.id,
        title="签购房合同那天",
        content="2018 年 5 月 20 号, 签购房合同。手续费加首付一共付了一百七十多万, 手一直在抖。周敏在台阶上坐了很久, 一句'终于有个家了'说了三遍。晚饭就在楼下小馆子吃的, 两个人都没怎么说话, 但心里踏实。",
        occurred_at="2018-05-20 10:00:00", location="城东售楼部",
        participants=["陈志强", "周敏"], emotion="激动", importance=9, status="published",
    )
    session.add(mem)
    session.flush()

    session.add(FinanceEvent(
        persona_id=p.id, event_type="expense_buy", title="买下城东两居室",
        item_category="real_estate", amount=1870000, occurred_at="2018-05-20",
        balance_after=120000, memory_id=mem.id, note="首付加税费",
    ))
    session.add(FinanceBalance(
        persona_id=p.id, cash=426000, property_value=2100000, liability_balance=680000,
    ))
    session.flush()

    # 向量: 给两个内容块 + 一条记忆建索引
    texts = [c1.content, c2.content, mem.content]
    vecs = embed_texts(texts)
    for eid, vec in zip([c1.id, c2.id, mem.id], vecs):
        session.add(KbEmbedding(
            entity_type="content" if eid != mem.id else "memory",
            entity_id=eid, model="text-embedding-3-small", embedding=vec,
        ))

    return p.id


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-llm", action="store_true", help="只测检索, 不调 LLM")
    parser.add_argument("--query", default="你还记得当年买房的事吗? 花了多少钱?")
    args = parser.parse_args()

    with session_factory() as session, session.begin():
        pid = seed(session)

    with session_factory() as session:
        print("=" * 50)
        print("[L0 身份]")
        persona = session.query(Persona).filter_by(slug=SLUG).one()
        print(load_l0_identity(persona))

        print("\n" + "=" * 50)
        print(f"[检索] query={args.query!r}")
        ctx = retrieve_context(session, SLUG, args.query)
        for b in ctx.blocks:
            print(f"  score={b.score:.3f} tokens={b.token_estimate:>4} [{b.source}] {b.title}" + (f"({b.label})" if b.label else ""))
        print(f"  预算 {ctx.budget} / 已用 {ctx.used_tokens}")

        print("\n" + "=" * 50)
        print("[账本]")
        print(render_finance(session, SLUG))

    if args.no_llm:
        return

    print("\n" + "=" * 50)
    print("[生成] 陈志强:")
    answer = chat(SLUG, args.query)
    print(answer)


if __name__ == "__main__":
    main()
