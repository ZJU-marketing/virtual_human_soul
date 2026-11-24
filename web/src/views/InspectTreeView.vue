<template>
  <section class="page fill-page">
    <div class="page-head">
      <h3>人物质检室 · 知识图谱</h3>
      <p class="hint">relation-graph 渲染知识图谱（树边实线 + 联想边虚线），点击节点查看详情</p>
    </div>

    <el-alert v-if="!inspectTarget" type="info" :closable="false" title="先在下方选择人物" show-icon />

    <template v-else>
      <!-- ============ 主体: 知识图谱 (自适应高度) ============ -->
      <div class="graph-box fill-graph">
        <RelationGraph v-if="graphNodes.length" ref="graphRef" :options="graphOptions">
          <template #node="{ node }">
            <div
              class="rg-node" :class="{ active: nodeDetail && node.id === nodeDetail.id }"
              :style="{ background: nodeFill(node.data.node_type) }"
              @click="jumpNode(node.id)"
            >
              <el-tag :type="typeTag(node.data.node_type)" size="small" effect="plain" class="rg-tag">
                {{ typeLabel(node.data.node_type) }}
              </el-tag>
              <div class="rg-title">{{ node.text }}</div>
              <div class="rg-imp">imp {{ node.data.importance }}</div>
            </div>
          </template>
        </RelationGraph>
        <el-skeleton v-else-if="graphLoading" :rows="6" animated style="padding: 24px" />
        <el-empty v-else description="该人物暂无知识节点" :image-size="80" />
      </div>

      <!-- ============ 图谱下方: 操作栏 (人物选择等) ============ -->
      <div class="toolbar">
        <span class="picker-label">人物:</span>
        <el-select v-model="inspectTarget" placeholder="选择人物" style="width: 280px" @change="onPersonaChange" clearable>
          <el-option v-for="p in personas" :key="p.id" :value="p.id" :label="`${p.name} (${p.slug})`" />
        </el-select>
        <el-button text @click="loadPersonas">刷新列表</el-button>

        <div class="toolbar-spacer" />

        <el-button
          v-if="nodeDetail" type="primary" plain @click="detailOpen = true"
        >查看选中: {{ nodeDetail.title.slice(0, 10) }}…</el-button>
        <el-tag v-else type="info" effect="plain" size="small">点击图谱节点查看详情</el-tag>
      </div>
    </template>

    <!-- ============ 节点详情抽屉 ============ -->
    <el-drawer v-model="detailOpen" direction="rtl" size="44%" :append-to-body="true">
      <template #header>
        <div class="drawer-head">
          <el-tag :type="typeTag(nodeDetail?.node_type)" size="small">{{ typeLabel(nodeDetail?.node_type) }}</el-tag>
          <b>{{ nodeDetail?.title }}</b>
        </div>
      </template>
      <template v-if="nodeDetail">
        <div class="detail-meta">
          <el-tag size="small" type="info">重要度 {{ nodeDetail.importance }}</el-tag>
          <el-tag size="small" type="info">披露度 {{ nodeDetail.disclosure_level }}</el-tag>
          <el-tag v-if="nodeDetail.access_count" size="small" type="warning">被想起 {{ nodeDetail.access_count }} 次</el-tag>
        </div>
        <p class="summary">{{ nodeDetail.summary }}</p>

        <el-divider content-position="left">内容块 ({{ nodeDetail.contents.length }})</el-divider>
        <div v-for="c in nodeDetail.contents" :key="c.id" class="content-block">
          <div class="cb-head">
            <b>{{ c.seq }}. {{ c.label || '(无标题)' }}</b>
            <span class="cb-meta">
              {{ c.token_estimate }} tokens
              <template v-if="c.valid_from"> · 生效 {{ c.valid_from }}</template>
              <template v-if="c.valid_to"> · 封口 {{ c.valid_to }}</template>
            </span>
          </div>
          <p class="cb-text">{{ c.content }}</p>
        </div>

        <template v-if="nodeDetail.clues.length">
          <el-divider content-position="left">联想线索 ({{ nodeDetail.clues.length }})</el-divider>
          <div v-for="(cl, i) in nodeDetail.clues" :key="i" class="clue" @click="jumpNode(cl.node_id)">
            <span class="arrow">{{ cl.direction === 'outgoing' ? '→' : '←' }}</span>
            <el-tag size="small" :type="clueTag(cl.edge_type)">{{ clueLabel(cl.edge_type) }}</el-tag>
            <span class="clue-title">{{ cl.title }}</span>
            <span class="clue-w">w={{ cl.weight.toFixed(2) }}</span>
          </div>
        </template>
      </template>
    </el-drawer>
  </section>
