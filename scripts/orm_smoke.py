"""ORM 冒烟测试: 建一个 persona + 知识树 + 记忆 + 财务账, 全链路读写."""

from virtual_human.db import session_factory
from virtual_human.models import (
    EpisodicMemory,
    FinanceBalance,
    FinanceEvent,
    KbEdge,
    KbNode,
    KbNodeContent,
    Persona,
)

def main() -> None:
    with session_factory() as session, session.begin():
        # 幂等: 清掉上次冒烟残留
        session.query(Persona).filter_by(slug="chen-zhiqiang").delete(synchronize_session=False)

        # persona
        p = Persona(
            slug="chen-zhiqiang",
            name="陈志强",
            status="live",
            core_identity="52 岁,中学语文老师,住在城东,妻子周敏,女儿陈小雨读大二。",
            voice_style="温和,爱引经据典,口头禅'我跟你说'",
        )
        session.add(p)
        session.flush()

        # 知识树: 章节 -> 内容块
        chapter = KbNode(
            persona_id=p.id, node_type="chapter", slug="career",
            title="教书三十年", summary="从 22 岁站上讲台到如今的职业轨迹。",
            load_level=1, importance=8, status="published",
        )
        session.add(chapter)
        session.flush()

        c1 = KbNodeContent(
            node_id=chapter.id, seq=1, label="初上讲台",
            content="1996 年第一次走进教室,粉笔灰落在袖口上,紧张得把'苟'念成了'句'。",
            token_estimate=60, importance=7, status="published",
        )
        c2 = KbNodeContent(
            node_id=chapter.id, seq=2, label="评上高级",
            content="2015 年评上高级教师那年,工资涨到八千多,家里终于换了台大冰箱。",
            token_estimate=55, importance=6, status="published",
        )
        session.add_all([c1, c2])

        # 联想边
        semantic = KbNode(
            persona_id=p.id, node_type="semantic", slug="money-attitude",
            title="对钱的态度", summary="钱要花在家上,但书不能省。",
            load_level=2, importance=7, status="published",
        )
        session.add(semantic)
        session.flush()
        session.add(KbEdge(src_node_id=chapter.id, dst_node_id=semantic.id, edge_type="related", weight=0.8))

        # 情景记忆 (财务大事的叙事面)
        mem = EpisodicMemory(
            persona_id=p.id, chapter_node_id=chapter.id,
            title="买下城东两居室",
            content="2018 年签完购房合同,手一直在抖。周敏在售楼部门口的台阶上坐了很久,一句'终于有个家了'说了三遍。",
            occurred_at="2018-05-20 10:00:00", location="城东售楼部",
            participants=["陈志强", "周敏"], emotion="激动", importance=9, status="published",
        )
        session.add(mem)
        session.flush()

        # 财务: 大额事件 + 家底
        session.add(FinanceEvent(
            persona_id=p.id, event_type="expense_buy", title="买下城东两居室",
            item_category="real_estate", amount=1870000, occurred_at="2018-05-20",
            balance_after=120000, memory_id=mem.id, note="首付加税费",
        ))
        session.add(FinanceBalance(
            persona_id=p.id, cash=426000, property_value=2100000, liability_balance=680000,
        ))

    # 读回验证
    with session_factory() as session:
        person = session.query(Persona).filter_by(slug="chen-zhiqiang").one()
        print(f"persona: {person.name} ({person.slug})")
        print(f"kb_nodes: {len(person.kb_nodes)}")
        node = person.kb_nodes[0]
        print(f"chapter: {node.title} | blocks: {[c.label for c in node.contents]}")
        edge = session.query(KbEdge).filter_by(src_node_id=node.id).one()
        print(f"edge: {node.slug} -{edge.edge_type}-> {edge.dst_node_id[:8]}...")
        mem = session.query(EpisodicMemory).filter_by(persona_id=person.id).one()
        print(f"memory: {mem.title} [{mem.emotion}]")
        evt = session.query(FinanceEvent).filter_by(persona_id=person.id).one()
        print(f"finance: {evt.title} {evt.amount} -> balance_after {evt.balance_after}, memory linked")
        bal = person.finance_balance
        net = float(bal.cash) + float(bal.property_value) - float(bal.liability_balance)
        print(f"balance: cash={bal.cash} property={bal.property_value} liability={bal.liability_balance} net={net}")

        # 清理冒烟数据: bulk delete 走 DB 级 ON DELETE CASCADE,
        # 避开 "ORM 试图置空 finance_balance 主键" 的问题
        session.query(Persona).filter_by(id=person.id).delete(synchronize_session=False)
        session.commit()
        print("smoke data cleaned")


if __name__ == "__main__":
    main()
