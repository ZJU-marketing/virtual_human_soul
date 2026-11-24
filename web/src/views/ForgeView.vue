<template>
  <section class="page fill-page">
    <div class="page-head">
      <h3>血肉铸造台 · 性格导向驱动</h3>
      <p class="hint">
        只需粘贴人生履历并入库：LangGraph 节点 1 读履历生成人物骨架，节点 2 归纳性格导向，
        再以性格为导向按年份逐批生成血肉。骨架与性格导向均可人工修订后重新生成。
      </p>
    </div>

    <!-- ============ 顶部状态栏 ============ -->
    <div class="toolbar">
      <el-select
        v-if="!personaId"
        v-model="selectedPersonaId" placeholder="载入已有的人物重新跑" size="small"
        style="width: 200px" filterable :disabled="busy"
        @change="onSelectPersona"
      >
        <el-option v-for="p in personas" :key="p.id" :label="p.name" :value="p.id" />
      </el-select>
      <span v-if="!personaId" class="sep">|</span>
      <template v-if="personaId">
        <el-tag type="success" effect="plain" size="small">人物: {{ personaName }}</el-tag>
        <el-tag size="small" type="info">slug: {{ personaSlug }}</el-tag>
        <span class="sep">|</span>
        <el-tag size="small" :type="skeletonText ? 'success' : 'warning'" effect="plain">
          骨架 {{ skeletonText ? '已生成' : '待节点1生成' }}
        </el-tag>
        <el-tag size="small" :type="personality ? 'success' : 'warning'" effect="plain">
          性格导向 {{ personality ? `已生成 ${personality.length} 字` : '待节点2生成' }}
        </el-tag>
        <el-tag size="small" type="warning" effect="plain">
          事件 {{ stats.processed }}/{{ stats.total }}
        </el-tag>
        <span v-if="stats.year_from" class="years">{{ stats.year_from }} – {{ stats.year_to }}</span>
      </template>
      <el-tag v-else type="info" effect="plain" size="small">尚未创建人物</el-tag>

      <div class="toolbar-spacer" />
      <el-button
        v-if="personaId" text size="small" :icon="SwitchButton"
        :disabled="busy" @click="exitPersona"
      >退出载入</el-button>
      <el-button v-if="personaId" text size="small" :icon="RefreshLeft" @click="resetAll">重新开始</el-button>
      <el-button text :icon="List" @click="$router.push('/forge/jobs')">查看任务列表</el-button>
    </div>

    <!-- ============ 进度条 ============ -->
    <div v-if="showTask" class="task-progress" :class="`tp-${task.status}`">
      <div class="tp-head">
        <el-icon v-if="task.status === 'running'" class="is-loading"><Loading /></el-icon>
        <el-icon v-else-if="task.status === 'done'" color="#67c23a"><CircleCheck /></el-icon>
        <el-icon v-else color="#f56c6c"><CircleClose /></el-icon>
        <b>生成{{ task.status === 'running' ? '中' : task.status === 'done' ? '完成' : '失败' }}</b>
        <span class="tp-msg" :title="task.message">{{ task.message }}</span>
        <el-button
          v-if="task.logs?.length && task.status !== 'running'"
          link size="small" @click="logsOpen = !logsOpen"
        >{{ logsOpen ? '隐藏日志' : '展开日志' }}</el-button>
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

    <!-- ============ 主体: 左生成结果 / 右履历输入 ============ -->
    <div class="split fill-split">
      <!-- 左: 骨架 + 性格导向 (均为 agent 生成) -->
      <div class="left-panel">
        <!-- ① 人物骨架 -->
        <div class="sub-panel skeleton-part">
          <div class="panel-title">
            ① 人物骨架
            <el-tag v-if="skeletonText" size="small" type="success" effect="plain">{{ skeletonText.length }} 字</el-tag>
            <el-tag v-else size="small" type="warning" effect="plain">待节点1生成</el-tag>
            <div class="title-spacer" />
            <el-button
              text size="small" :icon="Refresh" :loading="generatingSkeleton"
              :disabled="!personaId || stats.total === 0 || busy"
              @click="doRegenerateSkeleton"
            >重新生成</el-button>
            <el-button v-if="!editingSkeleton" text size="small" :icon="EditPen"
                       :disabled="!personaId || busy" @click="startEditSkeleton">编辑</el-button>
            <template v-else>
              <el-button text size="small" type="primary" :loading="savingSkeleton" @click="saveSkeleton">保存</el-button>
              <el-button text size="small" @click="cancelEditSkeleton">取消</el-button>
            </template>
          </div>

          <div v-if="!personaId" class="auto-placeholder">
            <el-icon><User /></el-icon>
            <p>无需手填。<b>节点 1</b> 会读取右侧人生履历，自动归纳姓名、出生、籍贯、家庭、职业轨迹与关键经历。</p>
          </div>
          <template v-else>
            <el-input
              v-model="skeletonDraft" type="textarea" resize="none"
              :readonly="!editingSkeleton"
              class="grow-input" :class="{ readonly: !editingSkeleton }"
              placeholder="暂无骨架。点击右上角「重新生成」，或运行渐进式生成由节点 1 自动归纳。"
            />
            <div v-if="runningAction === 'skeleton' && streamTail" class="stream-box">
              <div class="stream-label"><el-icon class="is-loading"><Loading /></el-icon> agent 正在归纳人物骨架…</div>
              <pre class="stream-text">{{ streamTail }}</pre>
            </div>
            <div class="panel-foot tight">
              <span v-if="stats.total === 0" class="muted-hint">需先导入履历</span>
              <span v-else-if="editingSkeleton" class="muted-hint">修改后可保存，作为后续生成的人物设定</span>
              <span v-else-if="skeletonText" class="done-hint">✓ 生成记忆时会作为人物设定注入</span>
              <span v-else class="muted-hint">运行渐进式生成时自动补齐</span>
            </div>
          </template>
        </div>

        <div class="panel-divider" />

        <!-- ② 性格导向提示词 -->
        <div class="sub-panel personality-part">
          <div class="panel-title">
            ② 性格导向提示词
            <el-tag v-if="personality" size="small" type="success" effect="plain">{{ personality.length }} 字</el-tag>
            <el-tag v-else size="small" type="warning" effect="plain">待节点2生成</el-tag>
            <div class="title-spacer" />
            <el-button
              text size="small" :icon="Refresh" :loading="generatingPersonality"
              :disabled="!personaId || stats.total === 0 || busy"
              @click="doRegeneratePersonality"
            >重新生成</el-button>
            <el-button v-if="!editingPersonality" text size="small" :icon="EditPen"
                       :disabled="!personaId || busy" @click="startEditPersonality">编辑</el-button>
            <template v-else>
              <el-button text size="small" type="primary" :loading="savingPersonality" @click="savePersonality">保存</el-button>
              <el-button text size="small" @click="cancelEditPersonality">取消</el-button>
            </template>
          </div>

          <div v-if="!personaId" class="auto-placeholder">
            <el-icon><MagicStick /></el-icon>
            <p>无需手填。<b>节点 2</b> 会依据骨架与全部履历，归纳性格底色、价值观、说话方式、行为模式与时代烙印。</p>
          </div>
          <template v-else>
            <el-input
              v-model="personalityDraft" type="textarea" resize="none" class="grow-input"
              :readonly="!editingPersonality"
              :class="{ readonly: !editingPersonality }"
              placeholder="暂无性格导向。点击右上角「重新生成」，或运行渐进式生成由节点 2 自动归纳。"
            />
            <div v-if="runningAction === 'personality' && streamTail" class="stream-box">
              <div class="stream-label"><el-icon class="is-loading"><Loading /></el-icon> agent 正在归纳性格导向…</div>
              <pre class="stream-text">{{ streamTail }}</pre>
            </div>
            <div class="panel-foot tight">
              <span v-if="editingPersonality" class="muted-hint">编辑后可保存，作为后续生成的统一语气</span>
              <span v-else-if="personality" class="done-hint">✓ 生成记忆时会注入此导向</span>
              <span v-else class="muted-hint">运行渐进式生成时自动补齐</span>
            </div>
          </template>
        </div>
      </div>

      <!-- 右: 人生履历 JSON -->
      <div class="right-panel">
        <div class="panel-title">
          ③ 人生履历 JSON
          <el-tag size="small" type="info">{{ parsedEvents.length }} 条</el-tag>
          <div class="title-spacer" />
          <el-button text size="small" :icon="DocumentCopy" @click="fillSample">示例</el-button>
        </div>
        <el-input
          v-model="eventsJson" type="textarea" resize="none" class="grow-input mono"
          :disabled="importing || busy"
          placeholder='粘贴事件数组: [{"type":"生育","start":"1981","payload":"{\"性别\":\"女\"}",...}, ...]'
        />
        <div v-if="eventsParseError" class="parse-error">{{ eventsParseError }}</div>

        <div class="panel-foot">
          <el-button
            v-if="!personaId"
            type="primary" :icon="Upload" :loading="creating"
            :disabled="!parsedEvents.length || !!eventsParseError"
            @click="doCreate"
          >创建人物并入库</el-button>
          <template v-else>
            <el-button
              :icon="Upload" :loading="importing"
              :disabled="!parsedEvents.length || !!eventsParseError || busy"
              @click="doImport"
            >覆盖导入履历</el-button>
            <el-button
              type="success" :icon="MagicStick" :loading="starting"
              :disabled="stats.total === 0 || busy"
              @click="doGenerate"
            >渐进式生成 ({{ stats.pending }} 待处理)</el-button>
          </template>
          <span v-if="personaId && stats.total" class="done-hint">✓ 已入库 {{ stats.total }} 条</span>
          <span v-else-if="!personaId && parsedEvents.length" class="muted-hint">创建后骨架与性格由 agent 生成</span>
        </div>

        <div v-if="personaId && !skeletonText && stats.total > 0 && !busy" class="ready-hint">
          <el-icon><InfoFilled /></el-icon>
          运行渐进式生成：节点 1 生成骨架 → 节点 2 归纳性格 → 再逐批生成血肉。
        </div>
        <div v-else-if="!personaId" class="ready-hint">
          <el-icon><InfoFilled /></el-icon>
          左侧骨架与性格导向无需填写，创建人物后由 agent 从履历生成。
        </div>
      </div>
    </div>

    <!-- ============ 事件列表 (按年份) ============ -->
    <div v-if="personaId && events.length" class="events-card">
      <div class="panel-title">
        ④ 履历消化进度 (按年份)
        <el-tag size="small" type="success" effect="plain">{{ stats.processed }} 已生成</el-tag>
        <el-tag size="small" type="warning" effect="plain">{{ stats.pending }} 待处理</el-tag>
      </div>
      <div class="event-list">
        <div
          v-for="e in events" :key="e.id"
          class="event-row" :class="{ done: e.processed, sensitive: e.sensitive }"
        >
          <span class="ev-year">{{ e.start_year || e.start_raw || '—' }}</span>
          <el-tag size="small" effect="plain" :type="e.processed ? 'success' : 'info'">{{ e.event_type }}</el-tag>
          <span class="ev-payload">{{ payloadText(e.payload) }}</span>
          <el-tag v-if="e.sensitive" size="small" type="warning" effect="light">敏感</el-tag>
          <el-icon v-if="e.processed && !e.sensitive" color="#67c23a"><CircleCheck /></el-icon>
          <el-icon v-else-if="e.sensitive" color="#e6a23c"><WarningFilled /></el-icon>
          <el-icon v-else color="#c0c4cc"><Clock /></el-icon>
        </div>
      </div>
    </div>
  </section>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  MagicStick, List, Loading, CircleCheck, CircleClose, User,
  Upload, DocumentCopy, Clock, EditPen, Refresh, RefreshLeft, InfoFilled,
  SwitchButton, WarningFilled,
} from '@element-plus/icons-vue'
import { API, loadPersonas, personas } from '../store'

