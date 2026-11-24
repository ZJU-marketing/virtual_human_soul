<template>
  <section class="page chat-page">
    <!-- ============ 顶部: 人物选择 + 会话操作 ============ -->
    <div class="chat-toolbar">
      <div class="persona-pick">
        <div class="avatar-sm" :class="{ empty: !currentPersona }">
          {{ currentPersona?.name?.slice(0, 1) || '?' }}
        </div>
        <div class="pick-info">
          <div class="pick-name">
            {{ currentPersona?.name || '选择对话人物' }}
            <el-tag v-if="currentPersona" size="small" type="success" effect="plain">{{ currentPersona.status }}</el-tag>
          </div>
          <div class="pick-sub">{{ currentPersona ? currentPersona.slug : '选择一个人物开始对话' }}</div>
        </div>
        <el-select
          v-model="chatTarget" placeholder="选择人物" style="width: 240px" @change="onPersonaChange"
        >
          <el-option v-for="p in personas" :key="p.id" :value="p.id" :label="`${p.name} (${p.slug})`" />
        </el-select>
      </div>

      <div class="toolbar-spacer" />

      <el-tag v-if="sessionId" size="small" type="info" effect="plain">
        会话 {{ sessionId.slice(0, 8) }}…
      </el-tag>
      <el-button text :icon="FolderOpened" :disabled="!chatTarget" @click="openDocs">知识文档</el-button>
      <el-button text :icon="RefreshLeft" :disabled="!messages.length" @click="newSession">新会话</el-button>
    </div>

    <el-alert
      v-if="!chatTarget" type="info" :closable="false" show-icon
      title="先在右上角选择一个人物，即可开始对话"
    />

    <!-- ============ 聊天区 ============ -->
    <div v-else class="chat-window" ref="chatWindow">
      <el-empty
        v-if="!messages.length" :image-size="90"
        :description="`和「${currentPersona?.name || 'TA'}」聊聊吧——可以问经历、聊想法、追问细节`"
      />

      <template v-for="(m, i) in messages" :key="i">
        <div :class="['msg-row', m.role]">
          <div v-if="m.role === 'assistant'" class="avatar" :style="{ background: avatarColor }">
            {{ currentPersona?.name?.slice(0, 1) || 'AI' }}
          </div>
          <div class="bubble-wrap">
            <!-- 本轮轨迹: 思考 + 具体工具调用 + 耗时 -->
            <div v-if="m.role === 'assistant' && (traceOf(m).length || m.recall)" class="trace-box">
              <div class="trace-head">
                <span class="trace-label">本轮过程</span>
                <template v-if="traceOf(m).length">
                  <el-tag size="small" type="info" effect="plain">
                    思考 {{ sumMs(traceOf(m), 'thinking') }}
                  </el-tag>
                  <el-tag size="small" type="success" effect="plain">
                    工具 {{ countTools(traceOf(m)) }} 次 · {{ sumMs(traceOf(m), 'tool') }}
                  </el-tag>
                </template>
                <div class="toolbar-spacer" />
                <el-button link size="small" @click="toggleTrace(i)">
                  {{ expandedTrace.has(i) || m.live ? '收起' : '展开过程' }}
                </el-button>
              </div>

              <div v-if="expandedTrace.has(i) || m.live" class="trace-list">
                <div v-for="(st, j) in traceOf(m)" :key="j" class="trace-item">
                  <template v-if="st.type === 'thinking'">
                    <el-tag size="small" type="info" effect="plain">
                      思考{{ st.turn ? ` ${st.turn}` : '' }}
                    </el-tag>
                    <span class="trace-dur">{{ st.duration_ms != null ? fmtMs(st.duration_ms) : '…' }}</span>
                    <span v-if="st.live" class="trace-live">思考中…</span>
                    <div v-if="st.text" class="think-text">{{ st.text }}</div>
                  </template>
                  <template v-else>
                    <el-tag size="small" type="success" effect="plain" :title="st.name">
                      {{ toolLabel(st.name) }}
                    </el-tag>
                    <span class="trace-tool-name">{{ st.name }}</span>
                    <span class="trace-dur">{{ st.duration_ms != null ? fmtMs(st.duration_ms) : '调用中…' }}</span>
                    <span class="trace-sum">{{ st.summary }}</span>
                    <span v-if="st.args && Object.keys(st.args).length" class="trace-args"
                          :title="argsText(st.args)">{{ argsText(st.args) }}</span>
                    <div v-if="st.blocks?.length" class="trace-blocks">
                      <div v-for="(b, k) in st.blocks" :key="k" class="rc-item">
                        <span class="rc-dot" :class="b.source === 'summary' ? 'sum' : 'hit'"></span>
                        <span class="rc-src">{{ b.source === 'memory' ? '情景' : (b.source === 'summary' ? '骨架' : '内容') }}</span>
                        <span class="rc-title">{{ b.title }}{{ b.label ? ` · ${b.label}` : '' }}</span>
                        <span class="rc-score">{{ (b.score ?? 0).toFixed(2) }}</span>
                      </div>
                    </div>
                  </template>
                </div>

                <!-- 兼容旧数据: 老 recall 结构 -->
                <div v-if="!traceOf(m).length && m.recall?.blocks" class="trace-blocks">
                  <div v-for="b in m.recall.blocks" :key="b.title + (b.label || '')" class="rc-item">
                    <span class="rc-dot" :class="b.source === 'summary' ? 'sum' : 'hit'"></span>
                    <span class="rc-src">{{ b.source === 'memory' ? '情景' : (b.source === 'summary' ? '骨架' : '内容') }}</span>
                    <span class="rc-title">{{ b.title }}{{ b.label ? ` · ${b.label}` : '' }}</span>
                    <span class="rc-score">{{ (b.score ?? 0).toFixed(2) }}</span>
                  </div>
                </div>
              </div>
            </div>

            <div class="bubble">
              <span v-if="m.text">{{ m.text }}</span>
              <span v-else-if="m.pending" class="typing"><i></i><i></i><i></i></span>
            </div>
            <div v-if="m.role === 'user' && m.docs?.length" class="msg-refs">
              <el-tag v-for="r in m.docs" :key="r" size="small" type="success" effect="plain">
                📄 {{ baseName(r) }}
              </el-tag>
            </div>
            <div class="msg-time">{{ m.time }}</div>
          </div>
          <div v-if="m.role === 'user'" class="avatar user">我</div>
        </div>
      </template>
    </div>

    <!-- ============ 输入区 ============ -->
    <div v-if="chatTarget" class="chat-input-bar">
      <div v-if="docRefs.length" class="ref-bar">
        <span class="ref-label">引用文档:</span>
        <el-tag
          v-for="r in docRefs" :key="r" size="small" type="success" effect="plain"
          closable @close="removeDocRef(r)"
        >📄 {{ baseName(r) }}</el-tag>
      </div>
      <el-upload
        class="attach-upload"
        action="#" :http-request="uploadDoc" :show-file-list="false"
        accept=".docx,.md,.txt,.csv,.json"
      >
        <el-tooltip content="上传文档到该人物的知识文档 (RustFS)，同名覆盖" placement="top">
          <el-button class="attach-btn" text :icon="Paperclip" :loading="docsUploading" />
        </el-tooltip>
      </el-upload>
      <el-input
        v-model="chatInput" type="textarea" :rows="2" resize="none"
        :placeholder="`对「${currentPersona?.name || 'TA'}」说点什么… (Enter 发送, Shift+Enter 换行)`"
        @keydown.enter.exact.prevent="doChat" :disabled="chatting"
      />
      <div class="send-col">
        <el-button
          type="primary" :icon="Promotion" :loading="chatting"
          :disabled="!chatInput.trim()" @click="doChat"
        >发送</el-button>
      </div>
    </div>

    <!-- ============ Agent 知识文档 (RustFS) ============ -->
    <el-drawer
      v-model="docsOpen" direction="rtl" size="540px"
      :title="`知识文档 · ${currentPersona?.name || ''}`"
    >
      <div class="docs-hint">
        存储于 RustFS: <code>{{ chatTarget }}/docs/</code>，对话中 agent 可读取并覆盖写回
      </div>
      <div class="docs-toolbar">
        <el-upload
          action="#" :http-request="uploadDoc" :show-file-list="false"
          accept=".docx,.md,.txt,.csv,.json"
        >
          <el-button type="primary" size="small" :icon="Upload" :loading="docsUploading">
            上传文档
          </el-button>
        </el-upload>
        <el-button size="small" :icon="Refresh" :loading="docsLoading" @click="loadDocs">刷新</el-button>
        <span class="docs-tip">docx 可预览抽取正文；同名上传会覆盖原文件</span>
      </div>

      <el-tree
        :data="docsTree" node-key="path" default-expand-all
        :expand-on-click-node="false" v-loading="docsLoading"
        empty-text="docs 目录暂无文档"
      >
        <template #default="{ data }">
          <div class="doc-node">
            <el-icon class="doc-ico" :class="{ dir: data.isDir, file: data.isFile }">
              <Folder v-if="data.isDir" />
              <Document v-else />
            </el-icon>
            <span class="doc-name" :class="{ dir: data.isDir }">{{ data.label }}</span>
            <template v-if="data.isFile">
              <span class="doc-size">{{ fmtSize(data.size) }}</span>
              <span class="doc-ops">
                <el-button link type="success" size="small" @click.stop="addDocRef(data.path)">引用</el-button>
                <el-button link type="primary" size="small" @click.stop="previewDoc(data)">预览</el-button>
                <el-button link type="primary" size="small" @click.stop="downloadDoc(data)">下载</el-button>
                <el-button link type="danger" size="small" @click.stop="deleteDoc(data)">删除</el-button>
              </span>
            </template>
            <span v-else class="doc-count">{{ data.children?.length ?? 0 }} 项</span>
          </div>
        </template>
      </el-tree>

      <el-dialog v-model="previewOpen" :title="previewPath" width="62%" append-to-body>
        <pre class="doc-preview">{{ previewText || '(空文档)' }}</pre>
      </el-dialog>
    </el-drawer>
  </section>
