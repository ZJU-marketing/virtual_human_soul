<template>
  <section class="page fill-page">
    <div class="page-head">
      <h3>人物质检室 · 检查</h3>
      <p class="hint">左侧为人物档案树，点击节点查看内容详情；质检报告从右侧抽屉弹出（Markdown 渲染）</p>
    </div>

    <!-- ============ 顶部操作栏 ============ -->
    <div class="toolbar">
      <span class="picker-label">受检人物:</span>
      <el-select
        v-model="inspectTarget" placeholder="选择人物" style="width: 280px"
        @change="loadDetail" clearable
      >
        <el-option v-for="p in personas" :key="p.id" :value="p.id" :label="`${p.name} (${p.slug})`" />
      </el-select>
      <el-button text @click="loadPersonas">刷新列表</el-button>

      <div class="toolbar-spacer" />

      <el-button
        type="warning" :loading="checking" :disabled="!inspectTarget"
        @click="doCheck"
      >开始质检</el-button>
      <el-tooltip
        :content="!inspectTarget ? '先选择人物' : (openIssues.length ? `按 ${openIssues.length} 条未处理记录自动修复` : '暂无未处理记录, 无需修复')"
        placement="bottom"
      >
        <span>
          <el-button
            type="danger" :loading="fixing"
            :disabled="!inspectTarget || !openIssues.length"
            @click="doFix"
          >自动修复</el-button>
        </span>
      </el-tooltip>
      <el-button
        v-if="reportText" plain type="info" @click="reportOpen = true"
      >查看报告 ({{ issueCount || '…' }})</el-button>
      <el-badge :value="openIssues.length" :hidden="!openIssues.length" type="danger" :offset="[-2, 2]">
        <el-button plain type="primary" @click="issuesOpen = true">检查记录</el-button>
      </el-badge>
    </div>

    <el-alert v-if="!inspectTarget" type="info" :closable="false" title="先选择受检人物" show-icon />

    <!-- 主体区: 显式条件 (不能用 v-else, 否则会被中间的进度条打断配对) -->
    <template v-if="inspectTarget">
    <!-- ============ 任务进度条 (质检 / 自动修复, 后台轮询) ============ -->
    <div v-if="activeTask" class="fix-progress" :class="`fp-${activeTask.status}`">
      <div class="fp-head">
        <el-icon v-if="activeTask.status === 'running'" class="is-loading"><Loading /></el-icon>
        <el-icon v-else-if="activeTask.status === 'done'" color="#67c23a"><CircleCheck /></el-icon>
        <el-icon v-else color="#f56c6c"><CircleClose /></el-icon>
        <b>{{ activeTask.kind === 'check' ? '质检' : '自动修复' }}{{
          activeTask.status === 'running' ? '中' : activeTask.status === 'done' ? '完成' : '失败'
        }}</b>
        <span class="fp-msg">{{ activeTask.message }}</span>
        <el-button
          v-if="activeTask.kind === 'check' && activeTask.report"
          link size="small" @click="reportOpen = true"
        >查看报告</el-button>
        <el-button
          v-if="activeTask.logs?.length && activeTask.status !== 'running'"
          link size="small" @click="logsOpen = !logsOpen"
        >{{ logsOpen ? '隐藏日志' : '展开日志' }}</el-button>
        <el-button
          v-if="activeTask.status !== 'running'" link size="small" @click="dismissTask"
        >收起</el-button>
      </div>
      <el-progress
        :percentage="activeTask.percent || 0"
        :status="activeTask.status === 'error' ? 'exception' : (activeTask.status === 'done' ? 'success' : undefined)"
        :stroke-width="10"
        striped :striped-flow="activeTask.status === 'running'"
      />
      <div class="fp-meta">
        <span v-if="activeTask.kind === 'fix'">{{ activeTask.progress || 0 }} / {{ activeTask.total || 0 }} 条</span>
        <span v-else>报告 {{ (activeTask.report || '').length }} 字</span>
        <span v-if="activeTask.status === 'running'">每 5 秒自动刷新</span>
      </div>
      <div
        v-if="activeTask.logs?.length"
        v-show="activeTask.status === 'running' || logsOpen"
        class="fp-logs"
      >
        <div v-for="(l, i) in activeTask.logs" :key="i" class="fp-log">{{ l }}</div>
      </div>
    </div>

      <div class="split fill-split">
        <!-- ============ 左: 人物档案树 (固定高度, 内部滚动) ============ -->
        <div class="left-panel">
          <el-skeleton v-if="detailLoading" :rows="6" animated />
          <template v-else-if="detail">
            <div class="pd-card">
              <div class="pd-card-inner">
                <div class="pd-head-row">
                  <div class="pd-avatar">{{ detail.name?.slice(0, 1) }}</div>
                  <div class="pd-name">
                    {{ detail.name }}
                    <el-tag size="small" :type="detail.status === 'live' ? 'success' : 'info'">{{ detail.status }}</el-tag>
                  </div>
                  <el-button
                    class="pd-refresh" size="small" circle :icon="Refresh"
                    title="刷新人物数据" @click="refreshDetail"
                  />
                </div>
                <div class="pd-slug">{{ detail.slug }}</div>
                <div v-if="detail.voice_style" class="pd-voice">{{ detail.voice_style }}</div>
              </div>
            </div>

            <div class="tree-scroll">
              <el-tree
                :data="treeData"
                node-key="key"
                :default-expanded-keys="expandedKeys"
                :expand-on-click-node="false"
                highlight-current
                @node-click="onTreeNodeClick"
              >
                <template #default="{ data }">
                  <span class="ft-node">
                    <span class="ft-icon" :class="data.icon">{{ iconChar(data.icon) }}</span>
                    <span
                      v-if="data.qcState"
                      class="qc-dot" :class="`qc-${data.qcState}`"
                      :title="qcTitle(data.qcIssues)"
                    >{{ qcDotChar(data.qcState) }}</span>
                    <span class="ft-label" :class="{ dim: data.dim }">{{ data.label }}</span>
                    <span v-if="data.badge != null" class="ft-badge">{{ data.badge }}</span>
                    <span v-if="data.token" class="ft-badge">{{ data.token }}t</span>
                  </span>
                </template>
              </el-tree>
            </div>
          </template>
          <el-empty v-else description="无数据" :image-size="60" />
        </div>

        <!-- ============ 右: 内容详情 (固定宽度, 上摘要下内容块) ============ -->
        <div class="right-panel">
          <!-- 知识图谱视图 (v-show 常驻, 避免 relation-graph 销毁后内部定时器报错) -->
          <div v-show="preview?.kind === 'graph'" class="graph-body">
            <div class="graph-head">
              <b>知识图谱</b>
              <span class="pv-meta">{{ graphNodes.length }} 节点 · {{ graphEdges.length }} 边（实线=树结构, 虚线=联想）</span>
            </div>
            <div class="graph-host">
              <RelationGraph v-if="graphMounted" ref="graphRef" :options="graphOptions">
                <template #node="{ node }">
                  <div
                    class="rg-node" :class="{ active: nodeDetail?.id === node.id }"
                    :style="{ background: nodeFill(node.data.node_type) }"
                    @click="openNodeDetail(node.id)"
                  >
                    <el-tag :type="typeTag(node.data.node_type)" size="small" effect="plain" class="rg-tag">
                      {{ typeLabel(node.data.node_type) }}
                    </el-tag>
                    <div class="rg-title">{{ node.text }}</div>
                    <div class="rg-imp">imp {{ node.data.importance }}</div>
                  </div>
                </template>
              </RelationGraph>
            </div>
            <div class="graph-tip">点击节点可查看该节点摘要与内容块详情</div>
          </div>

          <!-- 节点/条目详情视图 -->
          <template v-if="preview?.kind !== 'graph'">
            <div v-if="detailLoading" class="detail-body"><el-skeleton :rows="8" animated /></div>
            <el-empty v-else-if="!preview" description="点击左侧树节点查看详情" :image-size="80" />
            <div v-else class="detail-body">
            <div class="pv-head">
              <el-tag v-if="preview.kind" :type="previewTag(preview.kind)" size="small">{{ previewKindLabel(preview.kind) }}</el-tag>
              <b class="pv-title">{{ preview.title }}</b>
              <div v-if="preview.meta" class="pv-meta">{{ preview.meta }}</div>
              <el-button
                v-if="preview.kind === 'life_event'"
                class="pv-action" size="small" :icon="Refresh" :loading="regenLoading"
                @click="regenerateLifeEvent"
              >重新生成</el-button>
            </div>

            <!-- 上: 摘要区 (L1) -->
            <div class="summary-block">
              <div class="sb-label">
                摘要
                <span class="sb-level">L1 · 骨架层 (kb_nodes.summary)</span>
              </div>
              <p class="sb-text">{{ preview.summary || '(无摘要)' }}</p>
            </div>

            <!-- 下: 内容块区 (L2 记忆正文) -->
            <div class="contents-block">
              <div class="cb-title">
                内容块
                <span class="sb-level">L2 · 记忆层 (kb_node_contents)</span>
                <el-tag v-if="preview.contents?.length" size="small" type="info">{{ preview.contents.length }} 块</el-tag>
              </div>

              <el-skeleton
                v-if="preview.kind === 'node' && preview.contents === null"
                :rows="4" animated style="padding: 8px 4px"
              />
              <el-alert
                v-else-if="preview.kind === 'node' && preview.contents === false"
                type="error" :closable="false" show-icon
                title="内容块加载失败" :description="`接口: GET /api/personas/${inspectTarget}/nodes/${preview.nodeId}`"
              />
              <el-empty
                v-else-if="preview.kind === 'node' && !preview.contents?.length"
                description="该节点暂无内容块 (可跑 vh-ingest --edit 补写)" :image-size="60"
              />
              <div v-for="c in preview.contents" :key="c.id" class="content-card">
                <div class="cc-head">
                  <span
                    class="qc-dot lg" :class="`qc-${contentQcState(c.id)}`"
                    :title="qcTitle(contentIssues(c.id))"
                  >{{ qcDotChar(contentQcState(c.id)) }}</span>
                  <b class="cc-seq">{{ c.seq }}</b>
                  <b class="cc-label">{{ c.label || '(无标题)' }}</b>
                  <span class="cc-meta">
                    {{ c.token_estimate }} tokens
                    <template v-if="c.valid_from"> · 生效 {{ c.valid_from }}</template>
                    <template v-if="c.valid_to"> · 封口 {{ c.valid_to }}</template>
                  </span>
                </div>
                <p class="cc-text">{{ c.content }}</p>
                <!-- 该内容块的问题列表 -->
                <div v-if="contentIssues(c.id).length" class="cc-issues">
                  <div v-for="iss in contentIssues(c.id)" :key="iss.id" class="cc-issue">
                    <span class="qc-dot sm" :class="`qc-${sevState(iss.severity)}`"></span>
                    <el-tag size="small" effect="plain">{{ iss.category || '未分类' }}</el-tag>
                    <span class="iss-msg">{{ iss.message }}</span>
                    <el-button
                      link size="small" type="success" @click="resolveIssue(iss, 'resolved')"
                    >标记已修</el-button>
                    <el-button
                      link size="small" @click="resolveIssue(iss, 'dismissed')"
                    >忽略</el-button>
                  </div>
                </div>
              </div>

              <!-- 非知识节点: 单段文本展示在下区 -->
              <div v-if="preview.kind !== 'node'" class="content-card">
                <div class="cc-head"><b class="cc-label">{{ preview.title }}</b></div>
                <p class="cc-text">{{ preview.text }}</p>
              </div>
            </div>

            <!-- 关联节点 (kb_edges 关系查询) -->
            <div v-if="preview.clues?.length" class="clues-block">
              <div class="sb-label">关联节点 · {{ preview.clues.length }} (来自 kb_edges)</div>
              <div
                v-for="(cl, i) in preview.clues" :key="i"
                class="clue-row" @click="jumpNode(cl.node_id)"
              >
                <span class="arrow">{{ cl.direction === 'outgoing' ? '→' : '←' }}</span>
                <el-tag size="small" :type="clueTag(cl.edge_type)">{{ clueLabel(cl.edge_type) }}</el-tag>
                <span class="clue-title">{{ cl.title }}</span>
                <span class="clue-w">w={{ cl.weight.toFixed(2) }}</span>
              </div>
            </div>
            </div>
          </template>
        </div>
      </div>
    </template>

    <!-- ============ 质检报告抽屉 (Markdown 渲染) ============ -->
    <el-drawer
      v-model="reportOpen"
      title="质检报告"
      direction="rtl"
      size="52%"
      :append-to-body="true"
    >
      <template #header>
        <div class="drawer-head">
          <b>质检报告</b>
          <el-tag v-if="reportDone" size="small" :type="issueCount ? 'warning' : 'success'">
            {{ issueCount ? `${issueCount} 项提示` : '全部通过' }}
          </el-tag>
          <el-tag v-else-if="checking" size="small" type="info">生成中…</el-tag>
        </div>
      </template>
      <div class="report-md" v-html="renderedReport"></div>
    </el-drawer>

    <!-- ============ 检查记录抽屉 (qc_issues 表) ============ -->
    <el-drawer v-model="issuesOpen" direction="rtl" size="46%" :append-to-body="true">
      <template #header>
        <div class="drawer-head">
          <b>检查记录</b>
          <el-tag size="small" :type="openIssues.length ? 'danger' : 'success'">
            {{ openIssues.length }} 项未处理
          </el-tag>
          <el-radio-group v-model="issuesFilter" size="small" style="margin-left: 12px">
            <el-radio-button value="open">未处理</el-radio-button>
            <el-radio-button value="resolved">已修复</el-radio-button>
            <el-radio-button value="all">全部</el-radio-button>
          </el-radio-group>
        </div>
      </template>
      <el-empty v-if="!filteredIssues.length" description="暂无记录" :image-size="70" />
      <div v-for="iss in filteredIssues" :key="iss.id" class="issue-card" :class="`sev-${iss.severity}`">
        <div class="ic-head">
          <span class="qc-dot" :class="`qc-${sevState(iss.severity)}`">{{ qcDotChar(sevState(iss.severity)) }}</span>
          <el-tag size="small" :type="sevTagType(iss.severity)">{{ sevLabel(iss.severity) }}</el-tag>
          <el-tag size="small" effect="plain" type="info">{{ entityLabel(iss.entity_type) }}</el-tag>
          <span class="ic-title">{{ iss.entity_title || iss.entity_id?.slice(0, 8) || '—' }}</span>
          <el-tag size="small" :type="iss.status === 'open' ? 'warning' : iss.status === 'resolved' ? 'success' : 'info'">
            {{ statusLabel(iss.status) }}
          </el-tag>
        </div>
        <div class="ic-meta">
          <el-tag v-if="iss.category" size="small" effect="plain">{{ iss.category }}</el-tag>
          <span class="ic-date">{{ iss.checked_at }}</span>
        </div>
        <p class="ic-msg">{{ iss.message }}</p>
        <div v-if="iss.status === 'open'" class="ic-actions">
          <el-button size="small" type="success" plain @click="resolveIssue(iss, 'resolved')">标记已修</el-button>
          <el-button size="small" plain @click="resolveIssue(iss, 'dismissed')">忽略</el-button>
        </div>
      </div>
    </el-drawer>
  </section>