const eventsJson = ref('')
const personaId = ref(null)
const selectedPersonaId = ref(null)
const personaSlug = ref('')
const personaName = ref('')
const skeletonText = ref('')
const personality = ref('')
const stats = ref({ total: 0, processed: 0, pending: 0, year_from: null, year_to: null })
const events = ref([])

const creating = ref(false)
const importing = ref(false)
const starting = ref(false)
const logsOpen = ref(false)

const editingSkeleton = ref(false)
const skeletonDraft = ref('')
const savingSkeleton = ref(false)
const editingPersonality = ref(false)
const personalityDraft = ref('')
const savingPersonality = ref(false)
const generatingPersonality = ref(false)
const generatingSkeleton = ref(false)

const task = ref({ status: 'idle', percent: 0, message: '', logs: [], report: '', payload: {} })
const showTask = computed(() => ['running', 'done', 'error'].includes(task.value.status))
const busy = computed(
  () => task.value.status === 'running' || creating.value || importing.value || starting.value
    || generatingSkeleton.value || generatingPersonality.value
    || savingSkeleton.value || savingPersonality.value,
)
const runningAction = computed(
  () => (task.value.status === 'running' ? (task.value.payload?.action || 'scaffold') : ''),
)
const streamTail = computed(() => {
  const t = task.value.report || ''
  return t.length > 900 ? '…' + t.slice(-900) : t
})

