<template>
  <section class="page fill-page">
    <div class="page-head row-head">
      <div>
        <h3>人物质检室 · 时间线</h3>
        <p class="hint">{{ personaName }} 的人生履历，按开始时间排序；右侧可按时间策略插入事件并让 agent 生成</p>
      </div>
      <div class="toolbar-spacer" />
      <el-select
        v-model="selectedPid" placeholder="切换人物" size="small" filterable
        style="width: 190px" @change="onSwitchPersona"
      >
        <el-option v-for="p in personas" :key="p.id" :label="p.name" :value="p.id" />
      </el-select>
      <el-button size="small" :icon="Back" @click="$router.push('/inspect/personas')">返回列表</el-button>
    </div>

    <div class="split">
      <!-- ==================== 左: 时间轴 ==================== -->
      <div class="left-col">
        <div class="toolbar">
          <el-radio-group v-model="viewMode" size="small">
            <el-radio-button value="timeline">时间轴</el-radio-button>
            <el-radio-button value="table">列表</el-radio-button>
          </el-radio-group>
          <el-checkbox v-model="hideMinor" size="small" style="margin-left: 10px">
            隐藏低权重 ({{ minorCount }})
          </el-checkbox>
          <div class="toolbar-spacer" />
          <el-tag size="small" type="info" effect="plain">共 {{ events.length }}</el-tag>
          <el-tag size="small" type="success" effect="plain">{{ doneCount }} 已消化</el-tag>
          <el-button size="small" :icon="Refresh" @click="load">刷新</el-button>
        </div>

        <div v-if="viewMode === 'timeline'" class="tl-scroll">
          <el-empty v-if="!loading && !shown.length" description="该人物暂无履历" :image-size="90" />
          <el-timeline v-else class="tl">
            <el-timeline-item
              v-for="e in shown" :key="e.id"
              :timestamp="String(e.start_year ?? e.start_raw ?? '—')"
              :type="e.processed ? 'success' : 'info'"
              :hollow="!e.processed"
              placement="top"
            >
              <div class="tl-card" :class="{ done: e.processed }" @click="openEvent(e)">
                <div class="tlc-head">
                  <el-tag size="small" effect="plain" :type="e.processed ? 'success' : 'info'">{{ e.event_type }}</el-tag>
                  <b class="tlc-title">{{ e.summary || e.event_type }}</b>
                  <span class="tlc-w">w{{ weightOf(e) }}</span>
                  <el-icon class="tl-del" title="删除该事件" @click.stop="deleteEvent(e)"><Delete /></el-icon>
                </div>
                <p class="tlc-summary">{{ e.summary || payloadText(e.payload) || '（暂无摘要）' }}</p>
              </div>
            </el-timeline-item>
          </el-timeline>
        </div>

        <div v-else class="table-box">
          <el-table :data="shown" v-loading="loading" size="small" height="100%" @row-click="openEvent">
            <el-table-column label="年份" width="80">
              <template #default="{ row }"><b class="year">{{ row.start_year ?? row.start_raw ?? '—' }}</b></template>
            </el-table-column>
            <el-table-column label="类型" width="110">
              <template #default="{ row }">
                <el-tag size="small" effect="plain" :type="row.processed ? 'success' : 'info'">{{ row.event_type }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column label="摘要" min-width="220" show-overflow-tooltip>
              <template #default="{ row }">{{ row.summary || payloadText(row.payload) || '—' }}</template>
            </el-table-column>
            <el-table-column label="状态" width="90" align="center">
              <template #default="{ row }">
                <el-tag size="small" :type="row.processed ? 'success' : 'warning'" effect="plain">
                  {{ row.processed ? '已消化' : '待消化' }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column label="权重" width="70" align="center">
              <template #default="{ row }">w{{ weightOf(row) }}</template>
            </el-table-column>
            <el-table-column label="操作" width="70" align="center">
              <template #default="{ row }">
                <el-button link type="danger" size="small" @click.stop="deleteEvent(row)">删除</el-button>
              </template>
            </el-table-column>
          </el-table>
        </div>
      </div>

      <!-- ==================== 右: 插入事件 ==================== -->
      <div class="right-col">
        <div class="insert-panel">
          <div class="panel-title">
            插入履历事件
            <div class="toolbar-spacer" />
            <el-button text size="small" @click="fillSample">示例</el-button>
          </div>

          <div class="strategy-row">
            <el-radio-group v-model="strategy" size="small">
              <el-radio-button value="insert">仅插入</el-radio-button>
              <el-radio-button value="replace">仅替换</el-radio-button>
              <el-radio-button value="merge">合并</el-radio-button>
            </el-radio-group>
            <template v-if="strategy === 'merge'">
              <span class="mode-label">payload</span>
              <el-radio-group v-model="payloadMode" size="small">
                <el-radio-button value="merge">合并</el-radio-button>
                <el-radio-button value="replace">替换</el-radio-button>
              </el-radio-group>
            </template>
          </div>
          <p class="strategy-tip">{{ strategyTip }}</p>

          <el-input
            v-model="insertText" type="textarea" resize="none"
            class="json-input" placeholder='粘贴事件数组: [{"type":"出生","start":"1954","payload":"{\"地点\":\"陕西市区\"}"}, ...]'
          />

          <div class="parse-row">
            <template v-if="parseError">
              <span class="err">{{ parseError }}</span>
            </template>
            <template v-else-if="parsedEvents.length">
              <el-tag size="small" type="info" effect="plain">{{ parsedEvents.length }} 条</el-tag>
              <el-tag v-if="yearRange" size="small" type="info" effect="plain">{{ yearRange }}</el-tag>
              <span class="types">{{ typeList }}</span>
            </template>
            <span v-else class="muted">等待粘贴 JSON</span>
          </div>

          <div class="insert-actions">
            <el-checkbox v-model="generateAfter" size="small">插入后由 agent 生成</el-checkbox>
            <el-button
              type="primary" size="small" :loading="inserting"
              :disabled="!parsedEvents.length || !!parseError || inserting"
              @click="doInsert"
            >{{ strategy === 'insert' ? '插入' : strategy === 'replace' ? '替换' : '合并' }}</el-button>
          </div>

          <!-- 生成任务进度 -->
          <div v-if="insertJob" class="job-box" :class="`job-${insertJob.status}`">
            <div class="job-head">
              <el-icon v-if="insertJob.status === 'running'" class="is-loading"><Loading /></el-icon>
              <el-icon v-else-if="insertJob.status === 'done'" color="#67c23a"><CircleCheck /></el-icon>
              <el-icon v-else color="#f56c6c"><CircleClose /></el-icon>
              <span class="job-msg" :title="insertJob.message">{{ insertJob.message }}</span>
              <el-button v-if="insertJob.logs?.length" link size="small" @click="logsOpen = !logsOpen">
                {{ logsOpen ? '收起日志' : '日志' }}
              </el-button>
            </div>
            <el-progress
              :percentage="insertJob.percent || 0"
              :status="insertJob.status === 'error' ? 'exception' : insertJob.status === 'done' ? 'success' : undefined"
              :stroke-width="8" :show-text="false"
            />
            <div v-if="insertJob.logs?.length" v-show="logsOpen || insertJob.status === 'running'" class="job-logs">
              <div v-for="(l, i) in insertJob.logs" :key="i">{{ l }}</div>
            </div>
          </div>

          <div class="insert-help">
            <div><b>仅插入</b>：直接追加，不删除已有事件</div>
            <div><b>仅替换</b>：删除入参年份范围内的旧事件，再插入</div>
            <div><b>合并</b>：同年份+同类型合并 payload；找不到则插入</div>
          </div>
        </div>
      </div>
    </div>

    <!-- ============ 事件详情抽屉 ============ -->
    <el-drawer v-model="detailOpen" direction="rtl" size="46%">
      <template #header>
        <div class="dw-head">
          <el-tag size="small" effect="plain">{{ current?.event_type }}</el-tag>
          <b>{{ currentYear }} · {{ current?.event_type }}</b>
          <el-tag size="small" :type="current?.processed ? 'success' : 'warning'" effect="plain">
            {{ current?.processed ? '已消化' : '待消化' }}
          </el-tag>
        </div>
      </template>
      <template v-if="current">
        <div class="sec-label">摘要</div>
        <p class="sec-text">{{ current.summary || '（暂无摘要）' }}</p>

        <div class="sec-label">展开内容 <span class="sub">life_scaffold_contents</span></div>
        <div v-if="!current.contents?.length" class="sec-empty">
          暂无展开内容；可在铸造页对该事件单独「重新生成」
        </div>
        <div v-for="c in current.contents" :key="c.id" class="content-card">
          <p class="cc-text">{{ c.content }}</p>
        </div>

        <div class="sec-label">原始事件 <span class="sub">life_scaffold_events.payload</span></div>
        <pre class="payload">{{ prettyPayload(current.payload) }}</pre>

        <div v-if="current.memory_id" class="sec-label">关联记忆</div>
        <el-button v-if="current.memory_id" link type="primary" size="small" @click="goMemory">
          查看生成的情景记忆 →
        </el-button>

        <div class="dw-foot">
          <el-button type="danger" plain size="small" :icon="Delete" @click="deleteCurrent">
            删除该事件
          </el-button>
        </div>
      </template>
    </el-drawer>
  </section>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Back, CircleCheck, CircleClose, Delete, Loading, Refresh } from '@element-plus/icons-vue'
import { API, personas, inspectTarget, loadPersonas } from '../store'

const route = useRoute()
const router = useRouter()

const selectedPid = ref(route.params.pid ? String(route.params.pid) : (inspectTarget.value || ''))
const events = ref([])
const loading = ref(false)
const viewMode = ref('timeline')
const hideMinor = ref(false)
const detailOpen = ref(false)
const current = ref(null)

// ---- 插入面板 ----
const strategy = ref('insert')
const payloadMode = ref('merge')
const insertText = ref('')
const generateAfter = ref(true)
const inserting = ref(false)
const insertJob = ref(null)
const logsOpen = ref(false)
let jobTimer = null

const personaName = computed(
  () => personas.value.find((p) => p.id === selectedPid.value)?.name || '人物',
)
const doneCount = computed(() => events.value.filter((e) => e.processed).length)

const MINOR_TYPES = new Set(['时代暴露', '认知快照', '体貌定型'])
function weightOf(e) {
  if (e.importance != null) return e.importance
  return MINOR_TYPES.has(e.event_type) ? 3 : 6
}
const minorCount = computed(() => events.value.filter((e) => weightOf(e) < 5).length)
const shown = computed(() =>
  hideMinor.value ? events.value.filter((e) => weightOf(e) >= 5) : events.value,
)

const strategyTip = computed(() => ({
  insert: '仅追加：不会修改或删除已有事件',
  replace: '删除入参年份范围内的旧事件（含记忆/展开），再插入新事件',
  merge: '同年份 + 同类型事件：按 payload 合并/替换；不存在的则插入',
}[strategy.value]))

// ---- JSON 解析 ----
const parseResult = computed(() => {
  const txt = insertText.value.trim()
  if (!txt) return { events: [], error: '' }
  try {
    const data = JSON.parse(txt)
    const arr = Array.isArray(data) ? data : (Array.isArray(data.events) ? data.events : null)
    if (!arr) return { events: [], error: 'JSON 必须是事件数组' }
    const evs = arr.filter((x) => x && typeof x === 'object')
    if (!evs.length) return { events: [], error: '没有有效事件' }
    return { events: evs, error: '' }
  } catch (e) {
    return { events: [], error: `JSON 解析失败: ${e.message}` }
  }
})
const parsedEvents = computed(() => parseResult.value.events)
const parseError = computed(() => parseResult.value.error)
const yearRange = computed(() => {
  const years = parsedEvents.value
    .map((e) => parseInt(String(e.start ?? e.start_year ?? '').match(/\d{4}/)?.[0], 10))
    .filter((y) => !Number.isNaN(y))
  if (!years.length) return ''
  const lo = Math.min(...years)
  const hi = Math.max(...years)
  return lo === hi ? String(lo) : `${lo} – ${hi}`
})
const typeList = computed(() => {
  const set = [...new Set(parsedEvents.value.map((e) => e.type || e.event_type).filter(Boolean))]
  return set.slice(0, 5).join(' / ') + (set.length > 5 ? ' …' : '')
})

const currentYear = computed(
  () => current.value?.start_year ?? current.value?.start_raw ?? '—',
)

function payloadText(p) {
  if (p == null) return ''
  return typeof p === 'string' ? p : JSON.stringify(p)
}

function prettyPayload(p) {
  if (p == null) return '—'
  try {
    return JSON.stringify(typeof p === 'string' ? JSON.parse(p) : p, null, 2)
  } catch {
    return String(p)
  }
}

function openEvent(e) {
  current.value = e
  detailOpen.value = true
}

async function deleteEvent(e) {
  if (!selectedPid.value) return
  try {
    await ElMessageBox.confirm(
      `删除 ${e.start_year ?? e.start_raw ?? '—'} · ${e.event_type}？\n其展开内容与关联记忆会一并删除。`,
      '删除履历事件',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  const r = await fetch(
    `${API}/api/personas/${selectedPid.value}/scaffold/events/${e.id}`,
    { method: 'DELETE' },
  )
  if (!r.ok) {
    const d = await r.json().catch(() => ({}))
    ElMessage.error(d.detail || `删除失败 HTTP ${r.status}`)
    return
  }
  if (current.value?.id === e.id) {
    detailOpen.value = false
    current.value = null
  }
  await load()
  ElMessage.success('已删除该履历事件')
}

function deleteCurrent() {
  if (current.value) deleteEvent(current.value)
}

function goMemory() {
  if (!selectedPid.value) return
  inspectTarget.value = selectedPid.value
  detailOpen.value = false
  router.push('/inspect/check')
}

function onSwitchPersona(pid) {
  if (!pid) return
  router.replace(`/inspect/time-line/${pid}`)
  insertJob.value = null
  load()
}

async function load() {
  if (!selectedPid.value) return
  loading.value = true
  try {
    const r = await fetch(`${API}/api/personas/${selectedPid.value}/scaffold`)
    if (r.ok) {
      const d = await r.json()
      events.value = d.events || []
    }
  } finally { loading.value = false }
}

// ---- 插入 + 后台生成 ----
async function doInsert() {
  if (!parsedEvents.value.length || !selectedPid.value) return
  inserting.value = true
  logsOpen.value = false
  insertJob.value = null
  try {
    const r = await fetch(`${API}/api/personas/${selectedPid.value}/scaffold/insert`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        events: parsedEvents.value,
        strategy: strategy.value,
        payload_mode: payloadMode.value,
        generate: generateAfter.value,
      }),
    })
    const d = await r.json().catch(() => ({}))
    if (!r.ok) {
      ElMessage.error(d.detail || `插入失败 HTTP ${r.status}`)
      return
    }
    const a = d.applied || {}
    ElMessage.success(
      `已写入: 新增 ${a.inserted || 0} / 合并 ${a.updated || 0} / 删除 ${a.deleted || 0}`,
    )
    await load()
    await loadPersonas()
    if (d.job_id) {
      insertJob.value = { ...(d.state || {}), status: 'running', percent: d.state?.percent || 5 }
      pollJob(d.job_id)
    }
  } finally { inserting.value = false }
}

