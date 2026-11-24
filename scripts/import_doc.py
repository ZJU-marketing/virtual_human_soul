"""把本地文件导入某个 persona (agent) 的 docs 目录 (RustFS 对象存储).

用法:
    uv run python scripts/import_doc.py <persona_id> "D:\\path\\问卷.docx" [远端路径]

不传远端路径时用原文件名 (可含子目录, 如 资料/问卷.docx); 同名会覆盖.
未配置 RustFS 时先在 .env 设置 RUSTFS_ENDPOINT / RUSTFS_ACCESS_KEY.
"""

from __future__ import annotations

import sys

from virtual_human import storage
from virtual_human.config import settings
from virtual_human.db import run_async


async def _main(persona_id: str, local: str, remote: str | None) -> int:
    if not settings.object_storage_enabled:
        print("未配置对象存储: 请先在 .env 设置 RUSTFS_ENDPOINT / RUSTFS_ACCESS_KEY")
        return 2

    with open(local, "rb") as f:
        data = f.read()
    name = remote or local.replace("\\", "/").rsplit("/", 1)[-1]

    await storage.aensure_bucket()
    rel = await storage.awrite_doc(persona_id, name, data, storage.guess_content_type(name))
    print(f"已导入: {persona_id}/docs/{rel} ({len(data)} bytes)")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(__doc__)
        raise SystemExit(2)
    remote_arg = sys.argv[3] if len(sys.argv) > 3 else None
    raise SystemExit(run_async(_main(sys.argv[1], sys.argv[2], remote_arg)))