</template>

<script setup>
import { ref, computed, nextTick, watch, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Document, Folder, FolderOpened, Paperclip, Promotion, Refresh, RefreshLeft, Upload } from '@element-plus/icons-vue'
import { API, sse, personas, chatTarget, loadPersonas } from '../store'

const chatInput = ref('')
const chatting = ref(false)
const messages = ref([])
const chatWindow = ref(null)
const sessionId = ref(null)
const expandedRecall = ref(new Set())
const expandedTrace = ref(new Set())

// ---- 知识文档 (RustFS) ----
const docsOpen = ref(false)
const docsList = ref([])
const docsLoading = ref(false)
const docsUploading = ref(false)
const previewOpen = ref(false)
const previewPath = ref('')
const previewText = ref('')
const docRefs = ref([])   // 本轮输入引用的文档 (随消息发送, 发送后清空)

/** 扁平 path 列表 -> 文件树 (S3 无真实目录, 按 / 分段构建) */
function buildTree(docs) {
  const root = []
  for (const d of docs || []) {
    const segs = String(d.path || '').split('/').filter(Boolean)
    let level = root
    let acc = ''
    segs.forEach((seg, i) => {
      acc = acc ? `${acc}/${seg}` : seg
      const isFile = i === segs.length - 1
      let node = level.find((n) => n.label === seg && n.isDir === !isFile)
      if (!node) {
        node = isFile
          ? { label: seg, path: acc, isFile: true, size: d.size, last_modified: d.last_modified }
          : { label: seg, path: `${acc}/`, isDir: true, children: [] }
        level.push(node)
      }
      if (!isFile) level = node.children
    })
  }
  const sortNodes = (nodes) => {
    nodes.sort((a, b) => (a.isDir === b.isDir ? a.label.localeCompare(b.label) : a.isDir ? -1 : 1))
    nodes.forEach((n) => n.children && sortNodes(n.children))
  }
  sortNodes(root)
  return root
}