function stopJobPolling() {
  if (jobTimer) { clearInterval(jobTimer); jobTimer = null }
}

function pollJob(jobId) {
  stopJobPolling()
  jobTimer = setInterval(async () => {
    try {
      const r = await fetch(`${API}/api/jobs/${jobId}`)
      if (!r.ok) return
      const st = await r.json()
      insertJob.value = st
      if (st.status !== 'running') {
        stopJobPolling()
        await load()
        await loadPersonas()
        if (st.status === 'done') ElMessage.success(st.message || '生成完成')
        else if (st.status === 'error') ElMessage.error(st.message || '生成失败')
      }
    } catch { /* ignore */ }
  }, 3000)
}

function fillSample() {
  insertText.value = JSON.stringify([
    {
      event_id: 'p0004-e001', person_id: 'p0004', type: '出生',
      start: '1954', end: null,
      payload: JSON.stringify({ 地点: '陕西市区', 性别: '男' }),
      causes: '[]', world_event_ref: null, source: 'scaffold',
      confidence: 1, visibility: '本人知', correction_of: null, schema_version: '1.0',
    },
    {
      event_id: 'p0004-e002', person_id: 'p0004', type: '出生背景',
      start: '1954', end: null,
      payload: JSON.stringify({ 父亲: '工人/小学', 家境: '一般', 宗族: '原子化地区', 当地婚俗: '一般' }),
      causes: '[]', world_event_ref: null, source: 'scaffold',
      confidence: 1, visibility: '家人知', correction_of: null, schema_version: '1.0',
    },
  ], null, 2)
}

