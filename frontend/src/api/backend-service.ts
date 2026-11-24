import { request } from './client'
import { moduleMeta } from '@/data/modules'
import type { ActionResult, EntryRow, OverviewResult, PageResult } from '@/data/types'

export { moduleMeta }

export async function listEntries(
  key: string,
  filters: Record<string, string> = {},
): Promise<PageResult> {
  const query = new URLSearchParams(filters).toString()
  const response = await request(`/api/${key}?${query}`)
  if (!response.ok) throw new Error('业务数据读取失败')
  return await response.json()
}

export async function createEntry(
  key: string,
  values: Record<string, string>,
): Promise<ActionResult> {
  const response = await request(`/api/${key}`, {
    method: 'POST',
    body: JSON.stringify({ values }),
  })
  return await response.json()
}

export async function runAction(
  key: string,
  id: number,
  action: string,
): Promise<ActionResult> {
  const response = await request(`/api/${key}/${id}/actions`, {
    method: 'POST',
    body: JSON.stringify({ action }),
  })
  return await response.json()
}

export async function downloadEntries(
  key: string,
  filters: Record<string, string> = {},
): Promise<void> {
  const query = new URLSearchParams(filters).toString()
  const response = await request(`/api/${key}/export?${query}`)
  if (!response.ok) throw new Error('业务数据导出失败')
  const url = URL.createObjectURL(await response.blob())
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = `${key}.csv`
  document.body.appendChild(anchor)
  anchor.click()
  document.body.removeChild(anchor)
  URL.revokeObjectURL(url)
}

export async function loadOverview(): Promise<OverviewResult> {
  const response = await request('/api/overview')
  if (!response.ok) throw new Error('运营概览读取失败')
  return await response.json()
}

/** 订阅比较事实：轮询结果与值班日报明细共用同一份字段。 */
export type SubscriptionFact = {
  check_id: number | null
  subscription_id: number
  sub_name: string
  push_channel: string
  metric_id: number | null
  metric_name: string
  current_value: number | null
  current_calibration: number | null
  current_time: string | null
  baseline_value: number | null
  baseline_calibration: number | null
  baseline_time: string | null
  baseline_source: string
  baseline_source_label: string
  recalibration_id: number | null
  change_pct: number | null
  threshold_pct: number | null
  comparable: boolean
  reason: string
  triggered: boolean
  pushed: boolean
  pushed_at: string | null
  pushed_now?: boolean
}

export type SubscriptionCheckResult = {
  checked: number
  triggered: number
  pushed: number
  details: SubscriptionFact[]
}

export async function checkSubscriptions(): Promise<SubscriptionCheckResult> {
  const response = await request('/api/pipeline/subscriptions/check', { method: 'POST' })
  if (!response.ok) throw new Error('订阅轮询检查失败')
  return await response.json()
}

export type ReportOutput = {
  id: number
  report_id: number
  generated_at: string
  output_content: {
    report_name?: string
    generated_at?: string
    sections?: Record<string, unknown>[]
    subscription_comparisons?: SubscriptionFact[]
    summary?: Record<string, number>
  }
}

export async function generateReport(reportId: number): Promise<{ ok: boolean; message?: string }> {
  const response = await request(`/api/pipeline/report/${reportId}/generate`, { method: 'POST' })
  return await response.json()
}

export async function loadReportOutputs(reportId: number): Promise<{ items: ReportOutput[] }> {
  const response = await request(`/api/pipeline/report/${reportId}/outputs`)
  if (!response.ok) throw new Error('值班日报详情读取失败')
  return await response.json()
}