</template>

<script setup>
import { ref, nextTick, watch, onMounted, onUnmounted, computed } from 'vue'
import { marked } from 'marked'
import { ElMessage } from 'element-plus'
import RelationGraph from 'relation-graph-vue3'
import { Loading, CircleCheck, CircleClose, Refresh } from '@element-plus/icons-vue'
import { API, sse, personas, inspectTarget, loadPersonas } from '../store'

const checking = ref(false)
const fixing = ref(false)
const detail = ref(null)
const detailLoading = ref(false)

// 报告抽屉
const reportOpen = ref(false)
const reportText = ref('')
const reportDone = ref(false)
const issueCount = ref(0)

const renderedReport = computed(() => {
  if (!reportText.value) {
    return '<p class="md-empty">点击顶部「开始质检」生成报告…</p>'
  }
  return marked.parse(reportText.value)
})

const TYPE_MAP = {
  chapter: ['章节', 'primary'],
  semantic: ['观念', 'success'],
  procedural: ['习惯', 'warning'],
  identity: ['身份', 'danger'],
}
const typeLabel = (t) => TYPE_MAP[t]?.[0] || t
const typeTag = (t) => TYPE_MAP[t]?.[1] || 'info'

const ICONS = { identity: '👤', skeleton: '📜', tree: '🌳', graph: '🕸️', chapter: '📖', semantic: '💡', procedural: '⚙️', content: '📄', finance: '💰', event: '💵', memory: '🧠', timeline: '📅' }
const iconChar = (k) => ICONS[k] || '📄'

