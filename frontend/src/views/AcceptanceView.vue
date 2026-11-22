<template>
  <section class="page">
    <header class="page-head">
      <div>
        <h2>模型评估验收</h2>
        <p class="page-desc">
          按必评样本清单做组验收：每份样本都必须有效且不低于 {{ threshold }} 才放行；
          零分正常入榜，空值待补分，NaN / 无穷 / 非数值标为无效并保留原因。
        </p>
      </div>
      <div class="page-actions">
        <button class="btn primary" data-testid="open-acceptance" type="button" @click="openCreate">
          新建验收申请
        </button>
      </div>
    </header>

    <div class="stat-row">
      <article class="stat-card">
        <span class="stat-label">验收申请</span>
        <strong class="stat-value">{{ applications.length }}</strong>
      </article>
      <article v-for="item in statusCards" :key="item.status" class="stat-card">
        <span class="stat-label">{{ item.status }}</span>
        <strong class="stat-value">{{ item.count }}</strong>
      </article>
    </div>

    <table class="data-table">
      <thead>
        <tr>
          <th>验收编号</th>
          <th>组标识</th>
          <th>必评样本</th>
          <th>策略</th>
          <th>指标版本</th>
          <th>状态</th>
          <th>结论</th>
          <th>操作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in applications" :key="row.id" :data-testid="`acceptance-${row.id}`">
          <td>{{ row['验收编号'] }}</td>
          <td>{{ row['组标识'] }}</td>
          <td>{{ row['必评样本清单'].join('、') }}</td>
          <td>{{ row['验收策略'] }}</td>
          <td>{{ row['指标版本'] }}</td>
          <td>{{ row.status }}</td>
          <td>{{ row.conclusion }}</td>
          <td class="row-actions">
            <button class="link" type="button" @click="select(row)">查看回执</button>
            <button
              v-if="row.status === '待补证'"
              class="link"
              type="button"
              @click="openSubmit(row)"
            >
              提交 / 补录
            </button>
            <button
              v-if="row.status === '待补证'"
              class="link"
              type="button"
              @click="closeEarly(row)"
            >
              提前结案
            </button>
            <button class="link" type="button" @click="openRegistry(row)">注册中心记录</button>
          </td>
        </tr>
        <tr v-if="!applications.length">
          <td colspan="8" class="empty-state">暂无验收申请，可先新建一份</td>
        </tr>
      </tbody>
    </table>

    <p v-if="errorMessage" class="error-text">{{ errorMessage }}</p>

    <section v-if="activeReceipt" class="panel">
      <h3>第 {{ activeReceipt.round }} 轮回执 · {{ activeReceipt.conclusion }}</h3>
      <p>{{ activeReceipt.message }}</p>
      <ul class="plain-list">
        <li>可补录：{{ activeReceipt.supplementation }}</li>
        <li>注册：{{ activeReceipt.registration }}</li>
        <li>指标版本：{{ activeReceipt.metric_version }}（门槛 {{ activeReceipt.threshold }}）</li>
        <li>最低分 / 均分：{{ activeReceipt.min_score ?? '—' }} / {{ activeReceipt.mean_score ?? '—' }}</li>
      </ul>
      <table class="data-table">
        <thead>
          <tr><th>样本</th><th>状态</th><th>评分</th><th>原因</th><th>来源</th><th>锁定</th></tr>
        </thead>
        <tbody>
          <tr v-for="check in receiptChecks" :key="check.sample_id">
            <td>{{ check.sample_id }}</td>
            <td>{{ check.status }}</td>
            <td>{{ check.score ?? '—' }}</td>
            <td>{{ check.reason || '—' }}</td>
            <td>{{ check.source || '—' }}</td>
            <td>{{ check.locked ? '是' : '否' }}</td>
          </tr>
        </tbody>
      </table>
      <p v-for="warning in activeReceipt.warnings" :key="warning" class="warn-text">{{ warning }}</p>
    </section>

    <section v-if="registryRecord" class="panel">
      <h3>注册中心记录 · {{ registryRecord['验收编号'] }}</h3>
      <ul class="plain-list">
        <li>状态：{{ registryRecord.status }} / {{ registryRecord['验收结论'] }}</li>
        <li>最初指标版本：{{ registryRecord['最初指标版本'] }}</li>
        <li>评估分数：{{ registryRecord['评估分数'] || '—' }}</li>
        <li>注册控制：{{ registryRecord['注册控制'] }}</li>
      </ul>
      <table class="data-table" v-if="registryExceptions.length">
        <thead>
          <tr><th>样本</th><th>状态</th><th>代码</th><th>原因</th></tr>
        </thead>
        <tbody>
          <tr v-for="(row, index) in registryExceptions" :key="index">
            <td>{{ row['样本编号'] }}</td>
            <td>{{ row['状态'] }}</td>
            <td>{{ row['代码'] }}</td>
            <td>{{ row['原因'] }}</td>
          </tr>
        </tbody>
      </table>
    </section>

    <div v-if="showCreate" class="modal-backdrop">
      <form class="modal" @submit.prevent="submitCreate">
        <h3>新建验收申请</h3>
        <label class="create-field">
          <span>必评样本清单（逗号分隔）</span>
          <input v-model="createForm.samples" data-testid="create-samples" required />
        </label>
        <label class="create-field">
          <span>组标识</span>
          <input v-model="createForm.groupId" data-testid="create-group" />
        </label>
        <label class="create-field">
          <span>指标版本</span>
          <input v-model="createForm.metricVersion" data-testid="create-version" />
        </label>
        <label class="create-field">
          <span>验收策略</span>
          <select v-model="createForm.policy" data-testid="create-policy">
            <option value="early_close">提前结案（early_close）</option>
            <option value="wait_complete">等待完整证据（wait_complete）</option>
          </select>
        </label>
        <div class="page-actions">
          <button class="btn" type="button" @click="showCreate = false">取消</button>
          <button class="btn primary" type="submit">创建</button>
        </div>
      </form>
    </div>

    <div v-if="submitTarget" class="modal-backdrop">
      <form class="modal" @submit.prevent="submitScoresFor">
        <h3>提交评分 · {{ submitTarget['验收编号'] }}</h3>
        <p class="page-desc">
          空字符串或留空表示待补分；填 NaN / Infinity 会被标为无效。已确认有效的分数不会被覆盖。
        </p>
        <label v-for="sample in submitTarget['必评样本清单']" :key="sample" class="create-field">
          <span>{{ sample }}</span>
          <input v-model="submitForm[sample]" :data-testid="`score-${sample}`" />
        </label>
        <div class="page-actions">
          <button class="btn" type="button" @click="submitTarget = null">取消</button>
          <button class="btn primary" type="submit">提交</button>
        </div>
      </form>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import {
  closeApplication,
  createApplication,
  listApplications,
  loadRegistryRecord,
  submitScores,
} from '@/api/acceptance-service'
import type { AcceptanceApplication, AcceptanceReceipt, ScoreCheck } from '@/data/types'

