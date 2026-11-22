/** 纯前端数据层的公共类型：与全栈版后端返回的结构保持一致，换回后端时页面不用改。 */

export type EntryRow = {
  id: number
  status: string
  pending: boolean
  abnormal: boolean
  [field: string]: string | number | boolean
}

export type ModuleMeta = {
  key: string
  name: string
  entity: string
  desc: string
  fields: string[]
  statuses: string[]
  actions: string[]
  actionTargets: Record<string, string>
  metrics: string[]
}

export type PageResult = {
  items: EntryRow[]
  total: number
  page: number
  size: number
}

export type ActionResult = {
  ok: boolean
  message: string
}

export type OverviewResult = {
  cards: { label: string; value: number }[]
  modules: { name: string; created: number; pending: number; abnormal: number }[]
}

export type ScoreCheck = {
  sample_id: string
  status: '有效' | '待补分' | '无效'
  score: number | null
  raw: unknown
  code: string
  reason: string
  source?: string | null
  rank?: number
  meets_threshold?: boolean
  locked?: boolean
}

export type ScoreReport = {
  ok: boolean
  ranked: ScoreCheck[]
  pending: ScoreCheck[]
  invalid: ScoreCheck[]
  excluded: ScoreCheck[]
  total: number
  valid_count: number
  pending_count: number
  invalid_count: number
}

export type AcceptanceReceipt = {
  round: number
  submitted_at: string
  status: string
  conclusion: string
  policy: string
  policy_label: string
  threshold: number
  metric_version: string
  required_samples: string[]
  sources: Record<string, string | null>
  statuses: Record<
    string,
    { status: string; score: number | null; reason: string; source: string | null; locked: boolean }
  >
  ranked: ScoreCheck[]
  pending: ScoreCheck[]
  invalid: ScoreCheck[]
  below_threshold: string[]
  min_score: number | null
  mean_score: number | null
  can_supplement: boolean
  can_register: boolean
  needs_new_acceptance: boolean
  supplementation: string
  registration: string
  warnings: string[]
  rejected_overwrites: { sample_id: string; kept: number; ignored: unknown; reason: string }[]
  reasons: string[]
  message: string
}

export type AcceptanceApplication = {
  id: number
  acceptance_id?: number
  '验收编号': string
  '组标识': string
  '组名称': string
  '必评样本清单': string[]
  '验收策略': string
  status: string
  conclusion: string
  metric_version: string
  '指标版本': string
  threshold: number
  registry_id?: number | null
  rounds: AcceptanceReceipt[]
  receipt: AcceptanceReceipt | null
  created_at: string
  updated_at: string
  ok?: boolean
}

