"""文档中间件 (LangChain tool 中间件) —— 让模拟 agent 读写自己 docs 目录里的文档.

挂载方式 (RustFS 配置就绪时自动生效):
    PersonaAgent.stream_tools:
        llm.bind_tools(TOOL_MODELS + DOC_TOOL_MODELS)
        system prompt 追加 DOC_TOOL_GUIDE
    PersonaAgent._run_tool:
        首行按 is_doc_tool(name) 分发到 run_doc_tool()

对象存储目录: {persona_id}/docs/{path}   (persona_id 即 agent_id)
读写语义: 读进内存 (docx 抽正文) -> agent 修改 -> 覆盖写回同一 key (put_object)
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from virtual_human.config import settings

DOC_TOOL_GUIDE = """

## 文档工具 (本人 docs 目录, 只可访问自己的文档)
- ListDocsArgs: 列出本人文档目录 (可传子目录 path)
- ReadDocArgs: 读取一篇文档正文 (docx 自动抽取文字; md/txt 直接读)
- WriteDocArgs: 把修改后的「完整全文」覆盖写回同名文档 (同名即覆盖; create=true 可新建)

查资料/问卷/笔记时先 ListDocsArgs 再 ReadDocArgs;
需要修改文档时, 先 ReadDocArgs 读进内存, 修改后把完整新内容放进 content 调用 WriteDocArgs 覆盖写。
不要只写片段——content 必须是修改后的全文。"""


class ListDocsArgs(BaseModel):
    """列出本人 docs 目录下的文档."""

    path: str = Field(default="", description="子目录相对路径, 留空 = 整个 docs 目录")


class ReadDocArgs(BaseModel):
    """读取本人 docs 中一篇文档的正文."""

    path: str = Field(description="文档相对路径, 如 问卷.docx 或 资料/笔记.md")


class WriteDocArgs(BaseModel):
    """把修改后的全文覆盖写回本人 docs 中的文档 (同名覆盖)."""

    path: str = Field(description="文档相对路径 (与读取时一致即覆盖原文件)")
    content: str = Field(description="修改后的完整文本内容 (不是片段)")
    create: bool = Field(default=False, description="true = 允许新建; false = 仅覆盖已存在文档")


DOC_TOOL_MODELS = [ListDocsArgs, ReadDocArgs, WriteDocArgs]

_DOC_TOOL_NAMES = {
    "ListDocsArgs", "list_docs",
    "ReadDocArgs", "read_doc",
    "WriteDocArgs", "write_doc",
}

MAX_READ_CHARS = 12000  # 单次读入正文上限 (防止一次性塞爆上下文)


def docs_enabled() -> bool:
    """对象存储配置就绪才挂载文档工具."""
    return settings.object_storage_enabled


def is_doc_tool(name: str) -> bool:
    return name in _DOC_TOOL_NAMES


async def _exists(persona_id: str, rel: str) -> bool:
    from virtual_human import storage

    try:
        await storage.aread_doc(persona_id, rel)
        return True
    except Exception:  # noqa: BLE001
        return False


async def run_doc_tool(name: str, args: dict, persona_id: str) -> dict:
    """执行一个文档工具, 返回 {text, blocks, summary} (与回忆工具同构)."""
    from virtual_human import storage

    path = str(args.get("path") or "").strip()

    if name in ("ListDocsArgs", "list_docs"):
        try:
            metas = await storage.alist_docs(persona_id, path)
        except Exception as e:  # noqa: BLE001
            return {"text": f"文档列表获取失败: {e}", "blocks": [], "summary": "读取失败"}
        if not metas:
            return {
                "text": "(docs 目录为空; 需要新建文档时用 WriteDocArgs create=true)",
                "blocks": [],
                "summary": "0 篇",
            }
        lines = [f"- {m.path} ({m.size} bytes, {m.last_modified[:19]})" for m in metas]
        return {
            "text": "本人 docs 目录文档:\n" + "\n".join(lines),
            "blocks": [
                {"source": "doc", "title": m.path, "label": None, "score": 1.0,
                 "tokens": m.size // 2}
                for m in metas
            ],
            "summary": f"{len(metas)} 篇文档",
        }

    if name in ("ReadDocArgs", "read_doc"):
        try:
            data = await storage.aread_doc(persona_id, path)
            text = storage.extract_text(path, data)
        except Exception as e:  # noqa: BLE001
            return {"text": f"读取文档失败: {e}", "blocks": [], "summary": "读取失败"}
        shown = text[:MAX_READ_CHARS] + ("\n…(已截断)" if len(text) > MAX_READ_CHARS else "")
        return {
            "text": f"[文档] {path}\n{shown}",
            "blocks": [
                {"source": "doc", "title": path, "label": None, "score": 1.0,
                 "tokens": len(text) // 2}
            ],
            "summary": f"{len(text)} 字",
        }

    if name in ("WriteDocArgs", "write_doc"):
        content = str(args.get("content") or "")
        if not content:
            return {"text": "content 为空, 未写入", "blocks": [], "summary": "参数为空"}
        if not args.get("create") and not await _exists(persona_id, path):
            return {
                "text": f"文档不存在: {path} (新建请传 create=true)",
                "blocks": [],
                "summary": "未找到文档",
            }
        try:
            data = storage.build_text_bytes(path, content)
            await storage.awrite_doc(persona_id, path, data, storage.guess_content_type(path))
        except Exception as e:  # noqa: BLE001
            return {"text": f"写入文档失败: {e}", "blocks": [], "summary": "写入失败"}
        return {
            "text": f"已覆盖写入: {path} ({len(content)} 字)",
            "blocks": [
                {"source": "doc", "title": path, "label": None, "score": 1.0,
                 "tokens": len(content) // 2}
            ],
            "summary": f"已保存 {len(content)} 字",
        }

    return {"text": f"未知文档工具: {name}", "blocks": [], "summary": "未知工具"}
