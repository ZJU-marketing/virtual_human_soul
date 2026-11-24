"""对象存储 (RustFS / S3 兼容): agent 知识文档读写.

目录约定 (bucket 内的 key):
    {persona_id}/docs/{相对路径}      # 每个 agent 一个 docs 目录

读写语义 (读进内存 -> 修改 -> 覆盖):
    - 读: 对象字节进内存, 按扩展名抽文本 (.docx 用 python-docx; .md/.txt 等直接解码)
    - 写: 文本修改后覆盖写回同名 key (.docx 用 python-docx 重建段落; 文本类按 UTF-8)

所有 boto3 调用走 asyncio.to_thread, 不阻塞事件循环.
未配置 (RUSTFS_ENDPOINT / RUSTFS_ACCESS_KEY) 时抛 RuntimeError, 由上层转 503 / 关闭工具.
"""

from __future__ import annotations

import asyncio
import io
import logging
import threading
from dataclasses import dataclass
from datetime import datetime

from virtual_human.config import settings

log = logging.getLogger("vh.storage")

TEXT_EXTS = {".md", ".txt", ".csv", ".json", ".yaml", ".yml", ".log", ".py", ".html"}
DOCX_EXT = ".docx"

_client = None
_lock = threading.Lock()


def _s3():
    """惰性创建 boto3 client (进程内单例; path-style 适配 RustFS)."""
    global _client
    if not settings.object_storage_enabled:
        raise RuntimeError(
            "对象存储未配置: 需要 RUSTFS_ENDPOINT / RUSTFS_ACCESS_KEY / RUSTFS_SECRET_KEY"
        )
    with _lock:
        if _client is None:
            import boto3
            from botocore.config import Config

            _client = boto3.client(
                "s3",
                endpoint_url=settings.rustfs_endpoint,
                aws_access_key_id=settings.rustfs_access_key,
                aws_secret_access_key=settings.rustfs_secret_key,
                region_name=settings.rustfs_region,
                config=Config(
                    signature_version="s3v4",
                    s3={"addressing_style": "path"},
                    connect_timeout=5,
                    read_timeout=30,
                    retries={"max_attempts": 2},
                ),
            )
            log.info("对象存储 client 已创建: %s (bucket=%s)", settings.rustfs_endpoint, settings.rustfs_bucket)
    return _client


def _bucket() -> str:
    return settings.rustfs_bucket


def normalize_rel(path: str) -> str:
    """规范化 docs 内相对路径: 去前导斜杠/空白, 拒绝 .. 穿越 (只保留段名)."""
    p = str(path or "").replace("\\", "/").strip().lstrip("/")
    if p.startswith("docs/"):
        p = p[len("docs/"):]
    parts = [x for x in p.split("/") if x and x not in (".", "..")]
    if not parts:
        raise ValueError("文档路径不能为空")
    return "/".join(parts)


def doc_key(persona_id: str, rel: str) -> str:
    """bucket 内 key: {persona_id}/docs/{rel}."""
    return f"{persona_id}/docs/{normalize_rel(rel)}"


def docs_prefix(persona_id: str, rel: str = "") -> str:
    base = f"{persona_id}/docs/"
    return base + (normalize_rel(rel) if rel else "")


@dataclass
class DocMeta:
    """文档元信息 (docs 内相对路径)."""

    path: str
    size: int
    last_modified: str
    content_type: str = ""


def _meta_from_obj(persona_id: str, obj: dict) -> DocMeta:
    key = obj["Key"]
    rel = key.split("/docs/", 1)[-1]
    lm = obj.get("LastModified")
    return DocMeta(
        path=rel,
        size=int(obj.get("Size") or 0),
        last_modified=lm.isoformat() if isinstance(lm, datetime) else str(lm or ""),
        content_type=obj.get("ContentType") or "",
    )