// ---- 人物详情加载 ----
async function loadDetail(keepView = false) {
  if (!keepView) {
    detail.value = null
    preview.value = null
    graphNodes.value = []
    graphEdges.value = []
    graphRootId.value = null
    nodeDetail.value = null
    nodeContentIds.clear()
    expandedKeys.value = ['identity', 'skeleton', 'graph', 'knowledge', 'finance', 'scaffold']
  }
  if (!inspectTarget.value) return
  // 刷新场景 (keepView) 保留旧内容不显示骨架屏, 避免左侧卡片/树闪烁消失
  detailLoading.value = !keepView
  try {
    const [treeR, profileR, scaffoldR] = await Promise.all([
      fetch(`${API}/api/personas/${inspectTarget.value}/tree`),
      fetch(`${API}/api/personas/${inspectTarget.value}/profile`),
      fetch(`${API}/api/personas/${inspectTarget.value}/scaffold`),
    ])
    const tree = await treeR.json()
    const profile = await profileR.json()
    const scaffold = scaffoldR.ok ? await scaffoldR.json() : { events: [] }

    const md = profile.markdown || ''
    const lines = md.split('\n')
    const nameLine = lines.find((l) => l.startsWith('# '))
    const voiceLine = lines.find((l) => l.startsWith('> '))
    const voiceStyle = voiceLine?.slice(2).replace('说话风格:', '').trim() || ''
    const skelIdx = lines.findIndex((l) => l.trim() === '## 人生骨架')
    const graphIdx = lines.findIndex((l) => l.trim() === '## 记忆图谱')
    const bioSkeleton = skelIdx >= 0
      ? lines.slice(skelIdx + 1, graphIdx > skelIdx ? graphIdx : undefined).filter((l) => l.trim()).join('\n')
      : ''
    const identity = lines
      .slice(nameLine ? lines.indexOf(nameLine) + 1 : 0, skelIdx >= 0 ? skelIdx : lines.length)
      .filter((l) => l.trim() && !l.startsWith('>'))
      .join('\n')

    const nodes = []
    const walk = (list) => { for (const n of list || []) { nodes.push(n); walk(n.children) } }
    walk(tree.children)

    let finance = { events: [], balance: '' }
    const finIdx = lines.findIndex((l) => l.trim() === '## 财务大事记')
    if (finIdx >= 0) {
      const finLines = lines.slice(finIdx + 1)
      finance.events = finLines.filter((l) => l.trim().startsWith('-')).map((l) => l.replace(/^-\s*/, '').trim())
      finance.balance = finLines.find((l) => l.includes('当前家底'))?.trim() || ''
    }

    const p = personas.value.find((x) => x.id === inspectTarget.value)
    detail.value = {
      name: p?.name || nameLine?.slice(2).trim() || '',
      slug: p?.slug || tree.slug,
      status: p?.status || 'live',
      voice_style: voiceStyle,
      core_identity: identity,
      bio_skeleton: bioSkeleton,
      nodes,
      finance,
      scaffold_events: scaffold.events || [],
    }
    await loadQcIssues()   // 问题记录 (红黄绿点数据源)
  } finally { detailLoading.value = false }
}

