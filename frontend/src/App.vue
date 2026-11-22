<template>
  <div class="app-shell">
    <aside class="app-side">
      <h1 class="app-title">特征工程与数据挖掘工作台</h1>
      <nav class="nav-list">
        <RouterLink v-for="item in navItems" :key="item.path" :to="item.path" class="nav-item">
          {{ item.label }}
        </RouterLink>
      </nav>
    </aside>
    <main class="app-main">
      <header class="app-head">
        <span class="head-desc">面向特征构建、特征选择、数据切片、模型训练与挖掘结果管理的数据分析特征工程平台。</span>
        <span class="head-user">当前值班：数据工程组</span>
      </header>
      <RouterView />
    </main>
  </div>
</template>

<script setup lang="ts">
const navItems = [{ label: "运营概览", path: "/" }, { label: "模型评估验收", path: "/acceptance" }, { label: "特征源管理", path: "/feature_source" }, { label: "特征定义", path: "/feature_define" }, { label: "特征组管理", path: "/feature_group" }, { label: "数据切片配置", path: "/data_slice" }, { label: "特征计算任务", path: "/feature_compute" }, { label: "特征筛选策略", path: "/feature_selection" }, { label: "特征重要性分析", path: "/feature_iv" }, { label: "特征快照管理", path: "/feature_snapshot" }, { label: "训练流水线", path: "/train_pipeline" }, { label: "模型注册中心", path: "/model_registry" }, { label: "批量预测任务", path: "/predict_batch" }, { label: "挖掘结果管理", path: "/mining_result" }, { label: "聚类分析配置", path: "/cluster_config" }, { label: "特征相关性矩阵", path: "/feature_corr" }, { label: "特征漂移监控", path: "/drift_monitor" }, { label: "数据导出管理", path: "/data_export" }, { label: "流水线调度", path: "/pipeline_schedule" }, { label: "业务场景标注", path: "/scenario_label" }]
</script>