const threshold = 0.7
const applications = ref<AcceptanceApplication[]>([])
const errorMessage = ref('')
const activeReceipt = ref<AcceptanceReceipt | null>(null)
const registryRecord = ref<Record<string, unknown> | null>(null)
const showCreate = ref(false)
const createForm = ref({ samples: 'A,B,C', groupId: '', metricVersion: '', policy: 'early_close' })
const submitTarget = ref<AcceptanceApplication | null>(null)
const submitForm = ref<Record<string, string>>({})

const statusCards = computed(() =>
  ['待补证', '已通过', '不通过'].map((status) => ({
    status,
    count: applications.value.filter((row) => row.status === status).length,
  })),
)

const receiptChecks = computed<ScoreCheck[]>(() => {
  const receipt = activeReceipt.value
  if (!receipt) return []
  const merged = [...receipt.ranked, ...receipt.pending, ...receipt.invalid]
  return merged.sort((a, b) => a.sample_id.localeCompare(b.sample_id))
})

const registryExceptions = computed<Record<string, unknown>[]>(() => {
  const rows = registryRecord.value?.['例外记录']
  return Array.isArray(rows) ? (rows as Record<string, unknown>[]) : []
})

function parseScore(raw: string): unknown {
  const text = raw.trim()
  if (text === '') return ''
  // NaN / Infinity 以字符串提交：JSON 没有这两种字面量（JSON.stringify 会把
  // Number.NaN 变成 null，反而被当成待补分）。后端会按「无效」并保留原因。
  if (['NaN', '+NaN', '-NaN', 'Infinity', '+Infinity', '-Infinity', 'inf', '-inf'].includes(text)) {
    return text
  }
  const numeric = Number(text)
  return Number.isNaN(numeric) ? text : numeric
}

