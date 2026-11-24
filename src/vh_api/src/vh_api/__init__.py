"""vh_api —— REST API 模块 (FastAPI, DDD 分层).

层次:
    routers/       接口层: HTTP 路由 (persona / ingest / inspect / simulate)
    application/   应用层: 用例编排 (跨模块调用 vh_generate / vh_simulate / virtual_human)
    infra/         基础设施: SSE 通道 / 会话注册表 / checkpoint / 回调→流桥接

启动: uv run vh-api [--port 8300]   文档: /docs
"""