const route = useRoute()
const router = useRouter()
let timer = null

const SAMPLE = JSON.stringify([
  {
    type: '生育', start: '1981', end: null,
    payload: JSON.stringify({ 性别: '女', 胎次: 1, 政策属性: '计划内' }),
    causes: '[]', world_event_ref: null, source: 'scaffold',
    confidence: 1, visibility: '本人知', correction_of: null, schema_version: '1.0',
  },
  {
    type: '时代暴露', start: '1981', end: null,
    payload: JSON.stringify({ 事件: '中国女排五连冠', 渠道: 'tv', 切身: false }),
    causes: '[]', world_event_ref: null, source: 'scaffold',
    confidence: 1, visibility: '公开', correction_of: null, schema_version: '1.0',
  },
  {
    type: '升学', start: '1988', end: null,
    payload: JSON.stringify({ 阶段: '初中', 学校: '县一中' }),
    causes: '[]', world_event_ref: null, source: 'scaffold',
    confidence: 1, visibility: '本人知', correction_of: null, schema_version: '1.0',
  },
  {
    type: '婚宴', start: '2012', end: null,
    payload: JSON.stringify({ 事由: '婚礼', 金额: 326 }),
    causes: '[]', world_event_ref: null, source: 'scaffold',
    confidence: 1, visibility: '公开', correction_of: null, schema_version: '1.0',
  },
], null, 2)

