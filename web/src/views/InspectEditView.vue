<template>
  <section class="page fill-page">
    <div class="page-head">
      <h3>人物质检室 · 修改</h3>
      <p class="hint">下达一句修改指令，LLM 对照人物设定清单逐项检查并调工具修改（后台任务，5 秒刷新进度）</p>
    </div>

    <!-- ============ 顶部操作栏 ============ -->
    <div class="toolbar">
      <span class="picker-label">修改对象</span>
      <el-select
        v-model="inspectTarget" placeholder="选择人物" style="width: 260px" clearable
        @change="onPersonaChange"
      >
        <el-option v-for="p in personas" :key="p.id" :value="p.id" :label="`${p.name} (${p.slug})`" />
      </el-select>
      <el-button text :icon="Refresh" @click="loadPersonas">刷新列表</el-button>

      <div class="toolbar-spacer" />

      <el-button v-if="hasResult" plain :icon="Document" @click="openReport">查看结果</el-button>
      <el-button
        type="primary" :icon="Promotion" :loading="editing"
        :disabled="!inspectTarget || !instruction.trim()"
        @click="doEdit"
      >下达修改</el-button>
    </div>

    <el-alert v-if="!inspectTarget" type="info" :closable="false" title="先选择修改对象" show-icon />

    <template v-if="inspectTarget">
      <!-- ============ 任务进度条 ============ -->
      <div v-if="showTask" class="task-progress" :class="`tp-${task.status}`">
        <div class="tp-head">
          <el-icon v-if="task.status === 'running'" class="is-loading"><Loading /></el-icon>
          <el-icon v-else-if="task.status === 'done'" color="#67c23a"><CircleCheck /></el-icon>
          <el-icon v-else color="#f56c6c"><CircleClose /></el-icon>
          <b>修改{{ task.status === 'running' ? '中' : task.status === 'done' ? '完成' : '失败' }}</b>
          <span class="tp-msg" :title="task.message">{{ task.message }}</span>
          <el-button v-if="task.status !== 'running'" link size="small" @click="logsOpen = !logsOpen">
            {{ logsOpen ? '隐藏日志' : '展开日志' }}
          </el-button>
          <el-button v-if="task.status !== 'running'" link size="small" @click="dismissTask">收起</el-button>
        </div>
        <el-progress
          :percentage="task.percent || 0"
          :status="task.status === 'error' ? 'exception' : (task.status === 'done' ? 'success' : undefined)"
          :stroke-width="10" striped :striped-flow="task.status === 'running'"
        />
        <div class="tp-meta">
          <span>{{ task.percent || 0 }}%</span>
          <span v-if="task.status === 'running'">每 5 秒自动刷新</span>
        </div>
        <div v-if="task.logs?.length" v-show="task.status === 'running' || logsOpen" class="tp-logs">
          <div v-for="(l, i) in task.logs" :key="i" class="tp-log">{{ l }}</div>
        </div>
      </div>

      <!-- ============ 主线: 指令输入 + 结果预览 ============ -->
      <div class="split fill-split">
        <!-- 左: 指令 -->
        <div class="left-panel">
          <!-- 定向范围: 多选节点 -->
          <div class="scope-block">
            <div class="panel-title">
              定向修改范围
              <el-tag v-if="selectedNodeIds.length" size="small" type="warning">{{ selectedNodeIds.length }} 个节点</el-tag>
              <el-tag v-else size="small" type="info">全人物</el-tag>
            </div>
            <el-select
              v-model="selectedNodeIds" multiple collapse-tags collapse-tags-tooltip
              filterable clearable placeholder="不选=全人物；可多选节点定向修改"
              style="width: 100%"
            >
              <el-option
                v-for="n in nodeOptions" :key="n.id" :value="n.id"
                :label="`[${typeLabel(n.node_type)}] ${n.title}`"
              />
            </el-select>
            <div class="scope-hint">
              选中后只允许修改这些节点及其内容块，其它数据不会被改动
            </div>
          </div>

          <div class="panel-title">修改指令</div>
          <el-input
            v-model="instruction" type="textarea" :rows="8" resize="none"
            placeholder="例: 把女儿的名字改成陈晓雨, 考上的大学改成河北师范大学&#10;例: 给 2020-2022 补一段疫情期间上网课的经历"
          />
          <div class="quick-tips">
            <div class="tips-label">常用指令</div>
            <el-tag
              v-for="t in quickTips" :key="t" class="tip-tag" effect="plain"
              @click="instruction = t"
            >{{ t }}</el-tag>
          </div>
        </div>

        <!-- 右: 执行过程 / 结果 (Markdown) -->
        <div class="right-panel">
          <div class="panel-title">
            执行过程
            <el-tag v-if="task?.status === 'running'" size="small" type="warning">进行中</el-tag>
            <el-tag v-else-if="task?.status === 'done'" size="small" type="success">已完成</el-tag>
          </div>
          <div class="result-body">
            <div v-if="!reportText" class="empty-tip">
              <el-empty description="下达修改后，这里显示 LLM 的修改过程与结果" :image-size="70" />
            </div>
            <div v-else class="md-body" v-html="renderedReport"></div>
          </div>
        </div>
      </div>
    </template>

    <!-- 结果抽屉 -->
    <el-drawer v-model="reportOpen" direction="rtl" size="46%" title="修改结果">
      <div class="md-body" v-html="renderedReport"></div>
    </el-drawer>
  </section>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted, watch } from 'vue'
