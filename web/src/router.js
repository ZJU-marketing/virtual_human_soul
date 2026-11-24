import { createRouter, createWebHistory } from 'vue-router'

// 路由结构 = 业务域(路径前缀) / 子页
const routes = [
  {
    path: '/',
    redirect: '/forge',
  },
  {
    path: '/forge',
    name: '血肉铸造台 · 铸造',
    component: () => import('./views/ForgeView.vue'),
  },
  {
    path: '/forge/jobs',
    name: '血肉铸造台 · 任务列表',
    component: () => import('./views/JobsView.vue'),
  },
  {
    path: '/inspect',
    redirect: '/inspect/check',
  },
  {
    path: '/inspect/personas',
    name: '人物质检室 · 人物列表',
    component: () => import('./views/PersonasView.vue'),
  },
  {
    path: '/inspect/time-line/:pid',
    name: '人物质检室 · 时间线',
    component: () => import('./views/TimelineView.vue'),
  },
  {
    path: '/inspect/check',
    name: '人物质检室 · 检查',
    component: () => import('./views/InspectCheckView.vue'),
  },
  {
    path: '/inspect/edit',
    name: '人物质检室 · 修改',
    component: () => import('./views/InspectEditView.vue'),
  },
  {
    path: '/simulate',
    name: '人物模拟厅 · 对话',
    component: () => import('./views/SimulateView.vue'),
  },
  {
    path: '/docs',
    name: '系统 · API 文档',
    component: () => import('./views/DocsView.vue'),
  },
  { path: '/:pathMatch(.*)*', redirect: '/forge' },
]

export const router = createRouter({
  history: createWebHistory(),
  routes,
})
