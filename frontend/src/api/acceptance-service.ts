import { request } from './client'
import type { AcceptanceApplication, ScoreReport } from '@/data/types'

async function readJson<T>(response: Response, fallback: string): Promise<T> {
  if (!response.ok) throw new Error(fallback)
  return (await response.json()) as T
}

export async function listApplications(
  status?: string,
): Promise<{ items: AcceptanceApplication[]; total: number }> {
  const query = status ? `?status=${encodeURIComponent(status)}` : ''
  const response = await request(`/api/model_registry/acceptance${query}`)
  return await readJson(response, '验收申请读取失败')
}

export async function createApplication(values: {
  required_samples: string[]
  group_id?: string
  group_name?: string
  metric_version?: string
  policy: string
}): Promise<AcceptanceApplication> {
  const response = await request('/api/model_registry/acceptance', {
    method: 'POST',
    body: JSON.stringify(values),
  })
  return await readJson(response, '验收申请创建失败')
}

export async function submitScores(
  acceptanceId: number,
  scores: Record<string, unknown>,
  sources?: Record<string, string>,
): Promise<{
  ok: boolean
  code: string
  status: string
  conclusion: string
  message: string
  can_supplement: boolean
  can_register: boolean
  receipt: AcceptanceApplication['receipt']
  application?: AcceptanceApplication
}> {
  const response = await request(`/api/model_registry/acceptance/${acceptanceId}/submit`, {
    method: 'POST',
    body: JSON.stringify({ scores, sources }),
  })
  return await readJson(response, '评分提交失败')
}

export async function closeApplication(
  acceptanceId: number,
  reason: string,
): Promise<{ ok: boolean; status: string; message: string }> {
  const response = await request(`/api/model_registry/acceptance/${acceptanceId}/close`, {
    method: 'POST',
    body: JSON.stringify({ reason }),
  })
  return await readJson(response, '结案失败')
}

export async function loadReceipt(acceptanceId: number): Promise<AcceptanceApplication['receipt']> {
  const response = await request(`/api/model_registry/acceptance/${acceptanceId}/receipt`)
  return await readJson(response, '回执读取失败')
}

export async function loadExceptions(
  acceptanceId: number,
): Promise<{ items: Record<string, unknown>[] }> {
  const response = await request(`/api/model_registry/acceptance/${acceptanceId}/exceptions`)
  return await readJson(response, '例外记录读取失败')
}

export async function loadRegistryRecord(acceptanceNo: string): Promise<Record<string, unknown>> {
  const response = await request(
    `/api/model_registry/acceptance-record/${encodeURIComponent(acceptanceNo)}`,
  )
  return await readJson(response, '注册记录读取失败')
}

export async function validateScores(
  scores: Record<string, unknown>,
): Promise<ScoreReport> {
  const response = await request('/api/feature_iv/scores/validate', {
    method: 'POST',
    body: JSON.stringify({ scores }),
  })
  return await readJson(response, '评分校验失败')
}
