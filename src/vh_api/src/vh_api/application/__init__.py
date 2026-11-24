"""应用层 —— 用例编排.

每个限界上下文一个应用服务:
    persona_service   人物查询 (列表/树/简介)         → virtual_human.kb
    ingest_service    人设生成 (骨架 → 工具调用落库)   → vh_generate
    inspect_service   检查与修改 (质量门禁/设定编辑)    → vh_generate
    simulate_service  模拟对话 (会话/流式回答)          → vh_simulate + infra.sessions

应用层只编排, 不写业务规则 (规则在域模块 vh_generate/vh_simulate/virtual_human),
也不碰 HTTP (那是 routers 的事).
"""
