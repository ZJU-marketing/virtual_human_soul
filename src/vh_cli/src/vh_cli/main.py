"""vh_cli 入口 (aio): 骨架提示词 → 生成知识库内容 → 落库, 全程终端 UTF-8 输出.

命令 (uv 工作区安装后可直接调用):
    uv run vh-ingest "陈志强, 52岁, 语文老师..."   # 生成并灌库
    uv run vh-ingest --interactive                # 交互输入
    uv run vh-show                                # 查看库中人物与知识树
    uv run vh-chat <slug> "问题"                   # 与人物对话 (流式)

内部全部走 Python async API (async DB + 异步流式 LLM), 由 run_async 驱动.
"""

from __future__ import annotations

import argparse
import sys

from vh_cli.io_setup import force_utf8_stdio

STEP = "=" * 60


def _fmt_head(title: str) -> None:
    print(f"\n{STEP}\n {title}\n{STEP}")


# ---------------------------------------------------------------------
# vh-ingest: 新建流程 (默认) / 修改流程 (--edit)
# ---------------------------------------------------------------------
async def _ingest_create_async(skeleton: str, model: str | None) -> int:
    """新建流程: 骨架提示词 → LLM 生成完整人设 → 落库."""
    from virtual_human.db import async_session_factory
    from virtual_human.kb.async_api import aget_kb_tree
    from vh_generate.generate import ingest_persona_toolcall_async

    _fmt_head("[新建流程 1/2] 骨架输入")
    print(skeleton)

    _fmt_head("[新建流程 2/2] LLM 录入中 (流式输出, 工具调用实时落库)")

    def _log(msg: str) -> None:
        print(msg, flush=True)

    def _chunk(text: str) -> None:
        print(text, end="", flush=True)

    persona_id = await ingest_persona_toolcall_async(
        skeleton, model=model, on_log=_log, on_chunk=_chunk
    )

    _fmt_head("完成")
    print(f"  persona_id = {persona_id}")

    _fmt_head("知识树验证")
    async with async_session_factory() as session:
        print((await aget_kb_tree(session, persona_id)).render())

    print("\n提示: 用 vh-chat 与该人物对话; 修改设定用 vh-ingest --edit <slug> \"要求\"")
    return 0


async def _ingest_check_async(slug: str, fix: bool, model: str | None) -> int:
    """检查流程: LLM 对照人物设定全面检查库中数据, 可选自动修复."""
    from sqlalchemy import select

    from vh_generate.generate import check_persona_async
    from virtual_human.db import async_session_factory
    from virtual_human.models import Persona

    async with async_session_factory() as session:
        q = select(Persona).where((Persona.slug == slug) | (Persona.id == slug))
        persona = (await session.execute(q)).scalars().first()
        if persona is None:
            print(f"错误: 找不到人物 '{slug}' (可用 vh-show 查看全部)", file=sys.stderr)
            return 2

    mode = "检查+修复" if fix else "只检查"
    _fmt_head(f"[检查流程 1/2] 目标: {persona.name} (slug={persona.slug}) 模式: {mode}")

    _fmt_head("[检查流程 2/2] LLM 检查中 (流式输出)")

    def _log(msg: str) -> None:
        print(msg, flush=True)

    def _chunk(text: str) -> None:
        print(text, end="", flush=True)

    issues = await check_persona_async(
        persona.id, fix=fix, model=model, on_log=_log, on_chunk=_chunk
    )

    _fmt_head("检查结果汇总")
    if issues:
        for i in issues:
            print(f"  {i}")
        print(f"\n  共 {len(issues)} 项内置规则提示 (LLM 详细报告见上方)")
        return 0
    print("  内置规则全部通过 ✓ (LLM 详细报告见上方)")
    return 0


