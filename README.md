# virtual-human 工作区

「模拟人类」uv workspace：**知识库 + 生成模块** 两个子包，单仓管理。

```
G:\sunwu\virtual_human\          ← 工作区根 (pyproject 含 [tool.uv.workspace])
├─ src\
│  ├─ virtual_human\             ← 子包 1: virtual-human (知识库)
│  ├─ vh_generate\               ← 子包 2: vh-generate (生成)
│  ├─ vh_simulate\               ← 子包 3: vh-simulate (人物模拟)
│  └─ vh_cli\                    ← 子包 4: vh-cli (命令行, UTF-8 输出)
├─ migrations\                   ← alembic (知识库 schema)
├─ sql\                          ← 建表 DDL
├─ scripts\                      ← 演示与自检脚本
├─ docs\                         ← ER 设计文档
└─ tests\                        ← 测试 (pytest)
```

## 依赖关系

```
vh-cli ──▶ vh-simulate ──▶ vh-generate ──▶ virtual-human   (workspace source)
               │               │
               │               └─ langchain / langchain-openai / langchain-core
               └─ (PersonaAgent 用 generate.make_llm 与 kb 检索 API)
```

## CLI

工作区安装后（`uv sync`）获得三个命令，全部强制 UTF-8 终端输出（Windows GBK 控制台不乱码）：

```powershell
# 骨架提示词 → LLM 生成人设 → 写入知识库 → 终端展示全过程
uv run vh-ingest "李慧兰, 58岁, 退休小学数学老师, 丈夫是货车司机, 2010年县城买房..."
uv run vh-ingest -i                # 交互式输入骨架

# 查看库中人物与知识树 (slug 或 id 均可定位)
uv run vh-show
uv run vh-show --md                # Markdown 人物简介 (summary 带 <slug> 标签)

# 与人物对话 (交互 REPL 或单问)
uv run vh-chat chen-zhiqiang "您还记得买房那年的事吗?"
```

## 快速开始

```powershell
uv sync                                        # 一键装好两个子包
uv run python scripts/db_check.py              # 连库自检
uv run python scripts/demo_ingest.py           # 骨架描述 → 生成人设 → 灌库
uv run python scripts/demo_chat.py --no-llm    # 检索链路
uv run python scripts/test_simulate.py         # 模拟模块 (persona_id 初始化)
```

生成类脚本要真实 LLM 时配 `OPENAI_API_KEY`（可选 `OPENAI_BASE_URL` 切国产模型）。

## 两个子包的职责

| 子包 | 职责 | 关键 API |
| --- | --- | --- |
| `virtual-human` | ORM、渐进式检索、账本 | `get_node` / `get_node_clues` / `get_kb_tree` / `retrieve_context` / `render_persona_md` |
| `vh-generate` | LLM 对话入口、种子灌库 | `ingest_persona` / `chat` |
| `vh-simulate` | 人物模拟 agent | **`PersonaAgent.init(persona_id)`** / `say` / `stream` |

## 环境变量（.env，工作区根）

| 变量 | 用途 | 缺省行为 |
| --- | --- | --- |
| `PGHOST/PGPORT/PGUSER/PGPASSWORD/PGDATABASE` | 数据库连接 | — |
| `OPENAI_API_KEY` | LLM 与嵌入凭证 | 留空 → generate 走 mock、嵌入走伪向量；chat/simulate 报缺凭证 |
| `OPENAI_BASE_URL` | OpenAI 兼容端点（可切国产模型） | 留空走 OpenAI 官方 |
| `VH_LLM_MODEL` | 对话模型 | `gpt-4o-mini` |
| `VH_EMBED_MODEL` | 嵌入模型 | `text-embedding-3-small` |
| `RUSTFS_ENDPOINT` | RustFS (S3 兼容) endpoint | 留空 → 不挂载 agent 文档工具 |
| `RUSTFS_ACCESS_KEY` / `RUSTFS_SECRET_KEY` | RustFS 凭证 | — |
| `RUSTFS_BUCKET` | agent 文档 bucket | `vh-docs` |

全部通过 `virtual_human.config.settings` 访问（pydantic-settings + dotenv），代码里不直接 `os.environ`。

## Agent 知识文档（RustFS 对象存储）

模拟对话的 agent 可以读写自己的文档目录：bucket 内 key 约定 `{persona_id}/docs/{path}`（persona_id 即 agent_id）。

启动 RustFS（podman / docker 均可）：

```bash
podman run -d --name rustfs -p 9000:9000 -p 9001:9001 \
  -e RUSTFS_ACCESS_KEY=rustfsadmin -e RUSTFS_SECRET_KEY=rustfsadmin \
  -v rustfs-data:/data rustfs/rustfs:latest
```

- S3 endpoint：`http://127.0.0.1:9000`（代码读写走 boto3 path-style）
- 控制台：`http://127.0.0.1:9001`（默认 `rustfsadmin/rustfsadmin`）

使用方式：

1. **模拟厅页面**「知识文档」抽屉：上传 / 预览（docx 自动抽正文）/ 下载 / 删除；同名上传即覆盖
2. **对话工具**：RustFS 配置就绪后自动绑定 `ListDocsArgs` / `ReadDocArgs` / `WriteDocArgs`。
   读 → 修改 → `WriteDocArgs` 覆盖写回（docx 重建段落，格式会简化；建议改写成 `.md`）
3. **本地导入**：`uv run python scripts/import_doc.py <persona_id> "D:\path\问卷.docx"`
4. **HTTP API**：`GET/PUT/DELETE /api/personas/{pid}/docs`（PUT 为原始字节 body，`?path=` 指定文件名）

启动自检里有 `storage` 一项，`/api/health` 可随时查看。

## 异步 (aio) 约定

**LLM 与数据库都是异步流式优先**：

- **LLM**：`make_llm()` 固定 `streaming=True`。推荐路径 `await agent.asay(...)`（聚合）或 `async for chunk in agent.stream(...)`（打字机）；同步 `say()`/`chat()` 内部也走流聚合，只是给不便 asyncio 的场景兜底
- **DB**：`virtual_human.db` 提供 `async_session_factory`（psycopg3 async）。异步上下文里一律 `async with async_session_factory() as session: await session.execute(...)`
- **Windows 坑**：默认 `ProactorEventLoop` 不支持 psycopg async，必须用 Selector。统一入口：

```python
from virtual_human.db import run_async
run_async(main())   # 内部自动选 SelectorEventLoop
```

异步全链路验证脚本：`uv run python scripts/test_async_pipeline.py "问题" slug`

## schema 变更

1. 改 `src/virtual_human/src/virtual_human/models.py`（ORM 是唯一事实来源）
2. `uv run alembic revision --autogenerate -m "..."` → `uv run alembic upgrade head`
3. 同步 `sql/schema.sql`（全新建库用）

约定：ID 为 varchar(36) UUID；向量维度 1024；金额 numeric(14,2) 恒为正；配置统一走 `virtual_human.config.settings`，不直接 `os.environ`。
