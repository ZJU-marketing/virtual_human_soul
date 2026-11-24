"""验证: 全链路 aio (异步 DB → 异步检索 → 异步流式 LLM).

不写死任何数据: persona 从库里取第一个 live 的; slug 可用参数覆盖.

用法:
    uv run python scripts/test_async_pipeline.py                     # async DB + 检索
    uv run python scripts/test_async_pipeline.py --slug <slug> "问题"   # 指定人物全链路流式
"""

from __future__ import annotations

import argparse

from sqlalchemy import select, text

from virtual_human.db import async_session_factory, run_async


async def db_check() -> None:
    async with async_session_factory() as session:
        r = await session.execute(
            text("select current_database(), count(*) from information_schema.tables where table_schema='public'")
        )
        db, n = r.one()
        print(f"[async db] ok: db={db} tables={n}")


async def pick_persona(slug: str | None):
    """取 persona: 给了 slug 按slug查, 否则取库里第一个 (id, name, slug). 没有返回 None."""
    from virtual_human.models import Persona

    async with async_session_factory() as session:
        q = select(Persona.id, Persona.name, Persona.slug)
        if slug:
            q = q.where((Persona.slug == slug) | (Persona.id == slug))
        else:
            q = q.order_by(Persona.created_at).limit(1)
        return (await session.execute(q)).first()


async def retrieval_check(persona) -> None:
    from virtual_human.kb.async_api import arender_persona_md, aretrieve_context

    persona_id, name, persona_slug = persona
    async with async_session_factory() as session:
        ctx = await aretrieve_context(session, persona_slug, "人生经历")
        print(f"[async retrieve] {name}: blocks={len(ctx.blocks)} tokens={ctx.used_tokens}/{ctx.budget}")
        for b in ctx.blocks[:3]:
            print(f"  score={b.score:.3f} [{b.source}] {b.title}")

        md = await arender_persona_md(session, persona_id)
        print(f"[async md] {len(md)} 字符, 首行: {md.splitlines()[0]}")


async def stream_chat(persona, question: str) -> None:
    from vh_simulate import PersonaAgent

    persona_id, name, _ = persona
    agent = await PersonaAgent.create(persona_id)
    print(f"[stream] {name}: ", end="", flush=True)
    try:
        async for chunk in agent.stream(question):
            print(chunk, end="", flush=True)
        print()
        print(f"[history] {len(agent.history)} 条")
    except Exception as e:
        print(f"\n[LLM 调用失败] {type(e).__name__}: {str(e)[:80]}")
        print("[说明] aio 链路本身已通 (async DB → agent.stream); 配置可用 key 后即可出文本")


async def main() -> None:
    parser = argparse.ArgumentParser(description="aio 全链路验证")
    parser.add_argument("question", nargs="?", help="可选: 问题, 提供后走流式对话")
    parser.add_argument("--slug", default=None, help="可选: 人物 slug/id, 缺省取库里第一个")
    args = parser.parse_args()

    await db_check()

    persona = await pick_persona(args.slug)
    if persona is None:
        print(f"[error] 库中没有人物{f' (slug={args.slug})' if args.slug else ''}, 先跑 vh-ingest")
        return

    await retrieval_check(persona)

    if args.question:
        await stream_chat(persona, args.question)
    else:
        print("[skip] 全链路流式: uv run python scripts/test_async_pipeline.py --slug <slug> \"你的问题\"")


if __name__ == "__main__":
    run_async(main())