async def _ingest_edit_async(slug: str, instruction: str, model: str | None) -> int:
    """修改流程: 目标人物 + 一句要求 → LLM 检查设定 → 调工具修改."""
    from sqlalchemy import select

    from vh_generate.generate import edit_persona_async
    from virtual_human.db import async_session_factory
    from virtual_human.models import Persona

    async with async_session_factory() as session:
        q = select(Persona).where((Persona.slug == slug) | (Persona.id == slug))
        persona = (await session.execute(q)).scalars().first()
        if persona is None:
            print(f"错误: 找不到人物 '{slug}' (可用 vh-show 查看全部)", file=sys.stderr)
            return 2

    _fmt_head("[修改流程 1/2] 目标与要求")
    print(f"  人物: {persona.name} (slug={persona.slug})")
    print(f"  要求: {instruction}")

    _fmt_head("[修改流程 2/2] LLM 检查并修改 (流式输出, 工具调用实时落库)")

    def _log(msg: str) -> None:
        print(msg, flush=True)

    def _chunk(text: str) -> None:
        print(text, end="", flush=True)

    ok = await edit_persona_async(persona.id, instruction, model=model, on_log=_log, on_chunk=_chunk)

    _fmt_head("完成" if ok else "警告")
    if ok:
        async with async_session_factory() as session:
            from virtual_human.kb.async_api import arender_persona_md

            print(await arender_persona_md(session, persona.id))
    print()
    return 0 if ok else 1


def ingest(argv: list[str] | None = None) -> int:
    force_utf8_stdio()

    parser = argparse.ArgumentParser(
        prog="vh-ingest",
        description="人物录入: 默认新建; --edit 修改设定; --check 检查数据合理性",
    )
    # 流程选择 (互斥)
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--edit", "-e", action="store_true",
        help="修改流程: 对已有人物按一句要求检查并修改设定",
    )
    group.add_argument(
        "--check", "-c", action="store_true",
        help="检查流程: LLM 对照人物设定全面检查库中数据是否合理",
    )
    # 位置参数 (三种流程复用)
    parser.add_argument("target", nargs="?", help="新建: 骨架提示词 | 修改/检查: 人物 slug 或 persona_id")
    parser.add_argument("instruction", nargs="?", help="修改流程的第二参数: 修改要求")
    # 通用
    parser.add_argument("--interactive", "-i", action="store_true", help="交互式输入")
    parser.add_argument("--fix", action="store_true", help="配合 --check: 检查后自动修复高/中等问题")
    parser.add_argument("--model", default=None, help="LLM 模型 (默认取 VH_LLM_MODEL)")
    args = parser.parse_args(argv)

    from virtual_human.db import run_async

    try:
        if args.edit:
            # ---------- 修改流程: vh-ingest --edit <slug> "要求" ----------
            slug = args.target
            instruction = args.instruction
            if args.interactive:
                # 显式 -i: 逐项交互输入
                if not slug:
                    print("请输入人物 slug (vh-show 查看):")
                    slug = input("> ").strip()
                if not instruction:
                    print("请输入修改要求 (一行):")
                    instruction = input("> ").strip()
            if not slug:
                print("错误: 修改流程需要人物 slug。用法: vh-ingest --edit <slug> \"修改要求\"", file=sys.stderr)
                return 2
            if not instruction:
                print(f"错误: 修改流程需要修改要求。用法: vh-ingest --edit {slug} \"修改要求\"", file=sys.stderr)
                return 2
            return run_async(_ingest_edit_async(slug, instruction, args.model))

        if args.check:
            # ---------- 检查流程: vh-ingest --check <slug> [--fix] ----------
            slug = args.target
            if args.interactive and not slug:
                print("请输入人物 slug (vh-show 查看):")
                slug = input("> ").strip()
            if not slug:
                print("错误: 检查流程需要人物 slug。用法: vh-ingest --check <slug> [--fix]", file=sys.stderr)
                return 2
            return run_async(_ingest_check_async(slug, args.fix, args.model))

        # ---------- 新建流程 (默认): vh-ingest "骨架" ----------
        skeleton = args.target
        if args.interactive or not skeleton:
            if args.interactive or sys.stdin.isatty():
                print("请输入人物骨架提示词 (一行, 回车结束):")
                skeleton = input("> ").strip()
            if not skeleton:
                print("错误: 骨架提示词不能为空。用法: vh-ingest \"骨架描述\"", file=sys.stderr)
                return 2
        return run_async(_ingest_create_async(skeleton, args.model))
    except Exception as e:
        print(f"失败: {type(e).__name__}: {e}", file=sys.stderr)
        return 1