const docsTree = computed(() => buildTree(docsList.value))

const TOOL_LABEL = {
  RetrieveContextArgs: '检索记忆',
  LoadContentArgs: '加载内容块',
  RecallMemoryArgs: '回忆经历',
  LoadSkeletonArgs: '读取骨架',
  LoadTreeArgs: '浏览知识树',
  ListDocsArgs: '列出文档',
  ReadDocArgs: '读取文档',
  WriteDocArgs: '覆盖写入文档',
  retrieve_context: '检索记忆',
  load_content: '加载内容块',
  recall_memory: '回忆经历',
  load_skeleton: '读取骨架',
  load_tree: '浏览知识树',
  list_docs: '列出文档',
  read_doc: '读取文档',
  write_doc: '覆盖写入文档',
}
const toolLabel = (t) => TOOL_LABEL[t] || t

const currentPersona = computed(() => personas.value.find((p) => p.id === chatTarget.value) || null)
const avatarColor = computed(() => {
  const name = currentPersona.value?.name || ''
  const colors = ['#409eff', '#67c23a', '#e6a23c', '#f56c6c', '#909399', '#7c4dff']
  let h = 0
  for (const ch of name) h = (h * 31 + ch.charCodeAt(0)) % 1000
  return colors[h % colors.length]
})