onMounted(async () => {
  if (!selectedPid.value && personas.value.length) selectedPid.value = personas.value[0].id
  await loadPersonas()
  await load()
})
onUnmounted(stopJobPolling)
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
.row-head { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; row-gap: 8px; }
.toolbar-spacer { flex: 1; }

/* 左时间轴 / 右插入 */
.split {
  flex: 1; min-height: 0;
  display: grid; grid-template-columns: minmax(0, 62fr) minmax(0, 38fr); gap: 16px;
}
.left-col { min-width: 0; min-height: 0; display: flex; flex-direction: column; }
.right-col { min-width: 0; min-height: 0; }

/* 窄屏 (换屏幕/缩放后可用宽度变小时): 上下堆叠, 右侧面板不再被裁掉 */
@media (max-width: 1100px) {
  .split {
    grid-template-columns: minmax(0, 1fr);
    grid-template-rows: minmax(0, 3fr) minmax(0, 2fr);
  }
  .right-col { min-height: 0; }
}

.toolbar {
  flex-shrink: 0; display: flex; align-items: center; gap: 8px;
  background: #fff; border: 1px solid #e4e7ed; border-radius: 8px;
  padding: 8px 12px; margin-bottom: 12px;
}

/* 时间轴 */
.tl-scroll {
  flex: 1; min-height: 0; overflow-y: auto;
  background: #fff; border: 1px solid #e4e7ed; border-radius: 8px;
  padding: 16px 20px;
}
.tl { padding-left: 4px; }
.tl-card {
  background: #fafcff; border: 1px solid #e4e7ed; border-radius: 8px;
  padding: 10px 14px; cursor: pointer; transition: all 0.15s;
}
.tl-card:hover { border-color: #409eff; box-shadow: 0 2px 8px rgba(64, 158, 255, 0.12); }
.tl-card.done { background: #f0f9eb; border-color: #d1edc4; }
.tlc-head { display: flex; align-items: center; gap: 8px; }
.tlc-title {
  flex: 1; min-width: 0; font-size: 13px;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.tlc-w { font-size: 11px; color: #c0c4cc; flex-shrink: 0; }
.tl-del { color: #c0c4cc; cursor: pointer; font-size: 14px; }
.tl-del:hover { color: #f56c6c; }
.tlc-summary {
  margin: 6px 0 0; font-size: 12px; color: #909399; line-height: 1.7;
  display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden;
}

/* 列表 */
.table-box {
  flex: 1; min-height: 0; background: #fff; border: 1px solid #e4e7ed;
  border-radius: 8px; overflow: hidden; padding: 4px 8px 8px;
}
.table-box :deep(.el-table) { height: 100%; }
.table-box :deep(.el-table__row) { cursor: pointer; }
.year { color: #409eff; }

/* 插入面板 */
.insert-panel {
  height: 100%; overflow-y: auto;
  background: #fff; border: 1px solid #e4e7ed; border-radius: 8px;
  padding: 14px 16px; display: flex; flex-direction: column;
}
.panel-title {
  font-size: 13px; font-weight: 600; color: #303133;
  display: flex; align-items: center; margin-bottom: 10px;
}
.strategy-row { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.mode-label { font-size: 12px; color: #909399; }
.strategy-tip { margin: 8px 0 10px; font-size: 12px; color: #909399; line-height: 1.6; }
.json-input { flex-shrink: 0; }
.json-input :deep(.el-textarea__inner) {
  height: 210px; font-family: Consolas, monospace; font-size: 12px; line-height: 1.6;
}
.parse-row {
  display: flex; align-items: center; gap: 6px; flex-wrap: wrap;
  min-height: 26px; margin: 8px 0;
}
.parse-row .err { color: #f56c6c; font-size: 12px; }
.parse-row .types { font-size: 11px; color: #c0c4cc; }
.parse-row .muted { font-size: 12px; color: #a8abb2; }
.insert-actions { display: flex; align-items: center; gap: 10px; }
.insert-help {
  margin-top: auto; padding-top: 12px; border-top: 1px dashed #ebeef5;
  font-size: 11.5px; color: #a8abb2; line-height: 1.8;
}
.insert-help b { color: #909399; }

.job-box {
  margin-top: 12px; padding: 8px 10px; border-radius: 8px;
  border: 1px solid #e4e7ed; background: #fafcff;
}
.job-box.job-done { border-color: #d1edc4; background: #f0f9eb; }
.job-box.job-error { border-color: #fbc4c4; background: #fef0f0; }
.job-head { display: flex; align-items: center; gap: 6px; margin-bottom: 6px; font-size: 12px; }
.job-msg { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.job-logs {
  margin-top: 6px; max-height: 110px; overflow-y: auto;
  background: #1e1e1e; color: #c8c8c8; border-radius: 6px; padding: 8px 10px;
  font-size: 11px; line-height: 1.6; font-family: Consolas, monospace;
  white-space: pre-wrap; word-break: break-all;
}

/* 详情抽屉 */
.dw-head { display: flex; align-items: center; gap: 8px; }
.sec-label {
  font-size: 12px; font-weight: 600; color: #303133;
  margin: 16px 0 8px; display: flex; align-items: center; gap: 6px;
}
.sec-label .sub { font-weight: 400; font-size: 11px; color: #c0c4cc; }
.sec-text { margin: 0; font-size: 13px; line-height: 1.8; color: #303133; }
.sec-empty {
  font-size: 12px; color: #a8abb2; background: #fafcff;
  border: 1px dashed #d9ecff; border-radius: 6px; padding: 10px 12px;
}
.content-card {
  background: #fafcff; border: 1px solid #e4e7ed; border-radius: 8px;
  padding: 12px 14px; margin-bottom: 8px;
}
.cc-text { margin: 0; font-size: 13px; line-height: 1.9; color: #303133; white-space: pre-wrap; }
.payload {
  margin: 0; background: #1e1e1e; color: #c8c8c8; border-radius: 6px;
  padding: 10px 12px; font-size: 11.5px; line-height: 1.6;
  font-family: Consolas, monospace; white-space: pre-wrap; word-break: break-all;
}
.dw-foot { margin-top: 18px; padding-top: 12px; border-top: 1px dashed #ebeef5; }
</style>
