"""vh_generate 自检实现: LLM / 嵌入配置.

实现 HealthCheck 接口, app 启动时注册执行.
"""

from __future__ import annotations

from virtual_human.config import settings
from virtual_human.healthcheck import HealthCheck


class LLMCheck(HealthCheck):
    """LLM 自检: 凭证与模型配置 (未配置属正常降级, 生成走 mock)."""

    name = "llm"

    async def check(self) -> str:
        if not settings.has_llm:
            return "未配置 OPENAI_API_KEY (生成走 mock)"
        base = settings.openai_base_url or "默认 endpoint"
        return f"model={settings.llm_model} base_url={base}"


class EmbeddingCheck(HealthCheck):
    """嵌入自检: 启用则校验凭证, 未启用直接跳过."""

    name = "embedding"

    async def check(self) -> str:
        if not settings.embed_enabled:
            return "未启用 (VH_EMBED_ENABLED=false, 检索不走向量召回)"
        if not settings.has_llm:
            raise ValueError("已启用嵌入但缺少 OPENAI_API_KEY")
        return f"model={settings.embed_model}"
