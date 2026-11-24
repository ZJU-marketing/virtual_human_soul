// 全局共享状态 (轻量 store): 人物列表 + 各上下文选择 + 通用 SSE 读取器

import { ref } from 'vue'

export const API = 'http://127.0.0.1:8300'

export const personas = ref([])
export const inspectTarget = ref(null)   // 质检上下文共享 (检查/修改/知识树)
export const chatTarget = ref(null)      // 模拟厅独立

export async function loadPersonas() {
  const r = await fetch(`${API}/api/personas`)
  personas.value = await r.json()
}

// SSE 读取器 (fetch + ReadableStream, 支持 POST)
export async function sse(url, body, onEvent) {
  const resp = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  const reader = resp.body.getReader()
  const decoder = new TextDecoder()
  let buf = ''
  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buf += decoder.decode(value, { stream: true })
    let idx
    while ((idx = buf.indexOf('\n\n')) >= 0) {
      const line = buf.slice(0, idx).trim()
      buf = buf.slice(idx + 2)
      if (line.startsWith('data: ')) onEvent(JSON.parse(line.slice(6)))
    }
  }
}