</template>

<script setup>
import { ref, watch, onMounted, nextTick } from 'vue'
import RelationGraph from 'relation-graph-vue3'
import { API, personas, inspectTarget, loadPersonas } from '../store'

// ---- 详情抽屉 ----
const nodeDetail = ref(null)
const detailOpen = ref(false)

const TYPE_MAP = {
  chapter: ['章节', 'primary'],
  semantic: ['观念', 'success'],
  procedural: ['习惯', 'warning'],
  identity: ['身份', 'danger'],
}
const typeLabel = (t) => TYPE_MAP[t]?.[0] || t
const typeTag = (t) => TYPE_MAP[t]?.[1] || 'info'
const nodeFill = (t) => ({ chapter: '#ecf5ff', semantic: '#f0f9eb', procedural: '#fdf6ec', identity: '#fef0f0' }[t] || '#f5f7fa')

const CLUE_MAP = {
  related: ['相关', 'primary', '#409eff'],
  causes: ['引发', 'danger', '#f56c6c'],
  contradicts: ['矛盾', 'danger', '#f56c6c'],
  elaborates: ['展开', 'success', '#67c23a'],
  part_of: ['属于', 'info', '#909399'],
}
const clueLabel = (t) => CLUE_MAP[t]?.[0] || t
const clueTag = (t) => CLUE_MAP[t]?.[1] || 'info'
const edgeColor = (t) => CLUE_MAP[t]?.[2] || '#909399'

function truncate(s, n = 14) { return s.length > n ? s.slice(0, n - 1) + '…' : s }

// ---- relation-graph ----
const graphRef = ref(null)
const graphNodes = ref([])
const graphEdges = ref([])
const graphLoading = ref(false)

const graphOptions = {
  debug: false,
  moveToCenter: true,
  zoomToFit: true,
  allowSwitchLineShape: true,
  allowShowMiniToolBar: true,
  defaultNodeBorderWidth: 0,
  defaultNodeColor: 'rgba(0,0,0,0)',
  defaultLineMarker: { markerWidth: 12, markerHeight: 12 },
  layout: { layoutName: 'tree', from: 'top', min_per_width: 160, max_per_width: 220 },
  defaultJunctionPoint: 'border',
}

async function onPersonaChange() {
  nodeDetail.value = null
  detailOpen.value = false
  graphNodes.value = []
  graphEdges.value = []
  if (!inspectTarget.value) return
  await loadGraph()
}

async function loadGraph() {
  graphLoading.value = true
  try {
    const r = await fetch(`${API}/api/personas/${inspectTarget.value}/graph`)
    if (!r.ok) return
    const g = await r.json()
    graphNodes.value = g.nodes || []
    graphEdges.value = g.edges || []
    await nextTick()
    renderGraph()
  } finally { graphLoading.value = false }
}

function renderGraph() {
  const instance = graphRef.value?.getInstance()
  if (!instance) return

  instance.setJsonData({
    rootId: rootIdOf(),
    nodes: graphNodes.value.map((n) => ({
      id: n.id,
      text: truncate(n.title),
      data: { node_type: n.node_type, importance: n.importance },
      nodeShape: 0,
      width: 150,
      height: 64,
    })),
    lines: graphEdges.value.map((e) => ({
      from: e.source,
      to: e.target,
      text: e.type === 'tree' ? '' : clueLabel(e.type),
      color: e.type === 'tree' ? '#c0c4cc' : edgeColor(e.type),
      lineWidth: e.type === 'tree' ? 1.5 : 1 + (e.weight || 0.5) * 2,
      lineShape: e.type === 'tree' ? 1 : 6,
      style: e.type === 'tree'
        ? { stroke: '#c0c4cc', strokeWidth: 1.5 }
        : { stroke: edgeColor(e.type), strokeWidth: 1 + (e.weight || 0.5) * 2, strokeDasharray: '6 4' },
      useTextPath: true,
    })),
  }, { append: false })
}

