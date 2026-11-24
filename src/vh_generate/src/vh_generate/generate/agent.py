"""生成模块 (aio): LangChain 异步流式 + virtual_human 异步检索 API.

全部为 Python async API:
    - LLM 始终流式 (make_llm streaming=True), 主路径 astream_chat / achat
    - DB 一律 async_session_factory (psycopg async)
    - 入口统一 run_async() (Windows 下自动 SelectorEventLoop)

用法:
    from virtual_human.db import run_async
    from vh_generate.generate.agent import achat, astream_chat

    async def main():
        answer = await achat("chen-zhiqiang", "你还记得买房那年的事吗?")
        async for chunk in astream_chat("chen-zhiqiang", "后来呢?"):
            print(chunk, end="")

    run_async(main())
"""

from __future__ import annotations

import logging
import os
from typing import AsyncIterator

from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_openai import ChatOpenAI

from virtual_human.config import settings
from virtual_human.db import async_session_factory
from virtual_human.kb.async_api import achat_context

__all__ = ["make_llm", "achat", "astream_chat", "llm_logger"]

# 服务器控制台可见的 LLM 运行日志 (排查 bug 用)
llm_logger = logging.getLogger("vh.llm")
if not llm_logger.handlers:
    llm_logger.setLevel(logging.INFO)


# ---------------------------------------------------------------------
# LLM 工厂 (异步专用, 始终流式)
# ---------------------------------------------------------------------
def make_llm(model: str | None = None) -> ChatOpenAI:
    """异步流式 LLM 实例. 模型/端点/凭证从 .env (VH_LLM_MODEL/OPENAI_BASE_URL/OPENAI_API_KEY)."""
    m = model or settings.llm_model
    llm_logger.info(
        "[LLM] init model=%s base_url=%s key=%s",
        m,
        settings.openai_base_url or "https://api.openai.com/v1",
        (settings.openai_api_key[:6] + "***") if settings.openai_api_key else "(empty)",
    )
    return ChatOpenAI(
        model=m,
        streaming=True,
        api_key=settings.openai_api_key,
        base_url=settings.openai_base_url or None,
        temperature=0.8,
    )


def _prompt() -> ChatPromptTemplate:
    return ChatPromptTemplate.from_messages([
        ("system", "{system_prompt}"),
        MessagesPlaceholder("history", optional=True),
        ("human", "{input}"),
    ])


async def _astream_chunks(
    system_prompt: str,
    user_input: str,
    history: list | None,
    model: str | None,
) -> AsyncIterator[str]:
    """内部: 组 chain 并异步流式产出文本块."""
    chain = _prompt() | make_llm(model) | StrOutputParser()
    async for chunk in chain.astream({
        "system_prompt": system_prompt,
        "history": history or [],
        "input": user_input,
    }):
        yield chunk


# ---------------------------------------------------------------------
# 对话入口 (纯 async)
# ---------------------------------------------------------------------
async def achat(
    persona_slug: str, user_input: str, history: list | None = None, model: str | None = None
) -> str:
    """异步单轮: 异步取数 (L0+检索+账本) → 流式生成 → 聚合返回."""
    async with async_session_factory() as session:
        system_prompt = await achat_context(session, persona_slug, user_input)

    chunks: list[str] = []
    async for chunk in _astream_chunks(system_prompt, user_input, history, model):
        chunks.append(chunk)
    return "".join(chunks)


async def astream_chat(
    persona_slug: str, user_input: str, history: list | None = None, model: str | None = None
) -> AsyncIterator[str]:
    """异步流式 (打字机) —— 推荐路径."""
    async with async_session_factory() as session:
        system_prompt = await achat_context(session, persona_slug, user_input)

    async for chunk in _astream_chunks(system_prompt, user_input, history, model):
        yield chunk