async function reload() {
  errorMessage.value = ''
  try {
    const payload = await listApplications()
    applications.value = payload.items
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '验收申请读取失败'
  }
}

function openCreate() {
  createForm.value = { samples: 'A,B,C', groupId: '', metricVersion: '', policy: 'early_close' }
  showCreate.value = true
}

async function submitCreate() {
  errorMessage.value = ''
  try {
    await createApplication({
      required_samples: createForm.value.samples
        .split(',')
        .map((item) => item.trim())
        .filter(Boolean),
      group_id: createForm.value.groupId || undefined,
      metric_version: createForm.value.metricVersion || undefined,
      policy: createForm.value.policy,
    })
    showCreate.value = false
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '创建失败'
  }
}

function select(row: AcceptanceApplication) {
  activeReceipt.value = row.receipt
  registryRecord.value = null
}

function openSubmit(row: AcceptanceApplication) {
  submitTarget.value = row
  submitForm.value = Object.fromEntries(row['必评样本清单'].map((sample) => [sample, '']))
}

async function submitScoresFor() {
  if (!submitTarget.value) return
  errorMessage.value = ''
  const scores: Record<string, unknown> = {}
  for (const sample of submitTarget.value['必评样本清单']) {
    const raw = submitForm.value[sample] ?? ''
    if (raw.trim() === '') continue
    scores[sample] = parseScore(raw)
  }
  try {
    const result = await submitScores(submitTarget.value.id, scores)
    activeReceipt.value = result.receipt
    submitTarget.value = null
    await reload()
    if (!result.ok) errorMessage.value = result.message
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '提交失败'
  }
}

async function closeEarly(row: AcceptanceApplication) {
  errorMessage.value = ''
  try {
    const result = await closeApplication(row.id, '复核员在工作台提前结案')
    await reload()
    const updated = applications.value.find((item) => item.id === row.id)
    activeReceipt.value = updated?.receipt ?? null
    if (!result.ok) errorMessage.value = result.message
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '结案失败'
  }
}

async function openRegistry(row: AcceptanceApplication) {
  errorMessage.value = ''
  try {
    registryRecord.value = await loadRegistryRecord(row['验收编号'])
  } catch (error) {
    registryRecord.value = null
    errorMessage.value = error instanceof Error ? error.message : '注册记录读取失败'
  }
}

onMounted(reload)
</script>

<style scoped>
.panel {
  margin-top: 16px;
  padding: 14px;
  background: #fff;
  border: 1px solid var(--border);
  border-radius: 8px;
}
.plain-list {
  margin: 8px 0 12px;
  padding-left: 18px;
  font-size: 13px;
  color: var(--muted);
}
.warn-text {
  color: #b54708;
  font-size: 12px;
}
select {
  width: 100%;
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 8px;
}
</style>