import { marked } from 'marked'
import { Loading, CircleCheck, CircleClose, Refresh, Document, Promotion } from '@element-plus/icons-vue'
import { API, personas, inspectTarget, loadPersonas } from '../store'

const instruction = ref('')
const editing = ref(false)
const logsOpen = ref(false)
const reportOpen = ref(false)
const selectedNodeIds = ref([])
const nodeOptions = ref([])

const TYPE_LABEL = { chapter: '章节', semantic: '观念', procedural: '习惯', identity: '身份' }
const typeLabel = (t) => TYPE_LABEL[t] || t

/** 拉取该人物的知识节点, 作为定向修改的可选项 */
async function loadNodes() {
  nodeOptions.value = []
  if (!inspectTarget.value) return
  try {
    const r = await fetch(`${API}/api/personas/${inspectTarget.value}/tree`)
    if (!r.ok) return
    const tree = await r.json()
    const out = []
    const walk = (list) => {
      for (const n of list || []) {
        out.push({ id: n.id || n.node_id, title: n.title, node_type: n.node_type })
        walk(n.children)
      }
    }
    walk(tree.children)
    nodeOptions.value = out
  } catch { /* ignore */ }
}

const task = ref({ status: 'idle', percent: 0, message: '', logs: [], report: '' })
let timer = null

const hasResult = computed(() => !!task.value.report)
const reportText = computed(() => task.value.report || '')
const renderedReport = computed(() => (reportText.value ? marked.parse(reportText.value) : ''))
// 仅运行中/完成/失败展示进度条; idle = 已收起
const showTask = computed(() => ['running', 'done', 'error'].includes(task.value.status))

const quickTips = [
  '把女儿的名字改一下',
  '检查时间线是否自洽并修正',
  '补一段疫情期间在家上网课的经历',
  '统一所有金额口径，确保首付+贷款=总价',
]

async function fetchStatus() {
  if (!inspectTarget.value) return { status: 'idle' }
  try {
    const r = await fetch(`${API}/api/personas/${inspectTarget.value}/edit/status`)
    if (r.ok) return await r.json()
  } catch { /* ignore */ }
  return { status: 'idle' }
}

function stopPolling() {
  if (timer) { clearInterval(timer); timer = null }
}

function startPolling() {
  stopPolling()
  timer = setInterval(async () => {
    const st = await fetchStatus()
    if (st.status === 'running') {
      task.value = st
      return
    }
    stopPolling()               // done/error → 不再定时
    if (st.status === 'done') {
      task.value = st
      editing.value = false
      setTimeout(() => { if (task.value.status === 'done') task.value = { ...task.value, status: 'idle' } }, 800)
    } else if (st.status === 'error') {
      task.value = {
        ...st,
        report: st.report || `## 修改失败\n\n**原因:** ${st.message}\n\n> 可展开日志查看调用过程, 或稍后重试`,
      }
      editing.value = false
    }
  }, 5000)
}

async function doEdit() {
  if (!inspectTarget.value || !instruction.value.trim()) return
  editing.value = true
  logsOpen.value = false
  task.value = { status: 'running', percent: 5, message: '提交中…', logs: [], report: '' }
  try {
    const r = await fetch(`${API}/api/personas/${inspectTarget.value}/edit`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        instruction: instruction.value,
        node_ids: selectedNodeIds.value,     // 空数组 = 全人物
      }),
    })
    if (r.ok) {
      const d = await r.json()
      if (d.state) task.value = d.state
      startPolling()
    } else {
      editing.value = false
    }
  } catch {
    editing.value = false
  }
}

async function onPersonaChange() {
  stopPolling()
  editing.value = false
  selectedNodeIds.value = []
  task.value = { status: 'idle', percent: 0, message: '', logs: [], report: '' }
  await loadNodes()
}

async function syncTask() {
  const st = await fetchStatus()
  if (st.status === 'running') {
    task.value = st
    editing.value = true
    startPolling()
  } else if (st.status === 'done' || st.status === 'error') {
    task.value = { ...st, status: 'idle' }   // 旧任务不展示进度条, 但保留 report
  }
}