// ---- 档案树 (不含内容块: 内容块只在右侧详情展示) ----
const treeData = computed(() => {
  const d = detail.value
  if (!d) return []

  const knowledgeChildren = d.nodes.map((n) => {
    const nid = n.node_id ?? n.id
    return {
      key: `node-${nid}`,
      label: n.title,
      icon: n.node_type,
      kind: 'node',
      nodeId: nid,
      node_type: n.node_type,
      importance: n.importance,
      summary: n.summary,
      isLeaf: true,
      get qcState() { return nodeQcState(this.nodeId) },
      get qcIssues() {
        const own = issuesOf('node', this.nodeId)
        const child = (nodeContentIds.get(this.nodeId) || []).flatMap((cid) => issuesOf('content', cid))
        return [...own, ...child]
      },
    }
  })

  const financeChildren = d.finance.events.map((e, i) => ({
    key: `fin-${i}`,
    label: e.split(':')[0].slice(0, 24),
    icon: 'event',
    kind: 'finance',
    title: e.split(':')[0],
    text: e,
    summary: e,
  }))
  if (d.finance.balance) {
    financeChildren.push({
      key: 'fin-balance', label: '当前家底', icon: 'finance', kind: 'finance',
      title: '当前家底', text: d.finance.balance, summary: d.finance.balance,
    })
  }

  // 人物履历: life_scaffold_events (按开始时间升序, 后端已排序)
  const lifeChildren = (d.scaffold_events || []).map((e) => {
    const year = e.start_year ?? e.start_raw ?? '—'
    const title = `${year} · ${e.event_type}`
    const payloadTxt = e.payload ? (typeof e.payload === 'string' ? e.payload : JSON.stringify(e.payload)) : ''
    const blocks = (e.contents || []).map((c) => c.content).filter(Boolean)
    const text = blocks.length ? blocks.join('\n\n') : (e.content || '')
    return {
      key: `life-${e.id}`,
      label: title,
      icon: 'timeline',
      kind: 'life_event',
      eventId: e.id,
      title,
      summary: e.summary || '（暂无摘要，运行「渐进式生成」后由 agent 补齐）',
      text: text || `原始事件: ${payloadTxt || '(无)'}`,
      meta: `${e.processed ? '已消化' : '待消化'} · life_scaffold_events${payloadTxt ? ' · ' + payloadTxt : ''}`,
    }
  })

  return [
    { key: 'identity', label: '身份自述', icon: 'identity', kind: 'identity', title: '身份自述', text: d.core_identity || '(无)', summary: summarize(d.core_identity, 120) },
    { key: 'skeleton', label: '人生骨架', icon: 'skeleton', kind: 'skeleton', title: '人生骨架', text: d.bio_skeleton || '(无)', summary: summarize(d.bio_skeleton, 120) },
    { key: 'graph', label: '知识图谱', icon: 'graph', kind: 'graph', title: '知识图谱' },
    { key: 'knowledge', label: '知识节点', icon: 'tree', badge: d.nodes.length, children: knowledgeChildren },
    { key: 'finance', label: '财务大事记', icon: 'finance', badge: d.finance.events.length, children: financeChildren },
    { key: 'scaffold', label: '人物履历', icon: 'timeline', badge: lifeChildren.length, children: lifeChildren },
  ]
})

// 节点 → 内容块 id 映射 (仅用于节点红黄绿点的问题聚合, 不在树上展示)
const expandedKeys = ref(['identity', 'skeleton', 'graph', 'knowledge', 'finance', 'scaffold'])

// ---- 右侧详情 ----
const preview = ref(null)

const KIND_MAP = {
  identity: ['身份', 'danger'],
  skeleton: ['骨架', 'info'],
  node: ['知识节点', 'primary'],
  finance: ['财务', 'warning'],
  life_event: ['履历', 'success'],
  content: ['内容块', 'info'],
}
const previewKindLabel = (k) => KIND_MAP[k]?.[0] || k
const previewTag = (k) => KIND_MAP[k]?.[1] || 'info'

const nodeCache = new Map()

/** 从长文本提取短摘要 (虚拟节点/缺失摘要时兜底) */
function summarize(text, n = 120) {
  const t = String(text || '').replace(/\s+/g, ' ').trim()
  if (!t) return ''
  return t.length > n ? t.slice(0, n) + '…' : t
}

const regenLoading = ref(false)
let regenTimer = null

/** 重新生成当前履历事件 (走独立接口) */
async function regenerateLifeEvent() {
  const eventId = preview.value?.eventId
  if (!eventId || !inspectTarget.value) return
  regenLoading.value = true
  try {
    const r = await fetch(
      `${API}/api/personas/${inspectTarget.value}/scaffold/events/${eventId}/expand`,
      { method: 'POST' },
    )
    if (!r.ok) {
      const err = await r.json().catch(() => ({}))
      ElMessage.error(err.detail || `启动失败 HTTP ${r.status}`)
      regenLoading.value = false
      return
    }
    const d = await r.json()
    pollRegen(d.job_id, eventId)
  } catch {
    regenLoading.value = false
  }
}

function pollRegen(jobId, eventId) {
  if (regenTimer) clearInterval(regenTimer)
  regenTimer = setInterval(async () => {
    try {
      const r = await fetch(`${API}/api/jobs/${jobId}`)
      if (!r.ok) return
      const st = await r.json()
      if (st.status === 'running') return
      clearInterval(regenTimer)
      regenTimer = null
      regenLoading.value = false
      if (st.status === 'error') {
        ElMessage.error(st.message || '重新生成失败')
        return
      }
      await loadDetail(true)
      const group = treeData.value.find((g) => g.key === 'scaffold')
      const node = (group?.children || []).find((n) => n.eventId === eventId)
      if (node) await onTreeNodeClick(node)
      ElMessage.success(st.message || '履历已重新生成')
    } catch { /* ignore */ }
  }, 3000)
}

async function onTreeNodeClick(data) {
  if (data.kind === 'graph') {
    preview.value = { kind: 'graph', title: '知识图谱' }
    await nextTick()
    // 首次进入才挂载 RelationGraph (此时容器已可见, 避免 0 尺寸初始化);
    // 之后永不卸载 (v-show 切显隐), 规避其内部 ResizeObserver unobserve 报错
    if (!graphMounted.value) graphMounted.value = true
    if (!graphNodes.value.length) await loadGraph()
    else await ensureGraphRendered()
    return
  }

  // 内容块叶子节点: 直接展示正文
  if (data.kind === 'content') {
    preview.value = {
      kind: 'content', title: data.title, text: data.text,
      summary: summarize(data.text, 120),
      meta: `${data.token || 0} tokens`,
    }
    return
  }

  if (data.kind === 'node') {
    // 结构化 preview: 摘要(L1) 在上, 内容块(L2) 在下 (内容块只在详情里展示)
    preview.value = {
      kind: 'node', title: data.label, nodeId: data.nodeId,
      meta: `${typeLabel(data.node_type)} · 重要度 ${data.importance}`,
      summary: data.summary,
      contents: null,        // 加载中
      clues: null,
    }
    const cached = nodeCache.get(data.nodeId)
    if (cached) { applyNodeDetail(data, cached); return }
    try {
      const r = await fetch(`${API}/api/personas/${inspectTarget.value}/nodes/${data.nodeId}`)
      if (!r.ok) throw new Error(`HTTP ${r.status}`)
      const d = await r.json()
      nodeCache.set(data.nodeId, d)
      // 用户可能已切到别的节点, 只在仍选中本节点时应用
      if (preview.value?.nodeId !== data.nodeId) return
      applyNodeDetail(data, d)
    } catch {
      if (preview.value?.nodeId === data.nodeId) preview.value.contents = false
    }
    return
  }

  // 分组节点: 点击展开/收起, 不切右侧
  if (data.children) {
    const idx = expandedKeys.value.indexOf(data.key)
    if (idx >= 0) expandedKeys.value = expandedKeys.value.filter((k) => k !== data.key)
    else expandedKeys.value = [...expandedKeys.value, data.key]
    return
  }

  preview.value = {
    kind: data.kind, title: data.title, text: data.text,
    summary: data.summary || summarize(data.text || data.title, 120),
    meta: data.meta || '',
    eventId: data.eventId || null,
  }
}

