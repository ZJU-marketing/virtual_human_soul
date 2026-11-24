<template>
  <el-container class="layout">
    <!-- ===================== 页头: 业务域名 + 子菜单 (居中) ===================== -->
    <el-header class="header" height="56px">
      <div class="header-side left">
        <span class="domain">{{ currentDomain.label }}</span>
      </div>

      <el-menu mode="horizontal" :default-active="route.path" @select="go" class="header-submenu" :ellipsis="false">
        <el-menu-item v-for="c in currentDomain.children" :key="c.path" :index="c.path">
          {{ c.label }}
        </el-menu-item>
      </el-menu>

      <div class="header-side right">
        <el-tag v-if="activePersona" type="success" effect="plain" size="small">
          当前人物: {{ activePersona.name }}
        </el-tag>
      </div>
    </el-header>

    <el-container class="body">
      <!-- ===================== 左侧总菜单 (业务域) ===================== -->
      <el-aside width="220px" class="aside">
        <div class="brand">
          <span class="logo">虚</span>
          <span class="title">虚拟人铸造工坊</span>
        </div>
        <el-menu :default-active="domainKey" @select="switchDomain" class="side-menu">
          <el-menu-item v-for="d in domains" :key="d.key" :index="d.key">
            <el-icon><component :is="d.icon" /></el-icon>
            <span>{{ d.label }}</span>
          </el-menu-item>
        </el-menu>
        <div class="side-foot">{{ personas.length }} 位人物在库</div>
      </el-aside>

      <el-main class="main">
        <router-view />
      </el-main>
    </el-container>

    <!-- ===================== 页尾 ===================== -->
    <el-footer class="footer" height="36px">
      <span>virtual_human · 知识库 {{ personas.length }} 位人物</span>
      <span class="sep">|</span>
      <span>API <a :href="`${API}/docs`" target="_blank">{{ API }}</a></span>
      <span class="sep">|</span>
      <span>渐进式加载 · LangChain · pgvector</span>
    </el-footer>  </el-container>
</template>

<script setup>
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { MagicStick, CircleCheck, ChatDotRound, Monitor } from '@element-plus/icons-vue'
import { API, personas, inspectTarget, chatTarget } from './store'

const route = useRoute()
const router = useRouter()

// ---------------- 导航: 业务域 → 子页 (path 驱动) ----------------
const domains = [
  {
    key: 'forge', label: '血肉铸造台', icon: MagicStick, base: '/forge',
    children: [
      { label: '铸造', path: '/forge' },
      { label: '任务列表', path: '/forge/jobs' },
    ],
  },
  {
    key: 'inspect', label: '人物质检室', icon: CircleCheck, base: '/inspect',
    children: [
      { label: '检查', path: '/inspect/check' },
      { label: '修改', path: '/inspect/edit' },
      { label: '人物列表', path: '/inspect/personas' },
    ],
  },  {
    key: 'simulate', label: '人物模拟厅', icon: ChatDotRound, base: '/simulate',
    children: [{ label: '对话', path: '/simulate' }],
  },
  {
    key: 'system', label: '系统', icon: Monitor, base: '/docs',
    children: [{ label: 'API 文档', path: '/docs' }],
  },
]

const domainKey = computed(() => {
  const d = domains.find((x) => route.path.startsWith(x.base))
  return d?.key || 'forge'
})
const currentDomain = computed(() => domains.find((x) => x.key === domainKey.value) || domains[0])

function go(path) { router.push(path) }
function switchDomain(key) {
  const d = domains.find((x) => x.key === key)
  if (d) router.push(d.children[0].path)
}

// ---------------- 当前人物 (页头 tag) ----------------
const activePersona = computed(() => {
  const id = domainKey.value === 'simulate' ? chatTarget.value : inspectTarget.value
  return personas.value.find((p) => p.id === id) || null
})
</script>

