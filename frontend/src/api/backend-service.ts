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

// ------------------------------------------------------------- 桥接分派
export type DispatchRow = {
  id: string
  装配批次: string
  桥接编号: string
  所属事实表: string
  维度组: string
  归一校验: string
  条目状态: string
  当前版本: number
  当前指派人: string
  待办状态: string
  待办编号: string
  有资格装配员: string
  同编号批次归属: string
  最近拒绝依据: string
}

export type DispatchItem = { assembly_batch: string; bridge_no: string }
export type DispositionItem = DispatchItem & { disposition?: string; version?: number | null }

export type BatchResult = {
  request_id: string
  operation: string
  assembly_batch: string
  resumed: boolean
  success_count: number
  reject_count: number
  items: Record<string, string | number>[]
}

async function postJson<T>(path: string, body: unknown): Promise<T> {
  const response = await request(path, {
    method: 'POST',
    body: JSON.stringify(body),
  })
  if (!response.ok) {
    const detail = await response.json().catch(() => ({}))
    throw new Error(detail.detail ?? `接口返回 ${response.status}`)
  }
  return await response.json()
}

export async function fetchDispatchOverview(assemblyBatch?: string): Promise<DispatchRow[]> {
  const query = assemblyBatch ? `?assembly_batch=${encodeURIComponent(assemblyBatch)}` : ''
  const response = await request(`/api/bridge_link/dispatch/overview${query}`)
  if (!response.ok) throw new Error('桥接派工台读取失败')
  return await response.json()
}

export async function fetchEligibility(
  assemblyBatch: string,
  bridgeNo: string,
): Promise<Record<string, unknown>> {
  const response = await request(
    `/api/bridge_link/dispatch/eligibility/${encodeURIComponent(assemblyBatch)}/${encodeURIComponent(bridgeNo)}`,
  )
  if (!response.ok) throw new Error('处置资格读取失败')
  return await response.json()
}

export async function dispatchBridges(
  items: DispatchItem[],
  assembler: string,
  assemblyBatch: string,
  requestId?: string,
): Promise<BatchResult> {
  return postJson('/api/bridge_link/dispatch', {
    items,
    assembler,
    assembly_batch: assemblyBatch,
    request_id: requestId,
  })
}

export async function submitDispositions(
  items: DispositionItem[],
  assembler: string,
  assemblyBatch: string,
  requestId?: string,
): Promise<BatchResult> {
  return postJson('/api/bridge_link/dispositions', {
    items,
    assembler,
    assembly_batch: assemblyBatch,
    request_id: requestId,
  })
}

export async function resumeRequest(requestId: string): Promise<BatchResult> {
  return postJson('/api/bridge_link/resume', { request_id: requestId })
}

export async function fetchAssemblyTodos(assemblyBatch?: string): Promise<Record<string, string | number>[]> {
  const query = assemblyBatch ? `?assembly_batch=${encodeURIComponent(assemblyBatch)}` : ''
  const response = await request(`/api/bridge_link/assembly-todos${query}`)
  if (!response.ok) throw new Error('事实装配待办读取失败')
  return await response.json()
}

export async function fetchReceipts(requestId?: string): Promise<Record<string, string | number>[]> {
  const query = requestId ? `?request_id=${encodeURIComponent(requestId)}` : ''
  const response = await request(`/api/bridge_link/receipts${query}`)
  if (!response.ok) throw new Error('回执台账读取失败')
  return await response.json()
}

export function newRequestId(): string {
  const rand =
    typeof crypto !== 'undefined' && 'randomUUID' in crypto
      ? crypto.randomUUID().slice(0, 12).replace(/-/g, '')
      : Math.random().toString(16).slice(2, 14)
  return `REQ-FE-${Date.now().toString(36)}-${rand}`
}
