<template>
  <section class="page fill-page">
    <div class="page-head">
      <h3>血肉铸造台 · 任务列表</h3>
      <p class="hint">铸造任务的执行记录、进度与产出人物</p>
    </div>

    <!-- ============ 顶部筛选栏 ============ -->
    <div class="toolbar">
      <el-select v-model="statusFilter" placeholder="状态" style="width: 130px" clearable>
        <el-option value="running" label="运行中" />
        <el-option value="done" label="已完成" />
        <el-option value="error" label="失败" />
      </el-select>

      <div class="toolbar-spacer" />
      <el-tag v-if="runningCount" type="warning" effect="plain" size="small">
        {{ runningCount }} 个任务运行中
      </el-tag>
      <el-button :icon="Refresh" @click="load">刷新</el-button>
    </div>

    <!-- ============ 任务表格 ============ -->
    <div class="table-box">
      <el-table
        :data="filtered" v-loading="loading"
        size="small" height="100%" @row-click="openDetail"
        :row-class-name="rowClass"
      >
        <el-table-column label="状态" width="100">
          <template #default="{ row }">
            <span class="st-cell">
              <el-icon v-if="row.status === 'running'" class="is-loading" color="#e6a23c"><Loading /></el-icon>
              <el-icon v-else-if="row.status === 'done'" color="#67c23a"><CircleCheck /></el-icon>
              <el-icon v-else color="#f56c6c"><CircleClose /></el-icon>
              {{ statusLabel(row.status) }}
            </span>
          </template>
        </el-table-column>

        <el-table-column label="类型" width="120">
          <template #default="{ row }">
            <el-tag :type="jobTypeTag(row.job_type)" effect="plain" size="small">
              {{ jobTypeLabel(row.job_type) }}
            </el-tag>
          </template>
        </el-table-column>

        <el-table-column label="进度" width="150">
          <template #default="{ row }">
            <el-progress
              :percentage="row.percent || 0"
              :status="row.status === 'error' ? 'exception' : (row.status === 'done' ? 'success' : undefined)"
              :stroke-width="8" :show-text="false"
              striped :striped-flow="row.status === 'running'"
            />
          </template>
        </el-table-column>

        <el-table-column label="消息" min-width="240" show-overflow-tooltip>
          <template #default="{ row }">{{ row.message || '—' }}</template>
        </el-table-column>

        <el-table-column label="产出/目标人物" width="200">
          <template #default="{ row }">
            <span v-if="personaIdOf(row)" class="persona-link" @click.stop="gotoPersona(personaIdOf(row))">
              {{ personaName(personaIdOf(row)) }}
              <span class="pid">{{ personaIdOf(row).slice(0, 8) }}…</span>
            </span>
            <span v-else class="muted">—</span>
          </template>
        </el-table-column>

        <el-table-column label="开始时间" width="130">
          <template #default="{ row }">{{ fmtTime(row.started_at || row.created_at) }}</template>
        </el-table-column>

        <el-table-column label="耗时" width="90">
          <template #default="{ row }">{{ duration(row) }}</template>
        </el-table-column>
      </el-table>
    </div>

    <!-- ============ 详情抽屉 ============ -->
    <el-drawer v-model="detailOpen" direction="rtl" size="48%">
      <template #header>
        <div class="drawer-head">
          <el-tag :type="statusTag(detail?.status)" size="small">{{ statusLabel(detail?.status) }}</el-tag>
          <b>{{ detail?.message || '铸造任务详情' }}</b>
        </div>
      </template>

      <template v-if="detail">
        <el-descriptions :column="2" border size="small">
          <el-descriptions-item label="任务 ID">{{ detail.id }}</el-descriptions-item>
          <el-descriptions-item label="进度">{{ detail.percent || 0 }}%</el-descriptions-item>
          <el-descriptions-item label="开始">{{ fmtTime(detail.started_at) || '—' }}</el-descriptions-item>
          <el-descriptions-item label="结束">{{ fmtTime(detail.finished_at) || '—' }}</el-descriptions-item>
          <el-descriptions-item label="产出/目标人物" :span="2">
            <span v-if="personaIdOf(detail)" class="persona-link" @click="gotoPersona(personaIdOf(detail))">
              {{ personaName(personaIdOf(detail)) }} ({{ personaIdOf(detail) }})
            </span>
            <span v-else class="muted">—</span>
          </el-descriptions-item>
          <el-descriptions-item label="输入" :span="2">
            <div class="payload">{{ payloadText(detail) }}</div>
          </el-descriptions-item>
        </el-descriptions>

        <div v-if="detail.job_type === 'scaffold' && personaIdOf(detail)" class="drawer-actions">
          <el-button type="primary" size="small" :icon="Refresh" @click="rerunScaffold">
            载入该人物去重跑 (生成知识节点/财务大事记)
          </el-button>
        </div>

        <template v-if="detail.report">
          <el-divider content-position="left">报告 / 过程</el-divider>
          <div class="md-body" v-html="renderMd(detail.report)"></div>
        </template>

        <template v-if="detail.logs?.length">
          <el-divider content-position="left">日志 ({{ detail.logs.length }})</el-divider>
          <div class="log-box">
            <div v-for="(l, i) in detail.logs" :key="i" class="log-line">{{ l }}</div>
          </div>
        </template>
      </template>
    </el-drawer>
  </section>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import { marked } from 'marked'
import { Refresh, Loading, CircleCheck, CircleClose } from '@element-plus/icons-vue'
import { API, personas, inspectTarget, loadPersonas } from '../store'

const router = useRouter()
const jobs = ref([])
const loading = ref(false)
const statusFilter = ref('')
const detailOpen = ref(false)
const detail = ref(null)
let timer = null