/** 前端解析事件 JSON: 支持对象数组或 {events: []} */
const parsedEvents = computed(() => {
  const txt = eventsJson.value.trim()
  if (!txt) return []
  try {
    const data = JSON.parse(txt)
    const arr = Array.isArray(data) ? data : (Array.isArray(data.events) ? data.events : [])
    return arr.filter((x) => x && typeof x === 'object')
  } catch {
    return []
  }
})

const eventsParseError = computed(() => {
  const txt = eventsJson.value.trim()
  if (!txt) return ''
  try {
    JSON.parse(txt)
    return parsedEvents.value.length ? '' : '事件必须是对象数组'
  } catch (e) {
    return `JSON 解析失败: ${e.message}`
  }
})

function payloadText(p) {
  if (p == null) return '—'
  const s = typeof p === 'string' ? p : JSON.stringify(p)
  return s.length > 70 ? s.slice(0, 70) + '…' : s
}

function fillSample() { eventsJson.value = SAMPLE }

/** 把库里的履历事件还原成导入 JSON, 便于查看/修改后重导入 */
function eventsToJson(list) {
  const arr = (list || []).map((e) => ({
    type: e.event_type,
    start: e.start_raw ?? (e.start_year != null ? String(e.start_year) : ''),
    end: e.end_year != null ? String(e.end_year) : null,
    payload: typeof e.payload === 'string' ? e.payload : JSON.stringify(e.payload ?? {}),
    causes: '[]',
    world_event_ref: null,
    source: 'scaffold',
    confidence: 1,
    visibility: e.visibility || '本人知',
    correction_of: null,
    schema_version: '1.0',
  }))
  return JSON.stringify(arr, null, 2)
}

async function onSelectPersona(pid) {
  if (!pid) return
  await loadPersona(pid)
}

async function loadPersona(pid) {
  resetRunState(false)
  personaId.value = pid
  selectedPersonaId.value = pid
  await refreshScaffold()
  eventsJson.value = eventsToJson(events.value)
  ElMessage.success(`已载入人物「${personaName.value}」，可直接渐进式生成/重跑`)
}

async function readError(r, fallback) {
  const err = await r.json().catch(() => ({}))
  return err.detail || `${fallback} HTTP ${r.status}`
}