def ensure_bucket_sync() -> None:
    """确保 bucket 存在 (幂等)."""
    client = _s3()
    try:
        client.head_bucket(Bucket=_bucket())
        return
    except Exception:
        pass
    try:
        client.create_bucket(Bucket=_bucket())
        log.info("已创建对象存储 bucket: %s", _bucket())
    except Exception as e:  # 并发创建时可能 409
        log.warning("create_bucket 失败 (可能已存在): %s", e)


async def aensure_bucket() -> None:
    await asyncio.to_thread(ensure_bucket_sync)


async def alist_docs(persona_id: str, prefix: str = "") -> list[DocMeta]:
    """列出 {persona_id}/docs 下的文档 (可按子目录过滤)."""

    def _run() -> list[DocMeta]:
        client = _s3()
        out: list[DocMeta] = []
        paginator = client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=_bucket(), Prefix=docs_prefix(persona_id, prefix)):
            for obj in page.get("Contents") or []:
                if obj["Key"].endswith("/"):
                    continue
                out.append(_meta_from_obj(persona_id, obj))
        return out

    return await asyncio.to_thread(_run)


async def aread_doc(persona_id: str, rel: str) -> bytes:
    """读取原始字节 (不存在时抛 botocore ClientError)."""

    def _run() -> bytes:
        r = _s3().get_object(Bucket=_bucket(), Key=doc_key(persona_id, rel))
        return r["Body"].read()

    return await asyncio.to_thread(_run)


async def awrite_doc(persona_id: str, rel: str, data: bytes, content_type: str = "") -> str:
    """覆盖写入 (同名 key 直接覆盖), 返回规范化相对路径."""

    def _run() -> str:
        extra = {"ContentType": content_type} if content_type else {}
        _s3().put_object(Bucket=_bucket(), Key=doc_key(persona_id, rel), Body=data, **extra)
        return normalize_rel(rel)

    rel2 = await asyncio.to_thread(_run)
    log.info("[storage] 写入 %s/%s (%d bytes)", persona_id, rel2, len(data))
    return rel2


async def adelete_doc(persona_id: str, rel: str) -> None:
    def _run() -> None:
        _s3().delete_object(Bucket=_bucket(), Key=doc_key(persona_id, rel))

    await asyncio.to_thread(_run)
    log.info("[storage] 删除 %s/%s", persona_id, rel)


# ---------------------------------------------------------------------
# 文本抽取 / 回写 (读进内存 -> 修改 -> 覆盖)
# ---------------------------------------------------------------------
def _ext(name: str) -> str:
    low = name.lower()
    return low[low.rfind("."):] if "." in low else ""


def extract_text(name: str, data: bytes) -> str:
    """对象字节 -> 纯文本. .docx 用 python-docx; 文本类直接解码; 其他抛 ValueError."""
    ext = _ext(name)
    if ext == DOCX_EXT:
        from docx import Document

        doc = Document(io.BytesIO(data))
        parts = [p.text for p in doc.paragraphs]
        for table in doc.tables:  # 表格内容也抽取
            for row in table.rows:
                parts.append("\t".join(cell.text for cell in row.cells))
        return "\n".join(parts).strip()
    if ext in TEXT_EXTS or ext == "":
        return data.decode("utf-8", errors="replace")
    raise ValueError(f"暂不支持读取该类型: {ext or '(无扩展名)'} (支持 .docx / .md / .txt 等文本)")


def build_text_bytes(name: str, text: str) -> bytes:
    """文本 -> 覆盖写回用的字节. .docx 重建段落 (格式会简化); 其他按 UTF-8."""
    ext = _ext(name)
    if ext == DOCX_EXT:
        from docx import Document

        doc = Document()
        for line in text.splitlines():
            doc.add_paragraph(line)
        buf = io.BytesIO()
        doc.save(buf)
        return buf.getvalue()
    return text.encode("utf-8")


def guess_content_type(name: str) -> str:
    ext = _ext(name)
    return {
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".md": "text/markdown; charset=utf-8",
        ".txt": "text/plain; charset=utf-8",
        ".csv": "text/csv; charset=utf-8",
        ".json": "application/json",
    }.get(ext, "application/octet-stream")
