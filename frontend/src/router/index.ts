import { createRouter, createWebHistory } from 'vue-router'

import AcceptanceView from '@/views/AcceptanceView.vue'
import Overview from '@/views/Overview.vue'
import WorkbenchView from '@/views/WorkbenchView.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'overview', component: Overview },
    // 固定路径必须放在动态 :moduleKey 之前，否则会被模块路由抢先匹配。
    { path: '/acceptance', name: 'acceptance', component: AcceptanceView },
    { path: '/:moduleKey', name: 'module', component: WorkbenchView, props: true },
  ],
})

export default router