async function doCreate() {
  if (!parsedEvents.value.length) return
  if (eventsParseError.value) {
    ElMessage.error(eventsParseError.value)
    return
  }
  creating.value = true
  try {
    const r = await fetch(`${API}/api/personas/scaffold`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ skeleton: '', events: parsedEvents.value }),
    })
    if (!r.ok) {
      ElMessage.error(await readError(r, '创建失败'))
      return
    }
    const d = await r.json()
    personaId.value = d.persona_id
    personaSlug.value = d.slug
    personaName.value = d.name
    selectedPersonaId.value = d.persona_id
    await loadPersonas()
    await refreshScaffold()
    ElMessage.success(`人物已创建，履历 ${d.imported} 条已入库`)
  } finally { creating.value = false }
}

async function doImport() {
  if (!personaId.value || !parsedEvents.value.length) return
  if (stats.value.processed > 0) {
    try {
      await ElMessageBox.confirm(
        `覆盖会清空该人物已导入的 ${stats.value.total} 条履历（其中 ${stats.value.processed} 条已生成记忆），并重置消化进度。是否继续？`,
        '覆盖导入履历',
        { type: 'warning', confirmButtonText: '覆盖', cancelButtonText: '取消' },
      )
    } catch { return }
  }
  importing.value = true
  try {
    const r = await fetch(`${API}/api/personas/${personaId.value}/scaffold/import`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ events: parsedEvents.value }),
    })
    if (!r.ok) {
      ElMessage.error(await readError(r, '导入失败'))
      return
    }
    await refreshScaffold()
    ElMessage.success(`已导入 ${parsedEvents.value.length} 条履历`)
  } finally { importing.value = false }
}

async function doGenerate() {
  if (!personaId.value) return
  starting.value = true
  logsOpen.value = false
  try {
    const r = await fetch(`${API}/api/personas/${personaId.value}/scaffold/generate`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ batch_size: 5 }),
    })
    if (!r.ok) {
      ElMessage.error(await readError(r, '启动失败'))
      return
    }
    const d = await r.json()
    task.value = { ...d.state, payload: d.state?.payload || { action: 'scaffold' } }
    startPolling(d.job_id)
  } finally { starting.value = false }
}

/** 骨架 / 性格导向的后台生成通用入口 */
async function startPersonaJob(url, action, flagRef, failLabel) {
  if (!personaId.value) return
  flagRef.value = true
  logsOpen.value = false
  try {
    const r = await fetch(url, { method: 'POST' })
    if (!r.ok) {
      ElMessage.error(await readError(r, failLabel))
      return
    }
    const d = await r.json()
    task.value = { ...d.state, payload: d.state?.payload || { action } }
    startPolling(d.job_id)
  } finally { flagRef.value = false }
}

async function doRegenerateSkeleton() {
  if (!personaId.value) return
  if (skeletonText.value.trim()) {
    try {
      await ElMessageBox.confirm('将读取全部履历重新归纳，覆盖当前人物骨架。是否继续？', '重新生成人物骨架', {
        type: 'warning', confirmButtonText: '重新生成', cancelButtonText: '取消',
      })
    } catch { return }
  }
  await startPersonaJob(
    `${API}/api/personas/${personaId.value}/skeleton/generate`,
    'skeleton', generatingSkeleton, '启动失败',
  )
}

async function doRegeneratePersonality() {
  if (!personaId.value) return
  if (personality.value.trim()) {
    try {
      await ElMessageBox.confirm('将读取全部履历重新归纳，覆盖当前性格导向。是否继续？', '重新生成性格导向', {
        type: 'warning', confirmButtonText: '重新生成', cancelButtonText: '取消',
      })
    } catch { return }
  }
  await startPersonaJob(
    `${API}/api/personas/${personaId.value}/personality/generate`,
    'personality', generatingPersonality, '启动失败',
  )
}

function startEditSkeleton() {
  skeletonDraft.value = skeletonText.value
  editingSkeleton.value = true
}

function cancelEditSkeleton() {
  skeletonDraft.value = skeletonText.value
  editingSkeleton.value = false
}

async function saveSkeleton() {
  if (!personaId.value) return
  if (!skeletonDraft.value.trim()) {
    ElMessage.error('人物骨架不能为空')
    return
  }
  savingSkeleton.value = true
  try {
    const r = await fetch(`${API}/api/personas/${personaId.value}/skeleton`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ skeleton: skeletonDraft.value }),
    })
    if (!r.ok) {
      ElMessage.error(await readError(r, '保存失败'))
      return
    }
    const d = await r.json()
    skeletonText.value = d.skeleton || ''
    skeletonDraft.value = skeletonText.value
    editingSkeleton.value = false
    ElMessage.success('人物骨架已保存')
  } finally { savingSkeleton.value = false }
}

