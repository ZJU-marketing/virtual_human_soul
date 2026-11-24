<template>
  <section class="page fill-page">
    <div class="page-head">
      <h3>人物质检室 · 人物列表</h3>
      <p class="hint">全部人物一览；点击行打开表单式预览，可直接跳转质检/对话/铸造</p>
    </div>

    <!-- ============ 工具栏 ============ -->
    <div class="toolbar">
      <el-input
        v-model="keyword" placeholder="搜索姓名 / slug / 身份自述" size="small"
        style="width: 240px" clearable :prefix-icon="Search"
      />
      <el-select v-model="statusFilter" placeholder="状态" size="small" style="width: 110px" clearable>
        <el-option value="live" label="live" />
        <el-option value="draft" label="draft" />
        <el-option value="archived" label="archived" />
      </el-select>
      <div class="toolbar-spacer" />
      <el-button size="small" :icon="Refresh" @click="load">刷新</el-button>
    </div>

    <!-- ============ 人物列表 ============ -->
    <div class="table-box">
      <el-table
        :data="filtered" v-loading="loading"
        size="small" height="100%"
        :row-class-name="rowClass"
        @row-click="onRowClick"
      >
        <el-table-column label="人物" min-width="150">
          <template #default="{ row }">
            <span class="name-cell">
              <span class="avatar sm" :style="{ background: avatarColor(row.name) }">{{ (row.name || '人')[0] }}</span>
              <b>{{ row.name }}</b>
            </span>
          </template>
        </el-table-column>
        <el-table-column label="slug" min-width="150" show-overflow-tooltip>
          <template #default="{ row }"><span class="muted">{{ row.slug }}</span></template>
        </el-table-column>
        <el-table-column label="状态" width="80">
          <template #default="{ row }">
            <el-tag :type="row.status === 'live' ? 'success' : 'info'" size="small" effect="plain">
              {{ row.status }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="性格导向" width="90" align="center">
          <template #default="{ row }">
            <el-tag v-if="row.has_personality" size="small" type="success" effect="plain">✓</el-tag>
            <el-tag v-else size="small" type="warning" effect="plain">待生成</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="履历" width="90" align="center">
          <template #default="{ row }">
            <span :class="{ done: row.counts.events && row.counts.events_done === row.counts.events }">
              {{ row.counts.events_done }}/{{ row.counts.events }}
            </span>
          </template>
        </el-table-column>
        <el-table-column label="知识节点" width="80" align="center">
          <template #default="{ row }">{{ row.counts.kb_nodes }}</template>
        </el-table-column>
        <el-table-column label="财务大事" width="80" align="center">
          <template #default="{ row }">{{ row.counts.finance }}</template>
        </el-table-column>
        <el-table-column label="情景记忆" width="80" align="center">
          <template #default="{ row }">{{ row.counts.memories }}</template>
        </el-table-column>
        <el-table-column label="身份自述" min-width="240" show-overflow-tooltip>
          <template #default="{ row }"><span class="muted">{{ row.identity_head || '—' }}</span></template>
        </el-table-column>
        <el-table-column label="创建时间" width="150">
          <template #default="{ row }">{{ fmtDate(row.created_at) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="130" align="center" fixed="right">
          <template #default="{ row }">
            <div class="op-cell">
              <span class="op-link green" @click.stop="goTimeline(row)">时间线</span>
              <el-dropdown trigger="click" @command="(cmd) => onCommand(cmd, row)">
                <span class="op-link" @click.stop>
                  更多<el-icon class="op-caret"><ArrowDown /></el-icon>
                </span>
                <template #dropdown>
                  <el-dropdown-menu>
                    <el-dropdown-item command="preview">预览</el-dropdown-item>
                    <el-dropdown-item command="check" divided>去质检</el-dropdown-item>
                    <el-dropdown-item command="edit">去修改</el-dropdown-item>
                    <el-dropdown-item command="simulate">去对话</el-dropdown-item>
                    <el-dropdown-item command="forge" divided>去铸造/重跑</el-dropdown-item>
                  </el-dropdown-menu>
                </template>
              </el-dropdown>
            </div>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <!-- ============ 表单式预览抽屉 ============ -->
    <el-drawer v-model="drawerOpen" direction="rtl" size="46%" :with-header="false">
      <template v-if="selected">
        <div class="dw-head">
          <span class="avatar lg" :style="{ background: avatarColor(selected.name) }">{{ (selected.name || '人')[0] }}</span>
          <div class="dw-title">
            <b>{{ selected.name }}</b>
            <span class="dw-sub">{{ selected.slug }} · {{ selected.status }} · {{ fmtDate(selected.created_at) }}</span>
          </div>
          <div class="toolbar-spacer" />
          <el-button size="small" :icon="Refresh" text @click="load">刷新</el-button>
        </div>

        <el-form label-position="top" size="small" class="dw-form">
          <el-form-item label="身份自述 (core_identity)">
            <el-input v-model="form.identity" type="textarea" :rows="7" readonly />
          </el-form-item>
          <el-form-item label="人生骨架 (bio_skeleton · 节点索引)">
            <el-input v-model="form.skeleton" type="textarea" :rows="5" readonly />
          </el-form-item>
          <el-form-item label="性格导向 (personality)">
            <el-input v-model="form.personality" type="textarea" :rows="5" readonly
                      :placeholder="selected.has_personality ? '' : '尚未生成，去铸造页运行渐进式生成'" />
          </el-form-item>

          <el-form-item label="数据统计">
            <div class="stat-grid">
              <div class="stat"><b>{{ selected.counts.events }}</b><span>履历事件</span></div>
              <div class="stat"><b>{{ selected.counts.events_done }}</b><span>已消化</span></div>
              <div class="stat"><b>{{ selected.counts.event_contents }}</b><span>履历展开</span></div>
              <div class="stat"><b>{{ selected.counts.kb_nodes }}</b><span>知识节点</span></div>
              <div class="stat"><b>{{ selected.counts.finance }}</b><span>财务大事</span></div>
              <div class="stat"><b>{{ selected.counts.memories }}</b><span>情景记忆</span></div>
            </div>
          </el-form-item>
        </el-form>

        <div class="dw-actions">
          <el-button size="small" type="primary" @click="goCheck">去质检</el-button>
          <el-button size="small" type="success" @click="goTimelineSel">查看时间线</el-button>
          <el-button size="small" @click="goEdit">去修改</el-button>
          <el-button size="small" @click="goSimulate">去对话</el-button>
          <el-button size="small" @click="goForge">去铸造/重跑</el-button>
        </div>
      </template>
    </el-drawer>
  </section>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import { ArrowDown, Refresh, Search } from '@element-plus/icons-vue'
import { API, inspectTarget, chatTarget, loadPersonas } from '../store'

const router = useRouter()
const rows = ref([])
const loading = ref(false)
const keyword = ref('')
const statusFilter = ref('')
const selected = ref(null)
const form = ref({ identity: '', skeleton: '', personality: '' })
let timer = null

const drawerOpen = computed({
  get: () => selected.value != null,
  set: (v) => { if (!v) selected.value = null },
})

const filtered = computed(() => {
  const kw = keyword.value.trim().toLowerCase()
  return rows.value.filter((p) => {
    if (statusFilter.value && p.status !== statusFilter.value) return false
    if (!kw) return true
    return (
      (p.name || '').toLowerCase().includes(kw)
      || (p.slug || '').toLowerCase().includes(kw)
      || (p.identity || '').toLowerCase().includes(kw)
    )
  })
})

const rowClass = ({ row }) => (selected.value?.id === row.id ? 'row-active' : '')

function onRowClick(row) { openDetail(row) }

function avatarColor(name) {
  const colors = ['#409eff', '#67c23a', '#e6a23c', '#f56c6c', '#909399', '#7c4dff']
  let h = 0
  for (const ch of name || '') h = (h * 31 + ch.charCodeAt(0)) % 1000
  return colors[h % colors.length]
}

function fmtDate(iso) {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  const p = (n) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`
}

/** 选中人物后拉取骨架/性格全文做表单预览 */
async function openDetail(p) {
  selected.value = p
  form.value = {
    identity: p.identity || '',
    skeleton: '',
    personality: p.personality_head || '',
  }
  try {
    const r = await fetch(`${API}/api/personas/${p.id}/scaffold`)
    if (!r.ok) return
    const d = await r.json()
    if (selected.value?.id !== p.id) return
    form.value.identity = d.persona?.core_identity || p.identity || ''
    form.value.skeleton = d.persona?.bio_skeleton || ''
    form.value.personality = d.persona?.personality || ''
  } catch { /* ignore */ }
}

async function load() {
  loading.value = true
  try {
    const r = await fetch(`${API}/api/personas/overview`)
    if (r.ok) {
      rows.value = await r.json()
      if (selected.value) {
        const cur = rows.value.find((x) => x.id === selected.value.id)
        if (cur) await openDetail(cur)
        else selected.value = null
      }
    }
  } finally { loading.value = false }
}

function pickTarget(refObj, path) {
  if (!selected.value) return
  refObj.value = selected.value.id
  drawerOpen.value = false
  router.push(path)
}

function pickAndGo(row, refObj, path) {
  refObj.value = row.id
  selected.value = null
  router.push(path)
}

function goTimeline(row) {
  router.push(`/inspect/time-line/${row.id}`)
}

function onCommand(cmd, row) {
  if (cmd === 'preview') { openDetail(row); return }
  if (cmd === 'forge') { goForgeRow(row); return }
  const target = cmd === 'simulate' ? chatTarget : inspectTarget
  pickAndGo(row, target, cmd === 'simulate' ? '/simulate' : `/inspect/${cmd}`)
}

function goForgeRow(row) {
  router.push({ path: '/forge', query: { persona: row.id } })
}

const goCheck = () => pickTarget(inspectTarget, '/inspect/check')
const goTimelineSel = () => {
  if (!selected.value) return
  drawerOpen.value = false
  router.push(`/inspect/time-line/${selected.value.id}`)
}
const goEdit = () => pickTarget(inspectTarget, '/inspect/edit')
const goSimulate = () => pickTarget(chatTarget, '/simulate')
const goForge = () => {
  if (!selected.value) return
  drawerOpen.value = false
  router.push({ path: '/forge', query: { persona: selected.value.id } })
}

onMounted(async () => {
  await loadPersonas()
  await load()
  timer = setInterval(load, 10000)   // 轻量轮询, 后台任务变化时统计会更新
})
onUnmounted(() => { if (timer) clearInterval(timer) })
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

/* 人物列表 */
.table-box {
  flex: 1; min-height: 0; background: #fff; border: 1px solid #e4e7ed;
  border-radius: 8px; overflow: hidden; padding: 4px 8px 8px;
}
.table-box :deep(.el-table) { height: 100%; }
.table-box :deep(.row-active) { background: #f5f9ff; }
.table-box :deep(.el-table__row) { cursor: pointer; }
.name-cell { display: inline-flex; align-items: center; gap: 8px; }
.op-cell {
  display: flex; align-items: center; justify-content: center;
  gap: 14px; white-space: nowrap; line-height: 1;
}
.op-cell :deep(.el-dropdown) {
  display: inline-flex; align-items: center; line-height: 1;
}
.op-link {
  display: inline-flex; align-items: center; gap: 2px;
  font-size: 12px; line-height: 1; color: #606266; cursor: pointer;
}
.op-link.green { color: #67c23a; }
.op-link:hover { opacity: 0.75; }
.op-caret { font-size: 10px; }
.muted { color: #909399; }
span.done { color: #67c23a; font-weight: 600; }
.avatar.sm {
  width: 26px; height: 26px; border-radius: 50%; color: #fff;
  display: grid; place-items: center; font-size: 12px; font-weight: 600; flex-shrink: 0;
}
.avatar.lg { width: 44px; height: 44px; font-size: 19px; }
.avatar {
  width: 34px; height: 34px; border-radius: 50%; color: #fff;
  display: grid; place-items: center; font-size: 15px; font-weight: 600; flex-shrink: 0;
}

/* 抽屉表单预览 */
.dw-head { display: flex; align-items: center; gap: 12px; margin-bottom: 16px; }
.dw-title { display: flex; flex-direction: column; }
.dw-title b { font-size: 16px; }
.dw-sub { font-size: 12px; color: #909399; }
.dw-form :deep(.el-form-item__label) { font-weight: 600; color: #303133; }
.dw-form :deep(.el-textarea__inner) {
  background: #fafcff; color: #303133; font-size: 12.5px; line-height: 1.75;
}
.stat-grid {
  width: 100%; display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px;
}
.stat {
  background: #f5f7fa; border-radius: 8px; padding: 10px;
  display: flex; flex-direction: column; align-items: center; gap: 2px;
}
.stat b { font-size: 18px; color: #409eff; }
.stat span { font-size: 11px; color: #909399; }
.dw-actions {
  margin-top: 16px; padding-top: 14px; border-top: 1px dashed #ebeef5;
  display: flex; gap: 8px;
}
</style>