function nowTime() {
  const d = new Date()
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
}

function scrollToBottom() {
  nextTick(() => {
    const el = chatWindow.value
    if (el) el.scrollTop = el.scrollHeight
  })
}

function toggleRecall(i) {
  const s = new Set(expandedRecall.value)
  if (s.has(i)) s.delete(i); else s.add(i)
  expandedRecall.value = s
}

function toggleTrace(i) {
  const s = new Set(expandedTrace.value)
  if (s.has(i)) s.delete(i); else s.add(i)
  expandedTrace.value = s
}

function traceOf(m) {
  return m.trace || (m.recall && m.recall.trace) || []
}

function fmtMs(ms) {
  if (ms == null) return '…'
  return ms < 1000 ? `${ms}ms` : `${(ms / 1000).toFixed(2)}s`
}

function sumMs(trace, type) {
  const total = (trace || [])
    .filter((x) => x.type === type)
    .reduce((a, x) => a + (x.duration_ms || 0), 0)
  return fmtMs(total)
}

function countTools(trace) {
  return (trace || []).filter((x) => x.type === 'tool').length
}

function argsText(args) {
  try {
    return Object.entries(args || {})
      .map(([k, v]) => `${k}=${typeof v === 'string' ? v : JSON.stringify(v)}`)
      .join(', ')
  } catch {
    return ''
  }
}

function newSession() {
  messages.value = []
  sessionId.value = null
  docRefs.value = []
  expandedRecall.value = new Set()
  expandedTrace.value = new Set()
}

// ---- 引用文档 (输入框 chips) ----
function baseName(p) {
  return String(p || '').split('/').pop()
}

function addDocRef(path) {
  if (!path) return
  if (!docRefs.value.includes(path)) {
    docRefs.value.push(path)
    ElMessage.success(`已引用 ${baseName(path)}`)
  }
}

function removeDocRef(path) {
  docRefs.value = docRefs.value.filter((x) => x !== path)
}

// ---- 知识文档 (RustFS {persona_id}/docs): 列表 / 上传(覆盖) / 预览 / 下载 / 删除 ----
function openDocs() {
  if (!chatTarget.value) return
  docsOpen.value = true
  loadDocs()
}

function fmtSize(n) {
  if (n == null) return '-'
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  return `${(n / 1024 / 1024).toFixed(2)} MB`
}