function dismissTask() {
  task.value = { ...task.value, status: 'idle' }
}

function openReport() { reportOpen.value = true }

watch(inspectTarget, syncTask)
onMounted(async () => {
  if (inspectTarget.value) {
    await syncTask()
    await loadNodes()
  }
})
onUnmounted(stopPolling)
</script>

<style scoped>
.fill-page {
  display: flex; flex-direction: column;
  flex: 1; min-height: 0;
  padding-bottom: 12px;
  width: clamp(1000px, 62vw, 1800px);
  max-width: 100%;
  margin: 0 auto;
}
.page-head { flex-shrink: 0; }
.toolbar {
  flex-shrink: 0; display: flex; align-items: center; gap: 12px;
  background: #fff; border: 1px solid #e4e7ed; border-radius: 8px;
  padding: 10px 14px; margin-bottom: 12px;
}
.toolbar-spacer { flex: 1; }
.picker-label { font-size: 13px; color: #606266; }

/* 进度条 */
.task-progress {
  flex-shrink: 0; background: #fff; border: 1px solid #e4e7ed; border-radius: 8px;
  padding: 10px 14px; margin-bottom: 12px;
}
.task-progress.tp-done { border-color: #d1edc4; background: #f0f9eb; }
.task-progress.tp-error { border-color: #fbc4c4; background: #fef0f0; }
.tp-head { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; font-size: 13px; }
.tp-msg { flex: 1; color: #606266; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.tp-meta { display: flex; justify-content: space-between; font-size: 11px; color: #909399; margin-top: 4px; }
.tp-logs {
  margin-top: 8px; max-height: 72px; overflow-y: auto;
  background: #1e1e1e; color: #c8c8c8; border-radius: 6px; padding: 8px 10px;
  font-size: 11.5px; line-height: 1.6; font-family: Consolas, monospace;
}
.tp-log { white-space: pre-wrap; word-break: break-all; }

/* 主体: 左指令 右结果 (黄金分割 38 : 62) */
.fill-split {
  flex: 1 1 auto; min-height: 0;
  display: grid; grid-template-columns: 38fr 62fr; gap: 16px; align-items: stretch;
}
.left-panel, .right-panel {
  min-height: 0; background: #fff; border: 1px solid #e4e7ed; border-radius: 8px;
  padding: 16px; display: flex; flex-direction: column; overflow: hidden;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.04);
}
.panel-title {
  font-size: 13px; font-weight: 600; color: #303133; margin-bottom: 10px;
  display: flex; align-items: center; gap: 8px; flex-shrink: 0;
}
.scope-block {
  flex-shrink: 0; margin-bottom: 14px;
  background: #f9fafc; border: 1px solid #ebeef5; border-radius: 6px; padding: 10px 12px;
}
.scope-block .panel-title { margin-bottom: 8px; }
.scope-hint { font-size: 11px; color: #909399; margin-top: 6px; line-height: 1.5; }

.left-panel :deep(.el-textarea__inner) {
  font-size: 13px; line-height: 1.8; border-radius: 6px;
}
.quick-tips {
  margin-top: 14px; flex-shrink: 0;
  border-top: 1px dashed #ebeef5; padding-top: 12px;
}
.tips-label { font-size: 12px; color: #909399; margin-bottom: 8px; }
.tip-tag { margin: 0 6px 8px 0; cursor: pointer; transition: 0.15s; }
.tip-tag:hover { color: #409eff; border-color: #409eff; background: #ecf5ff; }

.result-body { flex: 1; min-height: 0; overflow-y: auto; }
.empty-tip { display: grid; place-items: center; height: 100%; }

.md-body :deep(h1) { font-size: 18px; margin: 12px 0 8px; }
.md-body :deep(h2) { font-size: 16px; margin: 12px 0 8px; border-bottom: 1px solid #ebeef5; padding-bottom: 4px; }
.md-body :deep(h3) { font-size: 14px; margin: 10px 0 6px; }
.md-body :deep(p) { margin: 6px 0; line-height: 1.8; }
.md-body :deep(ul), .md-body :deep(ol) { padding-left: 22px; margin: 6px 0; }
.md-body :deep(li) { margin: 3px 0; line-height: 1.7; }
.md-body :deep(code) { background: #f0f2f5; padding: 1px 5px; border-radius: 4px; font-size: 12px; color: #c7254e; }
.md-body :deep(pre) { background: #1e1e1e; color: #d4d4d4; padding: 10px; border-radius: 6px; overflow-x: auto; }
.md-body :deep(blockquote) { margin: 8px 0; padding: 8px 12px; border-left: 3px solid #e6a23c; background: #fdf6ec; border-radius: 0 4px 4px 0; }
.md-body :deep(strong) { color: #303133; }
</style>