function startEditPersonality() {
  personalityDraft.value = personality.value
  editingPersonality.value = true
}

function cancelEditPersonality() {
  personalityDraft.value = personality.value
  editingPersonality.value = false
}

async function savePersonality() {
  if (!personaId.value) return
  savingPersonality.value = true
  try {
    const r = await fetch(`${API}/api/personas/${personaId.value}/personality`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ personality: personalityDraft.value }),
    })
    if (!r.ok) {
      ElMessage.error(await readError(r, '保存失败'))
      return
    }
    const d = await r.json()
    personality.value = d.personality || ''
    personalityDraft.value = personality.value
    editingPersonality.value = false
    ElMessage.success('性格导向已保存')
  } finally { savingPersonality.value = false }
}

async function refreshScaffold() {
  if (!personaId.value) return
  const r = await fetch(`${API}/api/personas/${personaId.value}/scaffold`)
  if (!r.ok) return
  const d = await r.json()
  stats.value = d.stats
  events.value = d.events || []
  if (d.persona) {
    selectedPersonaId.value = personaId.value
    personaName.value = d.persona.name || personaName.value
    personaSlug.value = d.persona.slug || personaSlug.value
    skeletonText.value = d.persona.core_identity || d.persona.bio_skeleton || ''
    personality.value = d.persona.personality || ''
  }
  if (!editingSkeleton.value) skeletonDraft.value = skeletonText.value
  if (!editingPersonality.value) personalityDraft.value = personality.value
}

function stopPolling() { if (timer) { clearInterval(timer); timer = null } }

function startPolling(jobId) {
  stopPolling()
  timer = setInterval(async () => {
    try {
      const r = await fetch(`${API}/api/jobs/${jobId}`)
      if (!r.ok) return
      const st = await r.json()
      task.value = st
      if (st.status !== 'running') {
        stopPolling()
        await refreshScaffold()
        await loadPersonas()
        if (st.status === 'done') {
          ElMessage.success(st.message || '生成完成')
        } else if (st.status === 'error') {
          ElMessage.error(st.message || '生成失败')
        }
      }
    } catch { /* ignore */ }
  }, 5000)
}

function dismissTask() { task.value = { ...task.value, status: 'idle' } }

function resetRunState(clearAll = true) {
  stopPolling()
  editingSkeleton.value = false
  skeletonDraft.value = ''
  skeletonText.value = ''
  editingPersonality.value = false
  personalityDraft.value = ''
  personality.value = ''
  stats.value = { total: 0, processed: 0, pending: 0, year_from: null, year_to: null }
  events.value = []
  task.value = { status: 'idle', percent: 0, message: '', logs: [], report: '', payload: {} }
  if (clearAll) {
    personaId.value = null
    selectedPersonaId.value = null
    personaSlug.value = ''
    personaName.value = ''
    eventsJson.value = ''
  }
}

function resetAll() { resetRunState(true) }

/** 退出当前载入的人物, 回到空状态 (路由带 ?persona= 时同步清掉) */
function exitPersona() {
  resetRunState(true)
  if (route.query.persona) router.replace({ query: {} })
  ElMessage.info('已退出人物载入')
}