# ---------------------------------------------------------------------
# vh-show
# ---------------------------------------------------------------------
async def _show_async(slug: str | None, as_md: bool) -> int:
    from sqlalchemy import select

    from virtual_human.db import async_session_factory
    from virtual_human.kb.async_api import aget_kb_tree, arender_persona_md
    from virtual_human.models import Persona

    async with async_session_factory() as session:
        if slug:
            q = select(Persona).where((Persona.slug == slug) | (Persona.id == slug))
            personas = list((await session.execute(q)).scalars())
        else:
            personas = list((await session.execute(select(Persona))).scalars())

        if not personas:
            print("库中没有人物。先用 vh-ingest 生成一个。")
            return 0

        for p in personas:
            if as_md:
                print(await arender_persona_md(session, p.id))
            else:
                print(f"\n{p.name} (slug={p.slug}, id={p.id}, status={p.status})")
                print((await aget_kb_tree(session, p.id)).render())
    return 0


def show(argv: list[str] | None = None) -> int:
    force_utf8_stdio()

    parser = argparse.ArgumentParser(prog="vh-show", description="查看库中人物与知识树")
    parser.add_argument("slug", nargs="?", help="人物 slug (缺省列出全部)")
    parser.add_argument("--md", action="store_true", help="输出 Markdown 人物简介")
    args = parser.parse_args(argv)

    from virtual_human.db import run_async

    return run_async(_show_async(args.slug, args.md))


# ---------------------------------------------------------------------
# vh-chat
# ---------------------------------------------------------------------
async def _chat_async(slug: str, one_shot: str | None, quiet: bool = False) -> int:
    from sqlalchemy import select

    from vh_simulate import PersonaAgent
    from virtual_human.db import async_session_factory
    from virtual_human.models import Persona

    async with async_session_factory() as session:
        q = select(Persona).where((Persona.slug == slug) | (Persona.id == slug))
        persona = (await session.execute(q)).scalars().first()
        if persona is None:
            print(f"错误: 找不到人物 '{slug}' (可用 vh-show 查看全部)", file=sys.stderr)
            return 2

    agent = await PersonaAgent.create(persona.id)

    if not quiet:
        # 渐进加载事件 → 终端可视化 (浅色箭头行, 与正文区分)
        def _on_event(ev: dict) -> None:
            if ev["event"] == "recall":
                print(f"  ⚙ 想起 {len(ev['blocks'])} 段记忆 (预算 {ev['used']}/{ev['budget']} tokens):")
                for b in ev["blocks"]:
                    mark = "·" if b["source"] == "summary" else "★"
                    label = f"({b['label']})" if b.get("label") else ""
                    print(f"    {mark} [{b['source']:<7}] score={b['score']:.3f} {b['tokens']:>4}t {b['title']}{label}")
        agent.on_event = _on_event

    print(f"与【{agent.name}】对话 (exit 退出)")

    async def _ask(user_input: str) -> None:
        print(f"\n你: {user_input}")
        # 先显示"想起过程" (stream() 内部先执行检索, 事件回调先于正文 chunk 触发)
        try:
            first = True
            async for chunk in agent.stream(user_input):
                if first:
                    print(f"{agent.name}: ", end="", flush=True)   # 记忆装载完, 正文开始
                    first = False
                print(chunk, end="", flush=True)
            print()
        except Exception as e:
            print(f"\n(调用失败: {type(e).__name__}, 检查 OPENAI_API_KEY/OPENAI_BASE_URL)", file=sys.stderr)

    if one_shot:
        await _ask(one_shot)
        return 0

    loop = __import__("asyncio").get_event_loop()
    while True:
        try:
            user_input = (await loop.run_in_executor(None, lambda: input("\n你: "))).strip()
        except (EOFError, KeyboardInterrupt):
            print("\n再见")
            return 0
        if user_input.lower() in ("exit", "quit", "q"):
            print("再见")
            return 0
        if not user_input:
            continue
        await _ask(user_input)