function rootIdOf() {
  const targets = new Set(graphEdges.value.filter((e) => e.type === 'tree').map((e) => e.target))
  const root = graphNodes.value.find((n) => !targets.has(n.id))
  return root?.id || graphNodes.value[0]?.id
}

async function jumpNode(nodeId) {
  const r = await fetch(`${API}/api/personas/${inspectTarget.value}/nodes/${nodeId}`)
  if (!r.ok) return
  nodeDetail.value = await r.json()
  detailOpen.value = true   // 选中后弹详情抽屉
}

watch(inspectTarget, onPersonaChange)
onMounted(() => { if (inspectTarget.value) onPersonaChange() })
</script>

<style scoped>
.fill-page { display: flex; flex-direction: column; min-height: calc(100vh - 56px - 36px); padding-bottom: 12px; }
.page-head { flex-shrink: 0; }

/* 图谱: 主体, 弹性占满 */
.fill-graph {
  flex: 1; min-height: 320px;
  background: #fff; border: 1px solid #e4e7ed; border-radius: 8px;
  overflow: hidden; margin-bottom: 12px;
}

/* 操作栏 (图谱下方) */
.toolbar {
  flex-shrink: 0;
  display: flex; align-items: center; gap: 12px;
  background: #fff; border: 1px solid #e4e7ed; border-radius: 8px;
  padding: 10px 14px;
}
.toolbar-spacer { flex: 1; }
.picker-label { font-size: 13px; color: #606266; }

/* 自定义节点插槽 */
.rg-node {
  width: 150px; height: 64px; border-radius: 8px; border: 2px solid #fff;
  display: flex; flex-direction: column; align-items: center; justify-content: center;
  cursor: pointer; gap: 2px; box-shadow: 0 1px 4px rgba(0,0,0,0.08);
  transition: 0.15s;
}
.rg-node:hover { transform: translateY(-1px); box-shadow: 0 3px 8px rgba(0,0,0,0.14); }
.rg-node.active { border-color: #409eff; box-shadow: 0 0 0 3px rgba(64,158,255,0.25); }
.rg-tag { transform: scale(0.85); }
.rg-title { font-size: 12px; color: #303133; text-align: center; line-height: 1.25; padding: 0 4px; }
.rg-imp { font-size: 10px; color: #c0c4cc; }

/* 详情抽屉 */
.drawer-head { display: flex; align-items: center; gap: 8px; }
.detail-meta { display: flex; gap: 6px; flex-wrap: wrap; margin-bottom: 10px; }
.summary { color: #606266; font-size: 13px; background: #f5f7fa; padding: 8px 12px; border-radius: 6px; }

.content-block { margin-bottom: 14px; }
.cb-head { display: flex; justify-content: space-between; font-size: 13px; margin-bottom: 4px; }
.cb-meta { color: #c0c4cc; font-size: 12px; }
.cb-text { font-size: 13px; line-height: 1.7; color: #303133; white-space: pre-wrap; background: #fafafa; padding: 10px 12px; border-radius: 6px; }

.clue {
  display: flex; align-items: center; gap: 8px; padding: 6px 10px; border-radius: 6px;
  cursor: pointer; font-size: 13px;
}
.clue:hover { background: #f5f7fa; }
.clue .arrow { color: #909399; }
.clue-title { flex: 1; }
.clue-w { color: #c0c4cc; font-size: 12px; }

:deep(.el-divider__text) { font-size: 13px; color: #909399; }
</style>
