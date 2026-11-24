"""验证两个新 API: get_node (渐进加载) + get_node_clues (关联线索)."""

from __future__ import annotations

from virtual_human.db import session_factory
from virtual_human.kb import get_node, get_node_clues, get_kb_tree, render_persona_md, find_node
from virtual_human.models import KbEdge, KbNode, Persona


def main() -> None:
    with session_factory() as session:
        persona = session.query(Persona).first()
        if persona is None:
            print("库中无 persona, 先跑 vh-ingest 生成一个")
            return
        print(f"persona: {persona.slug}\n")

        nodes = session.query(KbNode).filter_by(persona_id=persona.id).all()
        if not nodes:
            print("该 persona 无知识节点, 先跑 vh-ingest 生成一个")
            return
        target = nodes[0]

        # 给第一个节点补两条联想边 (演示用): 指向其余节点
        others = nodes[1:]
        if others and not session.query(KbEdge).filter_by(src_node_id=target.id).first():
            session.add(KbEdge(src_node_id=target.id, dst_node_id=others[0].id,
                               edge_type="related", weight=0.8, note="同一段经历"))
            if len(others) > 1:
                session.add(KbEdge(src_node_id=others[1].id, dst_node_id=target.id,
                                   edge_type="elaborates", weight=0.6))
            session.commit()

        print("=" * 50)
        print(f"[get_node level=1] slug={target.slug}")
        v1 = get_node(session, persona.id, target.slug, load_level=1)
        print(f"  title={v1.title} type={v1.node_type} tokens={v1.token_estimate}")
        print(f"  contents={'有' if v1.contents else '无(符合预期)'}")
        print(f"  render:\n{v1.render()}")

        print("\n" + "=" * 50)
        print(f"[get_node level=2] slug={target.slug}")
        v2 = get_node(session, persona.id, target.slug, load_level=2)
        print(f"  contents={len(v2.contents)}块 tokens={v2.token_estimate}")
        print(f"  render:\n{v2.render()}")

        # 验证联动计数
        session.expire_all()
        node_after = find_node(session, persona.id, target.slug)
        print(f"\n  联动: node.access_count={node_after.access_count}, "
              f"content.access_count={[c.access_count for c in node_after.contents]}")

        # 参数校验
        try:
            get_node(session, persona.id, target.slug, load_level=3)
        except ValueError as e:
            print(f"  level=3 拒绝: {e}")
        try:
            get_node(session, persona.id, "不存在的slug", load_level=1)
        except KeyError as e:
            print(f"  未知slug拒绝: {e}")

        print("\n" + "=" * 50)
        print(f"[get_node_clues] slug={target.slug}")
        clues = get_node_clues(session, persona.id, target.slug)
        if not clues:
            print("  (无边)")
        for c in clues:
            print(f"  w={c.weight:.2f} [{c.direction}/{c.edge_type}] -> {c.neighbor_slug} ({c.neighbor_title})")
            print(f"    render: {c.render()}")

        print("\n" + "=" * 50)
        print("[get_kb_tree] 整棵树 (slug = persona_id/node_type)")
        tree = get_kb_tree(session, persona.id)
        print(tree.render())
        print(f"\n  节点总数(含根): {len(list(tree.walk()))}")

        print("\n" + "=" * 50)
        print("[render_persona_md] 默认披露阈值=1")
        print(render_persona_md(session, persona.id))

        # 演示披露度: 把"生活态度"调成 0 (隐藏 summary)
        from virtual_human.models import KbNode as KN
        hidden = session.query(KN).filter_by(persona_id=persona.id, title="生活态度").one()
        hidden.disclosure_level = 0
        session.commit()

        print("=" * 50)
        print("[render_persona_md] 生活态度 disclosure=0 (隐藏) + 阈值=1")
        print(render_persona_md(session, persona.id))

        print("=" * 50)
        print("[render_persona_md] 阈值=8 (只披露 importance>=8 且 disclosure>=8)")
        hidden.disclosure_level = 9
        session.commit()
        print(render_persona_md(session, persona.id, min_disclosure=8))


if __name__ == "__main__":
    main()