def chat(argv: list[str] | None = None) -> int:
    force_utf8_stdio()

    parser = argparse.ArgumentParser(prog="vh-chat", description="与库中人物对话 (异步流式)")
    parser.add_argument("slug", help="人物 slug 或 persona_id")
    parser.add_argument("message", nargs="?", help="问题 (缺省进入交互循环)")
    parser.add_argument("--quiet", "-q", action="store_true", help="不打印记忆加载过程")
    args = parser.parse_args(argv)

    from virtual_human.db import run_async

    return run_async(_chat_async(args.slug, args.message, args.quiet))


# ---------------------------------------------------------------------
# vh-edit: 修改人物设定
# ---------------------------------------------------------------------
async def _edit_async(slug: str, instruction: str) -> int:
    from sqlalchemy import select

    from vh_generate.generate import edit_persona_async
    from virtual_human.db import async_session_factory
    from virtual_human.models import Persona

    async with async_session_factory() as session:
        q = select(Persona).where((Persona.slug == slug) | (Persona.id == slug))
        persona = (await session.execute(q)).scalars().first()
        if persona is None:
            print(f"错误: 找不到人物 '{slug}' (可用 vh-show 查看全部)", file=sys.stderr)
            return 2

    print(f"[编辑] {persona.name} (slug={persona.slug})")
    print(f"[要求] {instruction}\n")

    def _log(msg: str) -> None:
        print(msg, flush=True)

    def _chunk(text: str) -> None:
        print(text, end="", flush=True)

    ok = await edit_persona_async(persona.id, instruction, on_log=_log, on_chunk=_chunk)
    print(f"\n[{'完成 ✓' if ok else '警告: 轮数用尽, 修改可能不完整'}]")
    return 0 if ok else 1


def edit(argv: list[str] | None = None) -> int:
    force_utf8_stdio()

    parser = argparse.ArgumentParser(
        prog="vh-edit",
        description="输入一句修改要求, LLM 检查人物设定并调工具修改 (流式显示过程)",
    )
    parser.add_argument("slug", help="人物 slug 或 persona_id")
    parser.add_argument("instruction", nargs="?", help="修改要求 (缺省交互输入)")
    parser.add_argument("--interactive", "-i", action="store_true", help="交互式输入要求")
    args = parser.parse_args(argv)

    instruction = args.instruction
    if args.interactive or not instruction:
        print("请输入修改要求 (一行, 回车结束):")
        instruction = input("> ").strip()
        if not instruction:
            print("错误: 修改要求不能为空", file=sys.stderr)
            return 2

    from virtual_human.db import run_async

    try:
        return run_async(_edit_async(args.slug, instruction))
    except Exception as e:
        print(f"编辑失败: {type(e).__name__}: {e}", file=sys.stderr)
        return 1