/** 记录 node → content ids (供节点红黄绿点聚合, 不在树上渲染内容块) */
function rememberContents(nodeId, d) {
  nodeContentIds.set(nodeId, (d.contents || []).map((c) => c.id))
}

function applyNodeDetail(treeNode, d) {
  // 记录 node → content ids 映射 (树聚合红黄绿点用)
  nodeContentIds.set(d.id, (d.contents || []).map((c) => c.id))
  preview.value = {
    kind: 'node',
    title: d.title,
    meta: `${typeLabel(d.node_type)} · 重要度 ${d.importance} · 披露度 ${d.disclosure_level}` +
          (d.access_count ? ` · 被想起 ${d.access_count} 次` : ''),
    summary: d.summary || summarize(d.contents?.[0]?.content || d.title, 120),
    contents: d.contents || [],
    clues: d.clues || [],
  }
}

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

// 图谱节点底色 (按节点类型)
const nodeFill = (t) => ({
  root: '#eef1f6',
  chapter: '#ecf5ff',
  semantic: '#f0f9eb',
  procedural: '#fdf6ec',
  identity: '#fef0f0',
}[t] || '#f5f7fa')

watch(inspectTarget, async () => {
  await loadDetail()
  await syncFixState()
  await syncCheckState()
})
onMounted(async () => {
  if (inspectTarget.value) {
    await loadDetail()
    await syncFixState()
    await syncCheckState()
  }
})
onUnmounted(() => { stopFixPolling(); stopCheckPolling(); if (regenTimer) clearInterval(regenTimer) })

// ---- 质检问题记录 (qc_issues) ----
const issuesOpen = ref(false)
const issuesFilter = ref('open')
const qcIssues = ref([])

const openIssues = computed(() => qcIssues.value.filter((i) => i.status === 'open'))
const filteredIssues = computed(() =>
  issuesFilter.value === 'all' ? qcIssues.value : qcIssues.value.filter((i) => i.status === issuesFilter.value)
)

async function loadQcIssues() {
  if (!inspectTarget.value) { qcIssues.value = []; return }
  const r = await fetch(`${API}/api/personas/${inspectTarget.value}/qc-issues?status=all`)
  if (!r.ok) return
  qcIssues.value = await r.json()
}

