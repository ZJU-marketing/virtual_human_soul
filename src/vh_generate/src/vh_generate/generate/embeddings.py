"""嵌入服务 (可选) —— 生成 kb_embeddings 向量.

默认不启用 (VH_EMBED_ENABLED=false). 启用后:
    - 本模块在导入时向 virtual_human.kb.retrieval.register_embedder 注册查询向量化函数
    - 依赖方向保持单向: vh_generate → virtual_human (知识库不反向 import 生成模块)

写库向量化: embed_texts()  (seeder 调用)
查询向量化: 通过 register_embedder 注入到知识库检索层
"""

from __future__ import annotations

from virtual_human.config import settings

VECTOR_DIM = 1024   # 与 kb_embeddings.embedding 的 vector(1024) 一致


def embed_enabled() -> bool:
    """词嵌入是否启用 (VH_EMBED_ENABLED)."""
    return settings.embed_enabled


def _query_embedder(query: str, model: str):
    """注册给知识库检索层的查询向量化函数 (签名固定: (query, model) -> vec | None)."""
    vecs = embed_texts([query], model=model)
    return vecs[0] if vecs else None


def register() -> None:
    """向知识库层注册嵌入函数 (嵌入启用时调用; vh_generate.generate.__init__ 导入时自动执行)."""
    if settings.embed_enabled:
        from virtual_human.kb.retrieval import register_embedder

        register_embedder(_query_embedder)


def embed_texts(texts: list[str], model: str | None = None) -> list[list[float]] | None:
    """批量向量化; 未启用嵌入时返回 None (调用方据此跳过向量写入).

    启用但调用失败时也返回 None —— 嵌入是增强项, 失败不阻塞主流程, 不打警告.
    """
    if not embed_enabled() or not settings.has_llm:
        return None

    try:
        resp = _get_client().embeddings.create(model=model or settings.embed_model, input=texts)
    except Exception:
        return None

    out = []
    for item in resp.data:
        vec = item.embedding
        if len(vec) > VECTOR_DIM:
            vec = vec[:VECTOR_DIM]
        elif len(vec) < VECTOR_DIM:
            vec = vec + [0.0] * (VECTOR_DIM - len(vec))
        out.append(vec)
    return out


_client = None


def _get_client():
    global _client
    if _client is None:
        from openai import OpenAI

        _client = OpenAI(
            api_key=settings.openai_api_key,
            base_url=settings.openai_base_url or None,
        )
    return _client


def embed_query(text: str, model: str | None = None) -> list[float] | None:
    """查询向量化; 未启用时 None (检索走非向量路径)."""
    vecs = embed_texts([text], model=model)
    return vecs[0] if vecs else None
