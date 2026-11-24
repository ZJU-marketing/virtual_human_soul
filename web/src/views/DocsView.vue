<template>
  <section class="page">
    <div class="page-head">
      <h3>系统 · 模块 API</h3>
      <p class="hint">工作区五个子包的核心函数，依赖方向: vh_api → vh_simulate → vh_generate → virtual_human</p>
    </div>
    <el-table :data="apiDocs" border size="small">
      <el-table-column prop="module" width="150" label="模块" />
      <el-table-column prop="func" width="320" label="函数" />
      <el-table-column prop="desc" label="说明" />
    </el-table>
  </section>
</template>

<script setup>
const apiDocs = [
  { module: 'vh_generate', func: 'ingest_persona_toolcall_async(skeleton, model, on_log, on_chunk)', desc: '骨架提示词 → LLM 工具调用 → 生成人设落库, 返回 persona_id' },
  { module: 'vh_generate', func: 'check_persona_async(persona_id, fix, ...)', desc: '检查人物数据合理性 (时间线/逻辑/完整性/财务), fix=true 自动修复' },
  { module: 'vh_generate', func: 'edit_persona_async(persona_id, instruction, ...)', desc: '按一句修改要求, LLM 对照设定清单调工具修改' },
  { module: 'vh_simulate', func: 'PersonaAgent.create(persona_id)', desc: '从 persona_id 构建模拟对话 agent (system prompt 组装)' },
  { module: 'vh_simulate', func: 'agent.stream(text) / agent.asay(text)', desc: '异步流式对话; 内置 trim+摘要压缩中间件, on_event 可订阅 recall/compress' },
  { module: 'virtual_human.kb', func: 'aget_kb_tree(session, persona_id)', desc: '整棵知识树 (slug=persona_id/node_type), L1 渐进层' },
  { module: 'virtual_human.kb', func: 'aget_node(session, persona_id, slug, load_level)', desc: '单节点渐进加载: 1=summary / 2=内容块全文' },
  { module: 'virtual_human.kb', func: 'aget_node_clues(session, persona_id, slug)', desc: 'kb_edges 关联线索 (联想加载)' },
  { module: 'virtual_human.kb', func: 'aretrieve_context(session, slug, query)', desc: '检索+load_score 打分+预算装载+访问记录 (渐进式核心)' },
  { module: 'virtual_human.kb', func: 'arender_persona_md(session, persona_id, min_disclosure)', desc: 'Markdown 简介, summary 带 <slug> 标签, 按披露度过滤' },
  { module: 'virtual_human.kb', func: 'arender_finance(session, slug)', desc: '财务账本概览 (精确数字, 不走向量)' },
  { module: 'vh_api.infra', func: 'SseChannel / registry / get_checkpointer', desc: 'SSE 事件通道 / 会话注册表 / langgraph Postgres checkpoint' },
]
</script>