async function resolveIssue(iss, status) {
  const r = await fetch(`${API}/api/qc-issues/${iss.id}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ status }),
  })
  if (r.ok) { iss.status = status }
}

// 实体 → 其未处理问题
function issuesOf(entityType, entityId) {
  if (!entityId) return qcIssues.value.filter((i) => i.status === 'open' && i.entity_type === entityType && !i.entity_id)
  return qcIssues.value.filter((i) => i.status === 'open' && i.entity_type === entityType && i.entity_id === entityId)
}

const hasRunCheck = computed(() => qcIssues.value.length > 0)
const nodeContentIds = new Map()   // node_id → [content_id] (详情加载时填充)

// 节点状态: 自身问题 + 其下内容块问题聚合 → red/yellow/green
function nodeQcState(nodeId) {
  const own = issuesOf('node', nodeId)
  const contentIds = nodeContentIds.get(nodeId) || []
  const child = contentIds.flatMap((cid) => issuesOf('content', cid))
  const all = [...own, ...child]
  if (all.some((i) => i.severity === 'high')) return 'red'
  if (all.length) return 'yellow'
  return hasRunCheck.value ? 'green' : null   // 没质检过不显示点
}

function contentQcState(contentId) {
  const list = issuesOf('content', contentId)
  if (list.some((i) => i.severity === 'high')) return 'red'
  if (list.length) return 'yellow'
  return hasRunCheck.value ? 'green' : null
}
function contentIssues(contentId) { return issuesOf('content', contentId) }

const SEV_LABEL = { high: '高', medium: '中', low: '低' }
const sevLabel = (s) => SEV_LABEL[s] || s
const sevState = (s) => (s === 'high' ? 'red' : s === 'medium' ? 'yellow' : 'green')
const sevTagType = (s) => ({ high: 'danger', medium: 'warning', low: 'info' }[s] || 'info')
const qcDotChar = () => '●'
function qcTitle(list) {
  if (!list?.length) return '质检通过'
  return list.map((i) => `[${sevLabel(i.severity)}] ${i.message}`).join('\n')
}
const ENTITY_LABEL = { node: '知识节点', content: '内容块', memory: '情景记忆', finance_event: '财务事件', finance_balance: '家底', persona: '人物' }
const entityLabel = (t) => ENTITY_LABEL[t] || t
const statusLabel = (s) => ({ open: '未处理', resolved: '已修复', dismissed: '已忽略' }[s] || s)

// ---- 知识图谱 (relation-graph, 右侧面板内渲染) ----
const graphRef = ref(null)
const graphNodes = ref([])
const graphEdges = ref([])
const graphRootId = ref(null)
const graphMounted = ref(false)   // 首次进入图谱才挂载, 之后只切显隐
const nodeDetail = ref(null)

const graphOptions = {
  debug: false,
  moveToCenter: true,
  zoomToFit: true,
  allowSwitchLineShape: true,
  allowShowMiniToolBar: true,
  defaultNodeBorderWidth: 0,
  defaultNodeColor: 'rgba(0,0,0,0)',
  defaultLineMarker: { markerWidth: 0, markerHeight: 0 },
  // 思维导图样式: 根在左, 向右逐层展开
  layout: {
    layoutName: 'tree',
    from: 'left',
    min_per_width: 210,
    max_per_width: 280,
    distance_coefficient: 1,
  },
  defaultJunctionPoint: 'border',
  defaultNodeShape: 0,
  defaultLineShape: 2,        // 折线 (思维导图风格)
}

async function loadGraph() {
  const r = await fetch(`${API}/api/personas/${inspectTarget.value}/graph`)
  if (!r.ok) return
  const g = await r.json()
  graphNodes.value = g.nodes || []
  graphEdges.value = g.edges || []
  graphRootId.value = g.root_id || null
  await nextTick()
  await ensureGraphRendered()
}

/** 轮询等待 relation-graph 实例就绪 (组件挂载是异步的), 再灌数据并适配尺寸. */
async function ensureGraphRendered() {
  for (let i = 0; i < 40; i++) {
    const inst = graphRef.value?.getInstance?.()
    if (inst) {
      inst.doResize?.()          // 从隐藏切回时容器尺寸变化, 重新计算
      setGraphData(inst)
      return
    }
    await new Promise((r) => setTimeout(r, 50))
  }
}

function setGraphData(instance) {
  const targets = new Set(graphEdges.value.filter((e) => e.type === 'tree').map((e) => e.target))
  const root = graphRootId.value
    || graphNodes.value.find((n) => !targets.has(n.id))?.id
    || graphNodes.value[0]?.id

  instance.setJsonData({
    rootId: root,
    nodes: graphNodes.value.map((n) => ({
      id: n.id,
      text: n.virtual ? '人物' : (n.title.length > 15 ? n.title.slice(0, 14) + '…' : n.title),
      data: { node_type: n.node_type, importance: n.importance, virtual: !!n.virtual },
      nodeShape: 0,
      width: n.virtual ? 100 : 180,
      height: n.virtual ? 52 : 64,
    })),
    lines: graphEdges.value.map((e) => ({
      from: e.source,
      to: e.target,
      text: e.type === 'tree' ? '' : clueLabel(e.type),
      color: e.type === 'tree' ? '#c0c4cc' : edgeColor(e.type),
      lineWidth: e.type === 'tree' ? 1.5 : 1 + (e.weight || 0.5) * 2,
      lineShape: e.type === 'tree' ? 2 : 6,   // 树边折线(思维导图), 联想边虚线
      style: e.type === 'tree'
        ? { stroke: '#c0c4cc', strokeWidth: 1.5 }
        : { stroke: edgeColor(e.type), strokeWidth: 1 + (e.weight || 0.5) * 2, strokeDasharray: '6 4' },
      useTextPath: true,
    })),
  }, { append: false })
}

// 点图谱节点 → 右侧切换为该节点详情
async function openNodeDetail(nodeId) {
  const r = await fetch(`${API}/api/personas/${inspectTarget.value}/nodes/${nodeId}`)
  if (!r.ok) return
  const d = await r.json()
  nodeDetail.value = d
  nodeCache.set(nodeId, d)
  rememberContents(nodeId, d)   // 仅记录内容块 id 供问题聚合
  preview.value = {
    kind: 'node',
    title: d.title,
    nodeId: d.id,
    meta: `${typeLabel(d.node_type)} · 重要度 ${d.importance} · 披露度 ${d.disclosure_level}` +
          (d.access_count ? ` · 被想起 ${d.access_count} 次` : ''),
    summary: d.summary || summarize(d.contents?.[0]?.content || d.title, 120),
    contents: d.contents || [],
    clues: d.clues || [],
  }
}

// ---- 任务进度条: 质检 / 修复共用 (取当前活跃或刚完成的任务) ----
const logsOpen = ref(false)   // 完成态日志默认折叠

const activeTask = computed(() => {
  const c = checkState.value
  const f = fixState.value
  if (c.status === 'running') return { ...c, kind: 'check' }
  if (f.status === 'running') return { ...f, kind: 'fix' }
  if (f.status === 'done' || f.status === 'error') return { ...f, kind: 'fix' }
  if (c.status === 'done' || c.status === 'error') return { ...c, kind: 'check' }
  return null
})

function dismissTask() {
  if (checkState.value.status !== 'running' && checkState.value.status !== 'idle') {
    checkState.value = { ...checkState.value, status: 'idle' }
  }
  if (fixState.value.status !== 'running' && fixState.value.status !== 'idle') {
    fixState.value = { ...fixState.value, status: 'idle' }
  }
}

// ---- 质检: 后台任务 + 2s 轮询 (报告实时增量渲染) ----
const checkState = ref({ status: 'idle', percent: 0, report: '', logs: [], issues: [] })
let checkTimer = null

/** 纯查询质检状态 (不写回 checkState) */
async function fetchCheckStatus() {
  if (!inspectTarget.value) return { status: 'idle' }
  try {
    const r = await fetch(`${API}/api/personas/${inspectTarget.value}/check/status`)
    if (r.ok) return await r.json()
  } catch { /* 网络抖动忽略 */ }
  return { status: 'idle' }
}

function stopCheckPolling() {
  if (checkTimer) { clearInterval(checkTimer); checkTimer = null }
}

function startCheckPolling() {
  stopCheckPolling()
  checkTimer = setInterval(async () => {
    const st = await fetchCheckStatus()
    reportText.value = st.report || ''      // 实时增量刷新报告 (抽屉内 MD 渲染)
    if (st.status === 'running') {
      checkState.value = st                  // 仅在运行中更新进度条
      return
    }
    stopCheckPolling()
    if (st.status === 'done') {
      checkState.value = st
      issueCount.value = (st.issues || []).length
      reportDone.value = true
      reportText.value += `\n\n---\n\n**✓ 检查完成**\n`
      loadDetail(true)
      loadQcIssues()
      nodeCache.clear()
    } else if (st.status === 'error') {
      checkState.value = st
      reportDone.value = true
      reportText.value += `\n\n**✗ 错误:** ${st.message}\n`
    }
    checking.value = false
    // 完成后自动收起进度条
    setTimeout(() => {
      if (checkState.value.status !== 'running') {
        checkState.value = { ...checkState.value, status: 'idle' }
      }
    }, 600)
  }, 5000)
}

async function doCheck() {
  if (!inspectTarget.value) return
  checking.value = true
  logsOpen.value = false
  reportText.value = ''
  reportDone.value = false
  issueCount.value = 0
  reportOpen.value = true   // 自动弹出抽屉
  try {
    const r = await fetch(`${API}/api/personas/${inspectTarget.value}/check`, { method: 'POST' })
    if (r.ok) {
      const d = await r.json()
      if (d.state) checkState.value = d.state
      startCheckPolling()
    } else {
      checking.value = false
    }
  } catch (e) {
    checking.value = false
  }
}

/** 进入页面 / 切换人物: 查一次质检状态 (仅在跑时续接轮询; 已完成的旧任务不展示) */
async function syncCheckState() {
  const st = await fetchCheckStatus()
  if (st.status === 'running') {
    checking.value = true
    reportOpen.value = true
    reportText.value = st.report || ''
    startCheckPolling()
  } else if (st.status === 'done' || st.status === 'error') {
    // 旧任务完成态: 不弹进度条 (报告仍可通过"查看报告"查看)
    checkState.value = { ...st, status: 'idle' }
    if (st.report) reportText.value = st.report
  }
}

/** 手动刷新人物数据 (左侧刷新按钮): 只刷人物/树/问题记录, 不动任务状态 */
async function refreshDetail() {
  nodeCache.clear()
  await loadDetail(true)
  await loadQcIssues()
}

// ---- 自动修复: 后台任务 + 2s 轮询进度 ----
const fixState = ref({ status: 'idle', progress: 0, total: 0, percent: 0, message: '', logs: [] })
let fixTimer = null

/** 纯查询修复状态 (不写回 fixState, 由调用方决定是否展示) */
async function fetchFixStatus() {
  if (!inspectTarget.value) return { status: 'idle' }
  try {
    const r = await fetch(`${API}/api/personas/${inspectTarget.value}/fix/status`)
    if (r.ok) return await r.json()
  } catch { /* 网络抖动忽略 */ }
  return { status: 'idle' }
}

function stopFixPolling() {
  if (fixTimer) { clearInterval(fixTimer); fixTimer = null }
}

function startFixPolling() {
  stopFixPolling()
  fixTimer = setInterval(async () => {
    const st = await fetchFixStatus()
    if (st.status === 'running') {
      fixState.value = st          // 仅在运行中刷新进度条
      return
    }
    stopFixPolling()
    if (st.status === 'done' || st.status === 'error') {
      fixState.value = st
      reportDone.value = true
      loadDetail(true)
      loadQcIssues()
      nodeCache.clear()
      // 完成后立即收起进度条
      setTimeout(() => {
        if (fixState.value.status === 'done') {
          fixState.value = { ...fixState.value, status: 'idle' }
        }
      }, 600)
    }
  }, 5000)
}

async function doFix() {
  if (!inspectTarget.value || !openIssues.value.length) return
  fixing.value = true
  logsOpen.value = false
  reportDone.value = false
  try {
    const r = await fetch(`${API}/api/personas/${inspectTarget.value}/fix`, { method: 'POST' })
    if (r.ok) {
      const d = await r.json()
      if (d.state) fixState.value = d.state
      startFixPolling()
    }
  } finally { fixing.value = false }
}

/** 进入页面 / 切换人物: 查一次修复状态 (仅在跑时续接轮询; 已完成的旧任务不展示) */
async function syncFixState() {
  const st = await fetchFixStatus()
  if (st.status === 'running') {
    startFixPolling()
  } else if (st.status === 'done' || st.status === 'error') {
    fixState.value = { ...st, status: 'idle' }   // 旧任务完成态: 自动隐藏
  }
}
</script>

<style scoped>
/* openpage: 页面占满 main 剩余高度, 页面本身不滚动; 左右两栏各自内部滚动 */
.fill-page {
  display: flex; flex-direction: column;
  flex: 1; min-height: 0;
  padding-bottom: 12px;
  width: clamp(1000px, 62vw, 1800px);
  max-width: 100%;
  margin: 0 auto;
}
.page-head { flex-shrink: 0; }
.toolbar { flex-shrink: 0; }

.toolbar {
  display: flex; align-items: center; gap: 12px;
  background: #fff; border: 1px solid #e4e7ed; border-radius: 8px;
  padding: 10px 14px; margin-bottom: 14px;
}
.toolbar-spacer { flex: 1; }
.picker-label { font-size: 13px; color: #606266; }

/* 修复进度条 */
.fix-progress {
  flex-shrink: 0; background: #fff; border: 1px solid #e4e7ed; border-radius: 8px;
  padding: 10px 14px; margin-bottom: 12px;
}
.fix-progress.fp-done { border-color: #d1edc4; background: #f0f9eb; }
.fix-progress.fp-error { border-color: #fbc4c4; background: #fef0f0; }
.fp-head { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; font-size: 13px; }
.fp-msg { flex: 1; color: #606266; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.fp-meta { display: flex; justify-content: space-between; font-size: 11px; color: #909399; margin-top: 4px; }
.fp-logs {
  margin-top: 8px; max-height: 72px; overflow-y: auto;   /* 限高, 避免挤压主体内容 */
  background: #1e1e1e; color: #c8c8c8; border-radius: 6px; padding: 8px 10px;
  font-size: 11.5px; line-height: 1.6; font-family: Consolas, monospace;
}
.fp-log { white-space: pre-wrap; word-break: break-all; }

/* 分栏: 黄金分割 —— 右(内容) 62%, 左(档案树) 38%; 高度吃满剩余, 内部各自滚动 */
.fill-split {
  flex: 1 1 auto; min-height: 0;
  display: grid;
  grid-template-columns: 38fr 62fr;
  gap: 16px;
  align-items: stretch;
}

/* 左: 档案树面板 (38%) */
.left-panel {
  min-width: 0; min-height: 0;
  background: #fff; border: 1px solid #e4e7ed;
  border-radius: 8px; padding: 14px;
  display: flex; flex-direction: column; overflow: hidden;
}
.left-panel :deep(.el-skeleton), .left-panel > .el-empty { margin: auto; }

.pd-card { flex-shrink: 0; max-height: 132px; border-bottom: 1px solid #f0f0f0; margin-bottom: 10px; overflow: hidden; }
.pd-card-inner { max-height: 132px; overflow-y: auto; padding-bottom: 12px; }
.pd-head-row { display: flex; align-items: center; gap: 12px; }
.pd-avatar {
  width: 44px; height: 44px; border-radius: 10px; flex-shrink: 0;
  background: linear-gradient(135deg, #409eff, #67c23a); color: #fff;
  display: grid; place-items: center; font-size: 20px; font-weight: 700;
}
.pd-name { font-size: 16px; font-weight: 600; display: flex; align-items: center; gap: 6px; flex: 1; min-width: 0; }
.pd-refresh { flex-shrink: 0; }
.pd-slug { font-size: 12px; color: #c0c4cc; margin-top: 4px; }
.pd-voice { font-size: 12px; color: #909399; margin-top: 6px; background: #f9f9f9; padding: 4px 8px; border-radius: 4px; }

.tree-scroll { flex: 1; min-height: 0; overflow-y: auto; }

.ft-node { display: inline-flex; align-items: center; gap: 6px; min-width: 0; }
.ft-icon { font-size: 13px; flex-shrink: 0; }
.ft-label { font-size: 13px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.ft-label.dim { color: #909399; }
.ft-badge { font-size: 10px; color: #909399; background: #f0f2f5; border-radius: 8px; padding: 0 6px; flex-shrink: 0; }

/* 右: 详情 (62%) */
.right-panel {
  min-width: 0; min-height: 0;
  background: #fff; border: 1px solid #e4e7ed;
  border-radius: 8px; overflow: hidden;
  display: flex; flex-direction: column;
}
.detail-body {
  flex: 1; min-height: 0; overflow-y: auto; padding: 16px;
  width: 100%;                    /* 内容限制在固定宽度内, 长文本换行不撑宽 */
  box-sizing: border-box;
}

.pv-head { display: flex; align-items: center; gap: 8px; margin-bottom: 12px; flex-wrap: wrap; }
.pv-title { font-size: 15px; }
.pv-meta { font-size: 12px; color: #909399; }
.pv-action { margin-left: auto; }

/* 上: 摘要区 (L1) */
.summary-block {
  background: #f5f7fa; border-left: 3px solid #409eff; border-radius: 0 6px 6px 0;
  padding: 10px 14px; margin-bottom: 14px;
}
.sb-label {
  font-size: 12px; font-weight: 600; color: #409eff; margin-bottom: 6px;
  display: flex; align-items: center; gap: 8px;
}
.sb-level { font-weight: 400; color: #c0c4cc; font-size: 11px; }
.sb-text { font-size: 13px; line-height: 1.8; color: #303133; margin: 0; }

/* 下: 内容块区 (L2) */
.contents-block { min-height: 0; }
.cb-title {
  font-size: 12px; font-weight: 600; color: #67c23a; margin-bottom: 10px;
  display: flex; align-items: center; gap: 8px;
}
.content-card {
  border: 1px solid #ebeef5; border-radius: 8px; padding: 12px 14px; margin-bottom: 10px;
  background: #fff;
}
.cc-head { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; }
.cc-seq {
  width: 20px; height: 20px; border-radius: 50%; background: #67c23a; color: #fff;
  display: grid; place-items: center; font-size: 11px; flex-shrink: 0;
}
.cc-label { font-size: 14px; flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.cc-meta { font-size: 11px; color: #c0c4cc; flex-shrink: 0; }
.cc-text { font-size: 13px; line-height: 1.9; color: #303133; white-space: pre-wrap; margin: 0; }

/* 联想线索 */
.clues-block { margin-top: 14px; border-top: 1px dashed #ebeef5; padding-top: 12px; }
.clues-block .sb-label { color: #909399; }
.clue-row {
  display: flex; align-items: center; gap: 8px; padding: 5px 8px; border-radius: 6px;
  font-size: 13px;
}
.clue-row:hover { background: #f5f7fa; }
.clue-row .arrow { color: #909399; }
.clue-title { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.clue-w { color: #c0c4cc; font-size: 12px; }

/* 知识图谱视图 (右侧面板内) */
.graph-body { flex: 1; min-height: 0; display: flex; flex-direction: column; padding: 12px 14px; }
.graph-head { display: flex; align-items: baseline; gap: 10px; margin-bottom: 8px; flex-shrink: 0; }
.graph-host { flex: 1; min-height: 0; border: 1px solid #ebeef5; border-radius: 8px; overflow: hidden; }
.graph-tip { flex-shrink: 0; font-size: 11px; color: #c0c4cc; padding-top: 8px; }

.rg-node {
  width: 180px; height: 64px; border-radius: 10px; border: 2px solid #fff;
  display: flex; flex-direction: column; align-items: center; justify-content: center;
  cursor: pointer; gap: 3px; box-shadow: 0 1px 4px rgba(0,0,0,0.08); transition: 0.15s;
}
.rg-node:hover { transform: translateX(2px); box-shadow: 0 3px 10px rgba(0,0,0,0.14); }
.rg-node.active { border-color: #409eff; box-shadow: 0 0 0 3px rgba(64,158,255,0.25); }
.rg-tag { transform: scale(0.82); }
.rg-title { font-size: 12px; color: #303133; text-align: center; line-height: 1.3; padding: 0 6px; }
.rg-imp { font-size: 10px; color: #c0c4cc; }

/* 质检状态点: 红=高危 黄=提示 绿=通过 (未质检不显示) */
.qc-dot {
  display: inline-block; width: 8px; height: 8px; border-radius: 50%;
  flex-shrink: 0; vertical-align: middle;
}
.qc-dot.qc-red { background: #f56c6c; box-shadow: 0 0 0 2px rgba(245, 108, 108, 0.25); }
.qc-dot.qc-yellow { background: #e6a23c; box-shadow: 0 0 0 2px rgba(230, 162, 60, 0.25); }
.qc-dot.qc-green { background: #67c23a; box-shadow: 0 0 0 2px rgba(103, 194, 58, 0.2); }
.qc-dot.lg { width: 10px; height: 10px; }
.qc-dot.sm { width: 6px; height: 6px; }

/* 内容块问题区 */
.cc-issues { margin-top: 8px; border-top: 1px dashed #ebeef5; padding-top: 8px; }
.cc-issue { display: flex; align-items: center; gap: 6px; padding: 3px 0; font-size: 12px; }
.iss-msg { flex: 1; color: #606266; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

/* 检查记录抽屉的条目卡 */
.issue-card {
  border: 1px solid #ebeef5; border-left-width: 3px; border-radius: 8px;
  padding: 10px 12px; margin-bottom: 10px; background: #fff;
}
.issue-card.sev-high { border-left-color: #f56c6c; }
.issue-card.sev-medium { border-left-color: #e6a23c; }
.issue-card.sev-low { border-left-color: #909399; }
.ic-head { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
.ic-title { font-size: 13px; font-weight: 500; flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.ic-meta { display: flex; align-items: center; gap: 6px; margin-top: 6px; }
.ic-date { font-size: 11px; color: #c0c4cc; }
.ic-msg { font-size: 13px; color: #303133; line-height: 1.7; margin: 8px 0 0; }
.ic-actions { margin-top: 8px; display: flex; gap: 8px; }

/* 抽屉头 */
.drawer-head { display: flex; align-items: center; gap: 10px; }
.report-md { padding: 0 6px; }
.report-md :deep(.md-empty) { color: #909399; }
</style>

<!-- Markdown 渲染样式 (非 scoped, 作用于 v-html) -->
<style>
.report-md h1, .report-md h2, .report-md h3 { margin: 14px 0 8px; }
.report-md h1 { font-size: 18px; }
.report-md h2 { font-size: 16px; border-bottom: 1px solid #ebeef5; padding-bottom: 6px; }
.report-md h3 { font-size: 14px; }
.report-md p { margin: 8px 0; line-height: 1.8; }
.report-md ul, .report-md ol { padding-left: 22px; margin: 6px 0; }
.report-md li { margin: 4px 0; line-height: 1.7; }
.report-md blockquote {
  margin: 8px 0; padding: 8px 12px; border-left: 3px solid #e6a23c;
  background: #fdf6ec; border-radius: 0 4px 4px 0; color: #7a5a2b; font-size: 13px;
}
.report-md code {
  background: #f0f2f5; padding: 1px 5px; border-radius: 4px;
  font-size: 12px; color: #c7254e;
}
.report-md pre {
  background: #1e1e1e; color: #d4d4d4; padding: 12px; border-radius: 6px;
  overflow-x: auto; font-size: 12.5px; line-height: 1.6;
}
.report-md pre code { background: none; color: inherit; padding: 0; }
.report-md table { border-collapse: collapse; margin: 10px 0; width: 100%; }
.report-md th, .report-md td { border: 1px solid #ebeef5; padding: 6px 10px; font-size: 13px; text-align: left; }
.report-md th { background: #f5f7fa; }
.report-md hr { border: none; border-top: 1px solid #ebeef5; margin: 14px 0; }
.report-md strong { color: #303133; }
</style>