<style>
* { margin: 0; box-sizing: border-box; }
html, body, #app { height: 100%; }
body { font-family: system-ui, 'Microsoft YaHei', sans-serif; }

.layout { height: 100vh; flex-direction: column; }

/* 页头: 三段式 (域名 | 居中子菜单 | 人物tag), 子菜单绝对居中 */
.header {
  position: relative; display: flex; align-items: center; justify-content: space-between;
  border-bottom: 1px solid #e4e7ed; background: #fff; padding: 0 24px;
}
.header-side { width: 240px; display: flex; }
.header-side.left { justify-content: flex-start; }
.header-side.right { justify-content: flex-end; }
.header-side .domain { font-size: 15px; font-weight: 600; }

.header-submenu {
  position: absolute; left: 50%; transform: translateX(-50%);   /* 居中 */
  border-bottom: none !important;
}

.body { flex: 1; overflow: hidden; }

/* 左侧总菜单 */
.aside { border-right: 1px solid #e4e7ed; display: flex; flex-direction: column; background: #fff; }
.brand { display: flex; align-items: center; gap: 10px; padding: 16px; }
.brand .logo {
  width: 32px; height: 32px; border-radius: 8px; background: linear-gradient(135deg, #409eff, #67c23a);
  color: #fff; font-weight: 700; display: grid; place-items: center; font-size: 16px;
}
.brand .title { font-size: 14px; font-weight: 600; line-height: 1.3; }
.side-menu { border-right: none; flex: 1; }
.side-foot { padding: 12px 16px; font-size: 12px; color: #c0c4cc; border-top: 1px solid #f0f0f0; }

/* el-main 外部不滚动: overflow hidden, 页面内容自己管理高度 (子 div 不撑大父级) */
.main { background: #fafafa; overflow: hidden; display: flex; flex-direction: column; padding-bottom: 0; }
.main > * { min-height: 0; }   /* 关键: 让 router-view 的子页不撑开 main */

/* 通用页面样式 (各 view 复用) */
.page { max-width: 1160px; margin: 0 auto; padding: 8px 0 24px; }
.page-head { margin-bottom: 16px; }
.page-head h3 { margin-bottom: 4px; }
.page-head .hint { color: #909399; font-size: 13px; }
.persona-picker { display: flex; align-items: center; gap: 12px; margin-bottom: 16px; }
.picker-label { font-size: 13px; color: #606266; }
.actions { margin: 12px 0; display: flex; gap: 8px; }
.stream-box {
  background: #1e1e1e; color: #d4d4d4; padding: 14px; border-radius: 8px;
  font-size: 13px; line-height: 1.6; max-height: 380px; overflow-y: auto;
  white-space: pre-wrap; word-break: break-all;
}

/* 对话页 */
.chat-window {
  flex: 1; overflow-y: auto; background: #fff; border-radius: 8px; padding: 16px;
  border: 1px solid #e4e7ed; min-height: 320px;
}
.msg { margin-bottom: 14px; }
.msg .who { font-size: 12px; color: #999; margin-bottom: 2px; }
.msg.user .text { background: #ecf5ff; display: inline-block; padding: 8px 12px; border-radius: 8px; }
.msg.assistant .text { background: #f5f7fa; display: inline-block; padding: 8px 12px; border-radius: 8px; }
.recall { background: #f0f9eb; border-radius: 8px; padding: 10px 14px; font-size: 12px; color: #67c23a; margin-bottom: 10px; }
.recall-item { padding-left: 12px; color: #909399; }
.chat-input { display: flex; gap: 8px; margin-top: 12px; }

/* 页尾 */
.footer {
  display: flex; align-items: center; justify-content: center; gap: 10px;
  border-top: 1px solid #e4e7ed; background: #fff;
  color: #909399; font-size: 12px;
}
.footer .sep { color: #dcdfe6; }
.footer a { color: #409eff; text-decoration: none; }
</style>