# ---------------------------------------------------------------------
# vh-demo: 渐进式加载全过程演绎
# ---------------------------------------------------------------------
async def _demo_async(slug: str, question: str | None, talk: bool) -> int:
    from sqlalchemy import select

    from virtual_human.db import async_session_factory
    from virtual_human.kb.async_api import (
        aget_kb_tree,
        aget_node,
        aget_node_clues,
        aretrieve_context,
        arender_finance,
        arender_persona_md,
    )
    from virtual_human.models import KbNode as KbNodeModel, Persona

    async with async_session_factory() as session:
        q = select(Persona).where((Persona.slug == slug) | (Persona.id == slug))
        persona = (await session.execute(q)).scalars().first()
        if persona is None:
            print(f"错误: 找不到人物 '{slug}' (vh-show 查看)")
            return 2
        pid = persona.id

        # ---------- L0: 常驻身份 (每次会话必进 system prompt) ----------
        _fmt_head("L0 常驻身份 (会话开始即加载, 极小)")
        l0 = persona.core_identity or ""
        print(f"  [{len(l0)} 字符] {l0[:80]}...")

        # ---------- L1: 人生骨架 (全部节点 title+summary, 不碰正文) ----------
        _fmt_head("L1 人生骨架 (全部节点摘要, 不读正文)")
        tree = await aget_kb_tree(session, pid)
        print(tree.render())
        n_nodes = len([t for t in tree.walk() if t.node_type != "root"])
        print(f"  ↑ {n_nodes} 个节点只带了 summary, 正文一个字没读")

        # ---------- L2: 单节点渐进加载 (level=1 摘要 → level=2 全文) ----------
        # 树上的 slug 是 persona_id/node_type 复合标识; aget_node 需要节点的原始 slug,
        # 通过 node_id 反查一次 (也演示了"从树定位到节点"的常规路径)
        from sqlalchemy import select as _sel

        first = next(t for t in tree.walk() if t.node_type != "root")
        node_row = (
            await session.execute(_sel(KbNodeModel).where(KbNodeModel.id == first.node_id))
        ).scalar_one()
        raw_slug = node_row.slug

        _fmt_head(f"L2 渐进加载节点 (原始 slug={raw_slug}, 树标识={first.slug})")
        v1 = await aget_node(session, pid, raw_slug, load_level=1)
        print(f"  [level=1] {v1['title']}: {v1['summary']}")
        print(f"            (只有摘要, tokens≈{len(v1['summary'] or '') // 2})")
        v2 = await aget_node(session, pid, raw_slug, load_level=2)
        if v2.get("contents"):
            c = v2["contents"][0]
            print(f"  [level=2] ({len(v2['contents'])} 块全文, 第一块 {c['token_estimate']} tokens)")
            print(f"            {c['content'][:60]}...")

        # ---------- 联想线索: kb_edges ----------
        clues = await aget_node_clues(session, pid, raw_slug)
        if clues:
            _fmt_head("联想线索 (kb_edges, 看完线索可决定深挖哪个节点)")
            for c in clues:
                mark = {"related": "相关", "causes": "引发", "contradicts": "矛盾",
                        "elaborates": "展开", "part_of": "属于"}.get(c["edge_type"], c["edge_type"])
                arrow = "→" if c["direction"] == "outgoing" else "←"
                print(f"  {arrow} {mark} {c['neighbor_title']} (w={c['weight']:.2f})")

        # ---------- L3: 检索装载 (预算内想起) ----------
        q_text = question or "人生经历"
        _fmt_head(f"L3 检索装载 (query={q_text!r}, 按 load_score 排序 + token 预算)")
        ctx = await aretrieve_context(session, persona.slug, q_text)
        for b in ctx.blocks:
            mark = "★" if b.source != "summary" else " "
            print(f" {mark} score={b.score:.3f} tokens={b.token_estimate:>4} [{b.source:<7}] {b.title}")
        print(f"  预算 {ctx.budget} / 已用 {ctx.used_tokens} (装满即停 = 渐进式)")

        # ---------- 账本 ----------
        _fmt_head("财务账本 (精确数字, 不走向量)")
        print(await arender_finance(session, persona.slug))

        if not talk:
            print("\n以上 = Agent 会话的一次完整记忆装载过程。")
            print("加 --talk 可继续让人物回答问题 (每轮重复 L3 动态检索)。")
            return 0

    # ---------- 对话: 上述装载如何被用起来 ----------
    _fmt_head("对话 (每轮动态执行 L3 检索, 流式回答)")
    from vh_simulate import PersonaAgent

    agent = await PersonaAgent.create(pid)
    print(f"[{agent.name}] ", end="", flush=True)
    try:
        async for chunk in agent.stream(q_text):
            print(chunk, end="", flush=True)
        print()
    except Exception as e:
        print(f"\n(LLM 未配置或失败: {type(e).__name__})")
    return 0


def demo(argv: list[str] | None = None) -> int:
    force_utf8_stdio()

    parser = argparse.ArgumentParser(
        prog="vh-demo",
        description="演绎渐进式加载全过程: L0身份 → L1骨架 → L2节点加载 → 联想 → L3检索 → 对话",
    )
    parser.add_argument("slug", help="人物 slug 或 persona_id")
    parser.add_argument("question", nargs="?", default=None, help="演示用问题 (缺省 '人生经历')")
    parser.add_argument("--talk", action="store_true", help="装载演示后继续流式对话")
    args = parser.parse_args(argv)

    from virtual_human.db import run_async

    return run_async(_demo_async(args.slug, args.question, args.talk))


def main() -> int:
    force_utf8_stdio()
    print("可用命令: vh-ingest / vh-show / vh-chat / vh-edit / vh-demo")
    return 0


if __name__ == "__main__":
    sys.exit(main())