async function loadDocs() {
  if (!chatTarget.value) return
  docsLoading.value = true
  try {
    const r = await fetch(`${API}/api/personas/${chatTarget.value}/docs`)
    docsList.value = r.ok ? ((await r.json()).docs || []) : []
  } catch {
    docsList.value = []
  } finally {
    docsLoading.value = false
  }
}

/** el-upload 自定义上传: PUT 原始字节, 同名即覆盖 */
async function uploadDoc(options) {
  const file = options.file
  docsUploading.value = true
  try {
    const r = await fetch(
      `${API}/api/personas/${chatTarget.value}/docs?path=${encodeURIComponent(file.name)}`,
      {
        method: 'PUT',
        headers: { 'Content-Type': file.type || 'application/octet-stream' },
        body: file,
      },
    )
    const d = await r.json().catch(() => ({}))
    if (!r.ok) {
      ElMessage.error(d.detail || `上传失败 HTTP ${r.status}`)
      return
    }
    ElMessage.success(`已上传 ${d.path}，可在对话中让 agent 读取该文档`)
    addDocRef(d.path)   // 上传后自动引用到本轮输入
    await loadDocs()
  } finally {
    docsUploading.value = false
  }
}

async function previewDoc(row) {
  if (!chatTarget.value) return
  const r = await fetch(
    `${API}/api/personas/${chatTarget.value}/docs/text?path=${encodeURIComponent(row.path)}`,
  )
  const d = await r.json().catch(() => ({}))
  if (!r.ok) {
    ElMessage.error(d.detail || '预览失败')
    return
  }
  previewPath.value = row.path
  previewText.value = d.text || ''
  previewOpen.value = true
}

function downloadDoc(row) {
  if (!chatTarget.value) return
  const url = `${API}/api/personas/${chatTarget.value}/docs/download?path=${encodeURIComponent(row.path)}`
  window.open(url, '_blank')
}

async function deleteDoc(row) {
  if (!chatTarget.value) return
  try {
    await ElMessageBox.confirm(`删除文档 ${row.path}？`, '删除文档', {
      type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消',
    })
  } catch {
    return
  }
  const r = await fetch(
    `${API}/api/personas/${chatTarget.value}/docs?path=${encodeURIComponent(row.path)}`,
    { method: 'DELETE' },
  )
  const d = await r.json().catch(() => ({}))
  if (!r.ok) {
    ElMessage.error(d.detail || `删除失败 HTTP ${r.status}`)
    return
  }
  ElMessage.success('已删除')
  await loadDocs()
}

/** 从 checkpoint 恢复历史对话 (刷新/重进页面后继续) */
async function restoreHistory() {
  if (!chatTarget.value) return
  try {
    const q = sessionId.value ? `?session_id=${encodeURIComponent(sessionId.value)}` : ''
    const r = await fetch(`${API}/api/personas/${chatTarget.value}/chat/history${q}`)
    if (!r.ok) return
    const data = await r.json()
    const rows = data.messages || []
    if (!rows.length) return
    sessionId.value = data.session_id || sessionId.value
    messages.value = rows
      .filter((m) => m.role !== 'summary')
      .map((m) => ({
        role: m.role,
        text: m.content,
        docs: (m.recall && m.recall.docs) || [],
        recall: m.recall || null,
        trace: (m.recall && m.recall.trace) || [],
        live: false,
        time: m.created_at ? fmtIso(m.created_at) : '',
      }))
    // 兼容旧数据: recall 曾存在 user 消息上 → 迁移到紧随其后的 assistant 消息
    for (let i = 0; i < messages.value.length - 1; i++) {
      const cur = messages.value[i]
      const next = messages.value[i + 1]
      if (cur.recall?.blocks && next.role === 'assistant' && !next.recall) {
        next.recall = cur.recall
        cur.recall = null
      }
    }
    scrollToBottom()
  } catch { /* ignore */ }
}

function fmtIso(iso) {
  try {
    const d = new Date(iso)
    return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
  } catch { return '' }
}

