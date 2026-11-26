<template>
  <section class="page" data-module="bridge_dispatch">
    <header class="page-head">
      <div>
        <h2>桥接分派</h2>
        <p class="page-desc">
          多值桥接条目按事实批次跨模块生成事实装配待办：审核员多选同批次条目批量派工，
          逐条查看装配员处置资格；改派与在途请求冲突时只确认一项处分，丢包可凭请求编号接续重试。
        </p>
      </div>
      <div class="page-actions">
        <button class="btn" data-testid="reload" type="button" @click="reloadAll">刷新派工台</button>
      </div>
    </header>

    <div class="stat-row">
      <article class="stat-card">
        <span class="stat-label">当前页签条目</span>
        <strong class="stat-value">{{ rows.length }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">在途待办</span>
        <strong class="stat-value">{{ inFlightCount }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">已确认</span>
        <strong class="stat-value">{{ confirmedCount }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">最近被拒条目</span>
        <strong class="stat-value">{{ rejectedCount }}</strong>
      </article>
    </div>

    <div class="tab-bar" role="tablist">
      <button
        v-for="tab in tabs"
        :key="tab.batch"
        class="tab-item"
        :class="{ active: activeBatch === tab.batch }"
        type="button"
        role="tab"
        :data-testid="`tab-${tab.batch}`"
        @click="switchTab(tab.batch)"
      >
        {{ tab.label }}
      </button>
    </div>

    <!-- 批量派工条 -->
    <form class="dispatch-bar" @submit.prevent="onDispatch">
      <label class="filter-item">
        <span>指派装配员</span>
        <select v-model="dispatchAssembler" data-testid="dispatch-assembler">
          <option v-for="name in assemblers" :key="name" :value="name">{{ name }}</option>
        </select>
      </label>
      <label class="check-line">
        <input v-model="simulateLost" type="checkbox" data-testid="simulate-lost" />
        <span>模拟回包丢失（本地不展示结果，稍后用请求编号重试）</span>
      </label>
      <button
        class="btn primary"
        type="submit"
        :disabled="!selectedKeys.size || busy"
        data-testid="submit-dispatch"
      >
        {{ busy ? '提交中…' : `批量派工（已选 ${selectedKeys.size} 条）` }}
      </button>
      <button class="btn ghost" type="button" @click="selectedKeys.clear()">清空选择</button>
    </form>

    <p v-if="message" class="page-message" :class="messageKind" data-testid="message">{{ message }}</p>

    <table class="data-table">
      <thead>
        <tr>
          <th>选择</th>
          <th>桥接编号</th>
          <th>所属事实表</th>
          <th>维度组</th>
          <th>归一校验</th>
          <th>条目状态</th>
          <th>版本</th>
          <th>当前指派人 / 待办</th>
          <th>有资格装配员</th>
          <th>同编号批次归属</th>
          <th>最近拒绝依据</th>
          <th>逐条操作</th>
        </tr>
      </thead>
      <tbody>
        <tr
          v-for="row in rows"
          :key="row.id"
          :data-testid="`row-${row.id}`"
          :data-status="row.条目状态"
          :class="{ rejected: !!row.最近拒绝依据 }"
        >
          <td>
            <input
              type="checkbox"
              :value="row.id"
              :checked="selectedKeys.has(row.id)"
              :data-testid="`check-${row.id}`"
              @change="toggleSelect(row.id)"
            />
          </td>
          <td>{{ row.桥接编号 }}</td>
          <td>{{ row.所属事实表 }}</td>
          <td>{{ row.维度组 }}</td>
          <td>{{ row.归一校验 }}</td>
          <td>{{ row.条目状态 }}</td>
          <td>{{ row.当前版本 }}</td>
          <td>
            <template v-if="row.当前指派人">
              {{ row.当前指派人 }} · {{ row.待办状态 }}
            </template>
            <template v-else>—</template>
          </td>
          <td>{{ row.有资格装配员 }}</td>
          <td>{{ row.同编号批次归属 }}</td>
          <td class="reject-cell">{{ row.最近拒绝依据 || '—' }}</td>
          <td class="row-actions">
            <button class="link" type="button" :data-testid="`elig-${row.id}`" @click="showEligibility(row)">
              资格
            </button>
            <button
              v-if="row.待办状态 === '在途'"
              class="link"
              type="button"
              :data-testid="`finish-${row.id}`"
              @click="onFinish(row)"
            >
              装配完成
            </button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td colspan="12" class="empty-state">该批次暂无可分派桥接条目</td>
        </tr>
      </tbody>
    </table>

    <!-- 资格弹层 -->
    <div v-if="eligibility" class="modal-backdrop" data-testid="eligibility-modal" @click.self="eligibility = null">
      <div class="modal">
        <h3>处置资格 · {{ eligibility.装配批次 }}/{{ eligibility.桥接编号 }}</h3>
        <p class="page-desc">维度组：{{ eligibility.维度组 }} ｜ 当前版本：{{ eligibility.当前版本 }} ｜ 当前指派人：{{ eligibility.当前指派人 || '无' }}</p>
        <table class="data-table">
          <thead><tr><th>装配员</th><th>有资格</th><th>拒绝依据</th></tr></thead>
          <tbody>
            <tr v-for="item in eligibility.资格明细" :key="item.装配员">
              <td>{{ item.装配员 }}</td>
              <td>{{ item.有资格 }}</td>
              <td class="reject-cell">{{ item.拒绝依据 || '—' }}</td>
            </tr>
          </tbody>
        </table>
        <div class="page-actions">
          <button class="btn primary" type="button" @click="eligibility = null">知道了</button>
        </div>
      </div>
    </div>

    <div class="lower-grid">
      <!-- 事实装配待办 -->
      <section class="panel">
        <h3>事实装配待办（跨模块）</h3>
        <table class="data-table compact">
          <thead><tr><th>待办</th><th>批次/桥接</th><th>装配员</th><th>状态</th><th>完成人</th></tr></thead>
          <tbody>
            <tr v-for="todo in todos" :key="String(todo.待办编号)">
              <td>{{ todo.待办编号 }}</td>
              <td>{{ todo.装配批次 }}/{{ todo.桥接编号 }}</td>
              <td>{{ todo.指派装配员 }}</td>
              <td>{{ todo.待办状态 }}</td>
              <td>{{ todo.完成人 || '—' }}</td>
            </tr>
            <tr v-if="!todos.length"><td colspan="5" class="empty-state">暂无待办</td></tr>
          </tbody>
        </table>
      </section>

      <!-- 回执台账 -->
      <section class="panel">
        <h3>回执台账</h3>
        <table class="data-table compact">
          <thead><tr><th>请求编号</th><th>操作</th><th>批次/桥接</th><th>装配员</th><th>结果</th><th>版本</th><th>接续</th></tr></thead>
          <tbody>
            <tr v-for="(receipt, idx) in receipts" :key="idx" :class="{ rejected: receipt.是否成功 === '否' }">
              <td class="mono">{{ receipt.请求编号 }}</td>
              <td>{{ receipt.操作类型 }}</td>
              <td>{{ receipt.装配批次 }}/{{ receipt.桥接编号 }}</td>
              <td>{{ receipt.装配员 }}</td>
              <td>{{ receipt.是否成功 }}</td>
              <td>{{ receipt.版本前 }}→{{ receipt.版本后 }}</td>
              <td>{{ receipt.是否接续重试 }}</td>
            </tr>
            <tr v-if="!receipts.length"><td colspan="7" class="empty-state">暂无回执</td></tr>
          </tbody>
        </table>
      </section>
    </div>

    <!-- 丢包重试 -->
    <section class="panel resume-panel">
      <h3>回包丢失重试</h3>
      <form class="dispatch-bar" @submit.prevent="onResume">
        <label class="filter-item grow">
          <span>请求编号</span>
          <input v-model="resumeRequestId" placeholder="粘贴回执上的请求编号" data-testid="resume-input" required />
        </label>
        <button class="btn primary" type="button" data-testid="resume-btn" @click="onResume">接续原操作</button>
      </form>
      <div class="request-history">
        <span class="page-desc">本次会话请求：</span>
        <button
          v-for="req in requestHistory"
          :key="req"
          class="link mono"
          type="button"
          @click="resumeRequestId = req"
        >
          {{ req }}
        </button>
      </div>
    </section>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import {
  dispatchBridges,
  fetchAssemblyTodos,
  fetchDispatchOverview,
  fetchEligibility,
  fetchReceipts,
  newRequestId,
  resumeRequest,
  submitDispositions,
  type BatchResult,
  type DispatchRow,
} from '@/api/backend-service'

const tabs = [
  { batch: 'F90', label: 'F90 · 门店组（门店销售事实表）' },
  { batch: 'F91', label: 'F91 · 渠道组（渠道销售事实表）' },
]
const assemblers = ['甲', '乙']

const activeBatch = ref('F90')
const rows = ref<DispatchRow[]>([])
const todos = ref<Record<string, string | number>[]>([])
const receipts = ref<Record<string, string | number>[]>([])
const selectedKeys = ref<Set<string>>(new Set())
const dispatchAssembler = ref('甲')
const simulateLost = ref(false)
const busy = ref(false)
const message = ref('')
const messageKind = ref('ok')
const eligibility = ref<Record<string, any> | null>(null)
const resumeRequestId = ref('')
const requestHistory = ref<string[]>([])

const inFlightCount = computed(() => rows.value.filter((r) => r.待办状态 === '在途').length)
const confirmedCount = computed(() => rows.value.filter((r) => r.条目状态 === '已装配').length)
const rejectedCount = computed(() => rows.value.filter((r) => r.最近拒绝依据).length)

function flash(text: string, kind: 'ok' | 'err' = 'ok') {
  message.value = text
  messageKind.value = kind
}

function switchTab(batch: string) {
  activeBatch.value = batch
  selectedKeys.value = new Set()
  void reloadBoard()
}

function toggleSelect(key: string) {
  const next = new Set(selectedKeys.value)
  if (next.has(key)) next.delete(key)
  else next.add(key)
  selectedKeys.value = next
}

async function reloadBoard() {
  rows.value = await fetchDispatchOverview(activeBatch.value)
}

async function reloadAux() {
  const [todoList, receiptList] = await Promise.all([
    fetchAssemblyTodos(),
    fetchReceipts(),
  ])
  todos.value = todoList
  receipts.value = receiptList.slice().reverse()
}

async function reloadAll() {
  try {
    await Promise.all([reloadBoard(), reloadAux()])
  } catch (error) {
    flash(error instanceof Error ? error.message : '派工台刷新失败', 'err')
  }
}

function describeResult(result: BatchResult): string {
  const rejected = result.items.filter((item) => item.是否成功 === '否')
  const head = `请求 ${result.request_id}：成功 ${result.success_count} 条、拒绝 ${result.reject_count} 条`
  if (!rejected.length) return head
  const detail = rejected
    .map((item) => `${item.装配批次}/${item.桥接编号}→${item.装配员}：${item.拒绝依据}`)
    .join('；')
  return `${head}。拒绝依据：${detail}`
}

async function onDispatch() {
  const items = rows.value
    .filter((row) => selectedKeys.value.has(row.id))
    .map((row) => ({ assembly_batch: row.装配批次, bridge_no: row.桥接编号 }))
  if (!items.length) return
  const requestId = newRequestId()
  requestHistory.value.push(requestId)
  busy.value = true
  try {
    if (simulateLost.value) {
      // 服务端已执行并留痕，仅本地丢弃回包，模拟在途请求未写入。
      await dispatchBridges(items, dispatchAssembler.value, activeBatch.value, requestId)
      flash(`请求 ${requestId} 的回包已模拟丢失，服务端已受理，可在下方用请求编号接续重试`, 'err')
    } else {
      const result = await dispatchBridges(items, dispatchAssembler.value, activeBatch.value, requestId)
      flash(describeResult(result), result.reject_count ? 'err' : 'ok')
    }
    selectedKeys.value = new Set()
    await reloadAll()
  } catch (error) {
    flash(error instanceof Error ? error.message : '批量派工失败', 'err')
  } finally {
    busy.value = false
  }
}

async function onFinish(row: DispatchRow) {
  const requestId = newRequestId()
  requestHistory.value.push(requestId)
  try {
    const result = await submitDispositions(
      [{ assembly_batch: row.装配批次, bridge_no: row.桥接编号, version: row.当前版本 }],
      row.当前指派人,
      activeBatch.value,
      requestId,
    )
    flash(describeResult(result), result.reject_count ? 'err' : 'ok')
    await reloadAll()
  } catch (error) {
    flash(error instanceof Error ? error.message : '处分提交失败', 'err')
  }
}

async function showEligibility(row: DispatchRow) {
  try {
    eligibility.value = await fetchEligibility(row.装配批次, row.桥接编号)
  } catch (error) {
    flash(error instanceof Error ? error.message : '资格读取失败', 'err')
  }
}

async function onResume() {
  if (!resumeRequestId.value.trim()) return
  try {
    const result = await resumeRequest(resumeRequestId.value.trim())
    flash(`接续重试完成（resumed=${result.resumed}）：${describeResult(result)}`, result.reject_count ? 'err' : 'ok')
    await reloadAll()
  } catch (error) {
    flash(error instanceof Error ? error.message : '接续重试失败', 'err')
  }
}

onMounted(reloadAll)
</script>