onMounted(async () => {
  await loadPersonas()
  const pid = route.query.persona
  if (pid) await loadPersona(String(pid))
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
  flex-shrink: 0; display: flex; align-items: center; gap: 10px;
  background: #fff; border: 1px solid #e4e7ed; border-radius: 8px;
  padding: 10px 14px; margin-bottom: 12px;
}
.toolbar-spacer { flex: 1; }
.sep { color: #dcdfe6; }
.years { font-size: 12px; color: #909399; }

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
  margin-top: 8px; max-height: 90px; overflow-y: auto;
  background: #1e1e1e; color: #c8c8c8; border-radius: 6px; padding: 8px 10px;
  font-size: 11.5px; line-height: 1.6; font-family: Consolas, monospace;
}
.tp-log { white-space: pre-wrap; word-break: break-all; }

/* 主体: 左生成结果 42% / 右履历 58% */
.fill-split {
  flex: 1 1 auto; min-height: 0;
  display: grid; grid-template-columns: 42fr 58fr; gap: 16px; align-items: stretch;
}
.left-panel, .right-panel {
  min-height: 0; background: #fff; border: 1px solid #e4e7ed; border-radius: 8px;
  padding: 16px; display: flex; flex-direction: column; overflow: hidden;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.04);
}
.sub-panel { display: flex; flex-direction: column; min-height: 0; }
.skeleton-part { flex: 4 1 0; }
.personality-part { flex: 6 1 0; }

.panel-title {
  font-size: 13px; font-weight: 600; color: #303133; margin-bottom: 10px;
  display: flex; align-items: center; gap: 8px; flex-shrink: 0;
}
.title-spacer { flex: 1; }

.grow-input { flex: 1; min-height: 0; }
.grow-input :deep(.el-textarea__inner) {
  height: 100%; font-size: 13px; line-height: 1.8; border-radius: 6px;
}
.grow-input.readonly :deep(.el-textarea__inner) {
  background: #fafcff; color: #303133;
}
.grow-input.mono :deep(.el-textarea__inner) {
  font-family: Consolas, monospace; font-size: 12px; line-height: 1.6;
}

.panel-foot {
  flex-shrink: 0; margin-top: 12px; padding-top: 12px;
  border-top: 1px dashed #ebeef5;
  display: flex; align-items: center; gap: 10px; flex-wrap: wrap;
}
.panel-foot.tight { margin-top: 8px; padding-top: 8px; }
.done-hint { font-size: 12px; color: #67c23a; }
.muted-hint { font-size: 12px; color: #a8abb2; }

.panel-divider {
  flex-shrink: 0; height: 1px; background: #ebeef5;
  margin: 14px -16px 14px;
}

/* 自动生成占位 */
.auto-placeholder {
  flex: 1; min-height: 0; display: flex; flex-direction: column;
  align-items: center; justify-content: center; gap: 10px;
  background: #fafcff; border: 1px dashed #d9ecff; border-radius: 8px;
  color: #909399; padding: 16px; text-align: center;
}
.auto-placeholder .el-icon { font-size: 26px; color: #a0cfff; }
.auto-placeholder p { margin: 0; font-size: 12.5px; line-height: 1.9; }
.auto-placeholder b { color: #409eff; }

.stream-box {
  flex-shrink: 0; margin-top: 8px; background: #f5f9ff;
  border: 1px solid #d9ecff; border-radius: 6px; padding: 6px 10px;
}
.stream-label { font-size: 11.5px; color: #409eff; display: flex; align-items: center; gap: 5px; }
.stream-text {
  margin: 6px 0 0; max-height: 96px; overflow-y: auto;
  font-size: 11.5px; line-height: 1.65; color: #606266;
  white-space: pre-wrap; word-break: break-all; font-family: inherit;
}

.parse-error { flex-shrink: 0; margin-top: 6px; font-size: 12px; color: #f56c6c; }
.ready-hint {
  flex-shrink: 0; margin-top: 8px; display: flex; align-items: center; gap: 6px;
  font-size: 12px; color: #e6a23c; background: #fdf6ec;
  border: 1px solid #faecd8; border-radius: 6px; padding: 6px 10px;
}

/* 事件列表 */
.events-card {
  flex-shrink: 0; margin-top: 12px; background: #fff; border: 1px solid #e4e7ed;
  border-radius: 8px; padding: 14px 16px; max-height: 250px; display: flex; flex-direction: column;
}
.event-list { flex: 1; min-height: 0; overflow-y: auto; }
.event-row {
  display: flex; align-items: center; gap: 10px; padding: 5px 8px;
  border-radius: 6px; font-size: 12.5px;
}
.event-row:hover { background: #f9fafc; }
.event-row.done { opacity: 0.75; }
/* 敏感事件: 黄色底 + 左侧黄色竖条 */
.event-row.sensitive {
  background: #fdf6ec;
  border-left: 3px solid #e6a23c;
  padding-left: 5px;
  opacity: 0.9;
}
.event-row.sensitive .ev-payload { color: #b88230; }
.ev-year { width: 48px; color: #409eff; font-weight: 600; flex-shrink: 0; }
.ev-payload {
  flex: 1; min-width: 0; color: #909399;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
</style>