function onPersonaChange() {
  newSession()
}

async function doChat() {
  const text = chatInput.value.trim()
  if (!text || chatting.value || !chatTarget.value) return
  chatting.value = true
  chatInput.value = ''

  const refs = [...docRefs.value]
  docRefs.value = []
  messages.value.push({ role: 'user', text, docs: refs, time: nowTime() })
  const aiIdx = messages.value.length
  messages.value.push({
    role: 'assistant', text: '', pending: true, time: nowTime(),
    trace: [], live: true,
  })
  scrollToBottom()

  try {
    await sse(`${API}/api/personas/${chatTarget.value}/chat`, {
      message: text, session_id: sessionId.value, docs: refs,
    }, (ev) => {
      const m = messages.value[aiIdx]
      if (ev.type === 'recall') {
        m.recall = ev.data       // 兼容旧服务端
      } else if (ev.type === 'thinking_start') {
        m.trace.push({
          type: 'thinking', turn: ev.data?.turn,
          text: '', duration_ms: null, live: true,
        })
        scrollToBottom()
      } else if (ev.type === 'thinking') {
        let st = [...(m.trace || [])].reverse().find((x) => x.type === 'thinking')
        if (!st) {
          st = { type: 'thinking', turn: null, text: '', duration_ms: null, live: true }
          m.trace.push(st)
        }
        st.text += ev.data.text || ''
        scrollToBottom()
      } else if (ev.type === 'thinking_end') {
        const st = [...(m.trace || [])].reverse().find((x) => x.type === 'thinking')
        if (st) {
          st.duration_ms = ev.data?.duration_ms
          st.live = false
        }
      } else if (ev.type === 'tool_start') {
        m.trace.push({
          type: 'tool', id: ev.data.id, name: ev.data.name, args: ev.data.args || {},
          summary: '调用中…', duration_ms: null, blocks: [], live: true,
        })
        scrollToBottom()
      } else if (ev.type === 'tool_end') {
        const st = [...(m.trace || [])].reverse().find(
          (x) => x.type === 'tool' && (x.id === ev.data.id || (x.name === ev.data.name && x.live)),
        )
        if (st) {
          st.duration_ms = ev.data.duration_ms
          st.summary = ev.data.summary || ''
          st.blocks = ev.data.blocks || []
          st.live = false
        }
        scrollToBottom()
      } else if (ev.type === 'chunk') {
        m.pending = false
        m.text += ev.data
        scrollToBottom()
      } else if (ev.type === 'done') {
        sessionId.value = ev.data.session_id
        m.pending = false
        m.live = false
        m.time = nowTime()
        if (m.trace?.length) {
          const set = new Set(expandedTrace.value)
          set.add(aiIdx)
          expandedTrace.value = set
        }
      } else if (ev.type === 'error') {
        m.pending = false
        m.live = false
        m.text = `[出错了] ${ev.data}`
      }
    })
  } catch (e) {
    messages.value[aiIdx].pending = false
    messages.value[aiIdx].live = false
    messages.value[aiIdx].text = `[请求失败] ${e}`
  } finally {
    chatting.value = false
    scrollToBottom()
  }
}

watch(chatTarget, async () => {
  newSession()
  if (docsOpen.value) await loadDocs()
  await restoreHistory()   // 切人物: 恢复该人物最近的会话
})
onMounted(async () => {
  await loadPersonas()
  if (chatTarget.value) {
    await restoreHistory()
  }
})
</script>

<style scoped>
.chat-page {
  display: flex; flex-direction: column;
  flex: 1; min-height: 0;
  padding-bottom: 12px;
  width: clamp(1000px, 62vw, 1800px);
  max-width: 100%;
  margin: 0 auto;
}