const JOB_TYPE = {
  ingest: ['铸造', 'primary'], check: ['质检', 'warning'],
  fix: ['修复', 'danger'], edit: ['修改', 'success'],
  scaffold: ['血肉铸造', 'primary'],
}
const jobTypeLabel = (t) => JOB_TYPE[t]?.[0] || t
const jobTypeTag = (t) => JOB_TYPE[t]?.[1] || 'info'

const STATUS = { running: ['运行中', 'warning'], done: ['已完成', 'success'], error: ['失败', 'danger'] }
const statusLabel = (s) => STATUS[s]?.[0] || s
const statusTag = (s) => STATUS[s]?.[1] || 'info'

const filtered = computed(() =>
  statusFilter.value ? jobs.value.filter((j) => j.status === statusFilter.value) : jobs.value
)
const runningCount = computed(() => jobs.value.filter((j) => j.status === 'running').length)

function fmtTime(ts) {
  if (!ts) return ''
  const d = typeof ts === 'number' ? new Date(ts * 1000) : new Date(ts)
  if (Number.isNaN(d.getTime())) return ''
  const p = (n) => String(n).padStart(2, '0')
  return `${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`
}

function duration(row) {
  const s = row.started_at
  if (!s) return '—'
  const end = row.finished_at || Date.now() / 1000
  const sec = Math.max(0, Math.round(end - (typeof s === 'number' ? s : new Date(s).getTime() / 1000)))
  return sec < 60 ? `${sec}s` : `${Math.floor(sec / 60)}m${sec % 60}s`
}

/** scaffold 任务 result 里没有 persona_id, 从 payload 兜底 */
function personaIdOf(row) {
  return row?.persona_id || row?.payload?.persona_id || null
}

function personaName(pid) {
  const p = personas.value.find((x) => x.id === pid)
  return p ? p.name : '未知人物'
}

function payloadText(j) {
  const p = j.payload || {}
  if (p.skeleton) return p.skeleton
  if (p.instruction) return p.instruction
  return JSON.stringify(p).slice(0, 300) || '—'
}

const renderMd = (t) => (t ? marked.parse(t) : '')
const rowClass = ({ row }) => `row-${row.status}`
const openDetail = (row) => { detail.value = row; detailOpen.value = true }

function gotoPersona(pid) {
  if (!pid) return
  inspectTarget.value = pid
  detailOpen.value = false
  router.push('/inspect/check')
}

function rerunScaffold() {
  const pid = personaIdOf(detail.value)
  if (!pid) return
  detailOpen.value = false
  router.push({ path: '/forge', query: { persona: pid } })
}

async function load() {
  loading.value = true
  try {
    const r = await fetch(`${API}/api/jobs?limit=100`)
    if (r.ok) jobs.value = await r.json()
    // 有运行中任务 → 5s 轮询, 全部结束则停止
    if (jobs.value.some((j) => j.status === 'running')) startPolling()
    else stopPolling()
  } finally { loading.value = false }
}

function startPolling() {
  if (timer) return
  timer = setInterval(async () => {
    try {
      const r = await fetch(`${API}/api/jobs?limit=100`)
      if (r.ok) jobs.value = await r.json()
      if (!jobs.value.some((j) => j.status === 'running')) stopPolling()
    } catch { /* ignore */ }
  }, 5000)
}

function stopPolling() {
  if (timer) { clearInterval(timer); timer = null }
}

async function refreshPersonas() {
  try { await loadPersonas() } catch { /* ignore */ }
}

onMounted(async () => {
  await refreshPersonas()
  await load()
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

.table-box {
  flex: 1; min-height: 0; background: #fff; border: 1px solid #e4e7ed;
  border-radius: 8px; overflow: hidden; padding: 4px 8px 8px;
}
.table-box :deep(.el-table) { height: 100%; }
.table-box :deep(.row-running) { background: #fdf6ec; }
.table-box :deep(.row-error) { background: #fef0f0; }
.table-box :deep(.el-table__row) { cursor: pointer; }

.st-cell { display: inline-flex; align-items: center; gap: 4px; font-size: 12px; }
.persona-link { color: #409eff; cursor: pointer; font-size: 12.5px; }
.persona-link:hover { text-decoration: underline; }
.pid { color: #c0c4cc; font-size: 11px; margin-left: 4px; }
.muted { color: #c0c4cc; }

.drawer-head { display: flex; align-items: center; gap: 8px; }
.drawer-actions { margin-top: 12px; display: flex; gap: 8px; }
.payload {
  font-size: 12px; color: #606266; line-height: 1.7; max-height: 120px; overflow-y: auto;
  white-space: pre-wrap; word-break: break-all;
}
.log-box {
  background: #1e1e1e; color: #c8c8c8; border-radius: 6px; padding: 10px 12px;
  font-size: 11.5px; line-height: 1.6; font-family: Consolas, monospace;
  max-height: 320px; overflow-y: auto;
}
.log-line { white-space: pre-wrap; word-break: break-all; }

.md-body :deep(h1) { font-size: 17px; margin: 10px 0 6px; }
.md-body :deep(h2) { font-size: 15px; margin: 10px 0 6px; border-bottom: 1px solid #ebeef5; padding-bottom: 4px; }
.md-body :deep(p) { margin: 6px 0; line-height: 1.8; font-size: 13px; }
.md-body :deep(li) { margin: 3px 0; line-height: 1.7; font-size: 13px; }
.md-body :deep(code) { background: #f0f2f5; padding: 1px 5px; border-radius: 4px; font-size: 12px; color: #c7254e; }
</style>
