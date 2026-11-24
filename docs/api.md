# 模块 API 文档

> 服务端/前端集成的 Python 模块 API（非 CLI）。所有异步入口需在 `run_async()` / asyncio 环境中调用。
> 工作区：`G:\sunwu\virtual_human`，依赖方向：`vh_api → vh_cli → vh_simulate → vh_generate → virtual_human`

## 依赖图

```
vh_api (FastAPI)
 ├── vh_generate.generate   生成/检查/编辑 (LLM 工具调用)
 ├── vh_simulate            模拟对话 (PersonaAgent, 上下文压缩)
 ├── vh_cli.io_setup        UTF-8 终端 (仅 CLI 用)
 └── virtual_human          知识库 (检索/ORM/账本)
       └── vh_generate      (嵌入可选注入: VH_EMBED_ENABLED)
```

---

## 1. virtual_human —— 知识库

### 配置（`virtual_human.config`）

```python
from virtual_human.config import settings
settings.pg_dsn                 # psycopg DSN
settings.sqlalchemy_url         # 同步 SQLAlchemy URL
settings.async_sqlalchemy_url   # 异步 SQLAlchemy URL
settings.has_llm                # 是否配置了 OPENAI_API_KEY
settings.llm_model / settings.embed_model / settings.embed_enabled
```

### DB（`virtual_human.db`）

```python
from virtual_human.db import (
    session_factory,          # 同步 Session 工厂
    async_session_factory,    # 异步 Session 工厂 (aio 主路径)
    run_async,                # Windows 安全: SelectorEventLoop 包装 asyncio.run
)
```

### 检索（`virtual_human.kb.async_api`，aio）

| 函数 | 签名 | 说明 |
| --- | --- | --- |
| `aget_persona` | `(session, slug) → Persona` | 取人物 |
| `aget_kb_tree` | `(session, persona_id) → TreeNode` | 整棵树（slug=`persona_id/node_type`），`.render()` 可直接输出 |
| `aget_node` | `(session, persona_id, slug, load_level)` | 渐进加载：1=summary / 2=含 contents 全文 |
| `aget_node_clues` | `(session, persona_id, slug) → list[dict]` | kb_edges 双向线索 |
| `aretrieve_context` | `(session, slug, query, top_k=20, with_skeleton=True) → RetrievedContext` | 检索+打分+预算装载+访问记录 |
| `arender_finance` | `(session, slug) → str` | 账本概览文本 |
| `arender_persona_md` | `(session, persona_id, min_disclosure=1, include_finance=True) → str` | MD 简介，summary 包 `<slug id>` 标签 |
| `achat_context` | `(session, slug, user_input) → str` | 一次性组装对话 system prompt 上下文 |

同步版在 `virtual_human.kb.retrieval`（`get_node` / `get_node_clues` / `get_kb_tree` / `retrieve_context` / `render_persona_md`…）。

---

## 2. vh_generate —— 生成

### 录入（`vh_generate.generate.seeder_tools`）

```python
async def ingest_persona_toolcall_async(
    skeleton: str,                    # 骨架提示词
    model: str | None = None,         # LLM 模型
    on_log: Callable | None,          # 工具调用短日志回调
    on_chunk: Callable | None,        # LLM 文本 chunk 回调 (实时)
    max_turns: int = 40,
) -> str                              # persona_id
```

- LLM 通过工具调用直接落库（5 个建表工具 + 7 个编辑工具），每工具即时 commit
- slug 冲突自动删旧重灌；单工具失败自动 rollback 并把错误回给 LLM 自纠

### 检查（`vh_generate.generate.checker`）

```python
async def check_persona_async(
    persona_id: str,
    fix: bool = False,                # True: 检查后自动调工具修复
    model, on_log, on_chunk, max_turns=20,
) -> list[str]                        # 内置硬规则问题清单 (LLM 报告经 on_chunk 流式输出)
```

检查维度：时间线自洽 / 逻辑一致 / 完整性 / 数据矛盾 / 财务合理性 / 文本质量。

### 编辑（`vh_generate.generate.editor`）

```python
async def edit_persona_async(
    persona_id: str,
    instruction: str,                 # 一句修改要求
    model, on_log, on_chunk, max_turns=20,
) -> bool
```

LLM 读取设定清单 → 对照要求 → 调 12 个工具（增删改）修改。

### 嵌入（`vh_generate.generate.embeddings`，可选）

```python
embed_texts(texts) -> list[list[float]] | None   # None=未启用 (VH_EMBED_ENABLED)
embed_query(text) -> list[float] | None
```

默认关闭。启用时导入 `vh_generate` 自动向知识库注册查询嵌入器（依赖注入，知识库不反向依赖）。

---

## 3. vh_simulate —— 模拟对话

### `PersonaAgent`（`vh_simulate.simulate`）

```python
agent = await PersonaAgent.create(
    persona_id, model=None,           # LLM 模型
    min_disclosure=1,                 # 简介 API 披露阈值
    with_finance=True,                # system prompt 带账本
)
agent.on_event = callback             # 渐进加载事件 (recall/compress/clear)

answer = await agent.asay("问题")      # 聚合返回
async for chunk in agent.stream("问题"): ...   # 流式 (推荐)
agent.reset()                         # 清对话历史
```

**上下文压缩中间件**（每轮自动）：
1. `compress_history_if_needed()`：历史 token > 预算×2.5 → LLM 压成 300 字要点笔记（发 `compress` 事件）
2. `_trim_history()`：LangChain `trim_messages` 硬裁剪到 预算×1.5，保留最近消息

**事件协议**：

| event | data | 时机 |
| --- | --- | --- |
| `recall` | `{blocks:[{source,title,label,score,tokens}], used, budget}` | 每轮检索后 |
| `compress` | `{before, after, summarized}` | 历史压缩时 |
| `clear` | — | 每轮回答结束 |

---

## 4. vh_api —— REST API

启动：`uv run vh-api [--port 8300]`，交互文档 `/docs`。

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/personas` | 人物列表 |
| GET | `/api/personas/{pid}/tree` | 知识树 (L1) |
| GET | `/api/personas/{pid}/profile` | MD 简介 |
| POST | `/api/personas/ingest` | 生成人设 (SSE) |
| POST | `/api/personas/{pid}/check` | 检查 (SSE, `{"fix":bool}`) |
| POST | `/api/personas/{pid}/edit` | 修改设定 (SSE) |
| POST | `/api/personas/{pid}/chat` | 对话 (SSE, `{"message","session_id"}`) |
| DELETE | `/api/sessions/{session_id}` | 结束会话 |

**SSE 事件**：`log`（工具日志）/ `chunk`（LLM 文本流）/ `recall`（记忆装载）/ `done` / `error`。

### Checkpoint（`vh_api.checkpointer`）

```python
from vh_api.checkpointer import make_thread_id, get_checkpointer, get_async_checkpointer

tid = make_thread_id(persona_id)   # 新会话线程 id (persona前缀-uuid)
cp = get_checkpointer()            # 同步 PostgresSaver (langgraph)
async_cp = get_async_checkpointer()  # AsyncPostgresSaver (连接池)
```

对话状态存 PostgreSQL（langgraph checkpoint 表），`thread_id` 即会话句柄，前端保存后可跨进程续聊。

### 前端（`web/`）

Vue3 + Element Plus + Vite。`npm install && npm run dev`（5273 端口，`/api` 代理到 8300）。