/* 顶部人物栏 */
.chat-toolbar {
  flex-shrink: 0; display: flex; align-items: center; gap: 12px;
  background: #fff; border: 1px solid #e4e7ed; border-radius: 8px;
  padding: 10px 14px; margin-bottom: 12px;
}
.persona-pick { display: flex; align-items: center; gap: 12px; }
.avatar-sm {
  width: 38px; height: 38px; border-radius: 10px; flex-shrink: 0;
  background: linear-gradient(135deg, #409eff, #67c23a); color: #fff;
  display: grid; place-items: center; font-size: 17px; font-weight: 700;
}
.avatar-sm.empty { background: #dcdfe6; }
.pick-info { min-width: 0; }
.pick-name { font-size: 14px; font-weight: 600; display: flex; align-items: center; gap: 6px; }
.pick-sub { font-size: 11px; color: #c0c4cc; margin-top: 2px; }
.toolbar-spacer { flex: 1; }

/* 聊天区 */
.chat-window {
  flex: 1; min-height: 0; overflow-y: auto; background: #fff;
  border: 1px solid #e4e7ed; border-radius: 8px; padding: 20px 24px;
  display: flex; flex-direction: column; gap: 4px;
}

/* 消息行 */
.msg-row { display: flex; gap: 10px; margin-bottom: 14px; align-items: flex-start; }
.msg-row.user { flex-direction: row; justify-content: flex-end; }
.avatar {
  width: 34px; height: 34px; border-radius: 10px; flex-shrink: 0;
  display: grid; place-items: center; color: #fff; font-size: 15px; font-weight: 600;
}
.avatar.user { background: #c8d6e5; color: #4a5a6a; }
.bubble-wrap { max-width: 68%; min-width: 0; }
.msg-row.user .bubble-wrap { display: flex; flex-direction: column; align-items: flex-end; }
.bubble {
  background: #f4f6f8; border-radius: 12px; padding: 10px 14px;
  font-size: 13.5px; line-height: 1.85; color: #303133;
  white-space: pre-wrap; word-break: break-word;
}
.msg-row.user .bubble { background: #409eff; color: #fff; border-bottom-right-radius: 4px; }
.msg-row.assistant .bubble { border-bottom-left-radius: 4px; }
.msg-time { font-size: 10.5px; color: #c0c4cc; margin-top: 4px; }

/* 打字机 loading */
.typing { display: inline-flex; gap: 4px; align-items: center; height: 18px; }
.typing i {
  width: 6px; height: 6px; border-radius: 50%; background: #b0b8c1;
  animation: bounce 1.2s infinite ease-in-out;
}
.typing i:nth-child(2) { animation-delay: 0.15s; }
.typing i:nth-child(3) { animation-delay: 0.3s; }
@keyframes bounce {
  0%, 60%, 100% { transform: translateY(0); opacity: 0.5; }
  30% { transform: translateY(-4px); opacity: 1; }
}

/* 本轮过程: 思考 + 工具调用 + 耗时 */
.trace-box { margin: 2px 0 8px 44px; }
.trace-head {
  display: flex; align-items: center; gap: 6px;
  font-size: 11.5px; color: #909399;
}
.trace-label { color: #b5bcc4; }
.trace-list {
  margin-top: 6px; padding: 8px 10px;
  background: #f9fafb; border: 1px dashed #e4e7ed; border-radius: 6px;
  font-size: 11.5px;
}
.trace-item {
  display: flex; align-items: center; gap: 6px; flex-wrap: wrap;
  padding: 5px 0; border-bottom: 1px dashed #f0f2f5;
}
.trace-item:last-child { border-bottom: none; }
.trace-dur { color: #909399; font-family: Consolas, monospace; }
.trace-tool-name { font-size: 10px; color: #c0c4cc; font-family: Consolas, monospace; }
.trace-sum { color: #606266; }
.trace-args {
  color: #c0c4cc; max-width: 320px;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.trace-live { color: #409eff; }
.think-text {
  flex-basis: 100%; margin: 4px 0 0 4px; padding: 6px 8px;
  background: #fff; border-left: 2px solid #dcdfe6; border-radius: 0 4px 4px 0;
  color: #606266; line-height: 1.7; white-space: pre-wrap;
  max-height: 160px; overflow-y: auto;
}
.trace-blocks { flex-basis: 100%; margin-top: 4px; }

/* 旧版本 recall 样式 (兼容) */
.tools-row {
  display: flex; align-items: center; gap: 6px; flex-wrap: wrap;
  margin: 2px 0 6px 44px; font-size: 11.5px; color: #909399;
}
.tr-label { color: #b5bcc4; }
.tr-tag { transform: scale(0.92); }
.tr-meta { color: #c0c4cc; margin-left: 4px; }

.tools-detail {
  margin: 0 0 10px 44px; padding: 6px 10px;
  background: #f9fafb; border: 1px dashed #e4e7ed; border-radius: 6px;
  font-size: 11.5px;
}
.rc-item { display: flex; align-items: center; gap: 6px; padding: 3px 0; color: #606266; }
.rc-dot { width: 6px; height: 6px; border-radius: 50%; background: #c0c4cc; flex-shrink: 0; }
.rc-dot.hit { background: #67c23a; }
.rc-src { color: #a3c98f; flex-shrink: 0; }
.rc-title { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.rc-score { color: #c0c4cc; }

/* 输入区 */
.chat-input-bar {
  flex-shrink: 0; display: flex; gap: 10px; align-items: stretch; flex-wrap: wrap;
  background: #fff; border: 1px solid #e4e7ed; border-radius: 8px;
  padding: 10px 12px; margin-top: 12px;
}
.chat-input-bar :deep(.el-textarea__inner) {
  border: none; box-shadow: none; font-size: 13.5px; line-height: 1.7; padding: 4px 2px;
}
.chat-input-bar > :deep(.el-input) { flex: 1; min-width: 0; }
.send-col { display: flex; align-items: flex-end; }
.attach-upload { display: flex; align-items: flex-end; margin-bottom: 2px; }
.attach-btn { color: #909399; font-size: 18px; }
.attach-btn:hover { color: #409eff; }

/* 输入框引用文档 chips */
.ref-bar {
  flex-basis: 100%; display: flex; align-items: center; gap: 6px;
  flex-wrap: wrap; margin: 0 0 4px 2px; font-size: 12px;
}
.ref-label { color: #909399; }
.msg-refs {
  display: flex; gap: 4px; flex-wrap: wrap;
  margin-top: 4px; justify-content: flex-end;
}

/* 知识文档抽屉 */
.docs-hint {
  font-size: 12px; color: #909399; background: #fafcff;
  border: 1px dashed #d9ecff; border-radius: 6px; padding: 8px 10px; margin-bottom: 12px;
}
.docs-hint code { color: #409eff; font-family: Consolas, monospace; }
.docs-toolbar { display: flex; align-items: center; gap: 8px; margin-bottom: 10px; }
.docs-tip { font-size: 11px; color: #c0c4cc; }

/* 文件树 */
.doc-node {
  display: flex; align-items: center; gap: 6px; width: 100%;
  font-size: 12.5px; padding-right: 8px;
}
.doc-ico { color: #e6a23c; font-size: 14px; flex-shrink: 0; }
.doc-ico.file { color: #909399; }
.doc-name { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: #303133; }
.doc-name.dir { font-weight: 600; color: #606266; }
.doc-size { color: #c0c4cc; font-size: 11px; font-family: Consolas, monospace; flex-shrink: 0; }
.doc-count { color: #c0c4cc; font-size: 11px; flex-shrink: 0; }
.doc-ops { display: none; flex-shrink: 0; }
.doc-node:hover .doc-ops { display: inline-flex; }

.doc-preview {
  margin: 0; max-height: 60vh; overflow: auto; white-space: pre-wrap; word-break: break-word;
  background: #fafcff; border: 1px solid #e4e7ed; border-radius: 6px;
  padding: 12px 14px; font-size: 12.5px; line-height: 1.8; color: #303133;
}
</style>
