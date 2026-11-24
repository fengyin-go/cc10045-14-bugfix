<template>
  <section class="page" :data-module="meta.key">
    <header class="page-head">
      <div>
        <h2>{{ meta.name }}</h2>
        <p class="page-desc">{{ meta.desc }}</p>
      </div>
      <div class="page-actions">
        <button
          v-if="isSubscriptionModule"
          class="btn"
          data-testid="poll-subscriptions"
          type="button"
          :disabled="polling"
          @click="pollSubscriptions"
        >
          {{ polling ? '轮询中…' : '轮询检查推送' }}
        </button>
        <button class="btn primary" data-testid="open-create" type="button" @click="openCreate">
          登记{{ meta.entity }}
        </button>
        <button class="btn" data-testid="export" type="button" @click="exportRows">
          导出{{ meta.name }}清单
        </button>
      </div>
    </header>

    <section v-if="subscriptionResult" class="sub-check-panel" data-testid="subscription-check-result">
      <h3 class="sub-check-title">
        轮询结果：检查 {{ subscriptionResult.checked }} 条 · 触发 {{ subscriptionResult.triggered }} 条 ·
        本次推送 {{ subscriptionResult.pushed }} 条
      </h3>
      <SubscriptionFactTable :facts="subscriptionResult.details" />
    </section>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <p class="status-legend">
      <span v-for="item in statusSummary" :key="item.status" class="legend-item">
        {{ item.status }}：{{ item.count }}
      </span>
    </p>

    <form class="filter-bar" @submit.prevent="reload">
      <label v-for="field in filterFields" :key="field" class="filter-item">
        <span>{{ field }}</span>
        <input v-model="filters[field]" :data-testid="`filter-${field}`" :placeholder="`按${field}检索`" />
      </label>
      <button class="btn" data-testid="query" type="submit">查询</button>
      <button class="btn ghost" data-testid="reset" type="button" @click="resetFilters">重置条件</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in meta.fields" :key="column">{{ column }}</th>
          <th>当前状态</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)" :data-testid="`row-${row.id}`" :data-status="String(row.status)">
          <td v-for="column in meta.fields" :key="column">{{ row[column] ?? '—' }}</td>
          <td>{{ row.status }}</td>
          <td class="row-actions">
            <button
              v-for="action in meta.actions"
              :key="action"
              class="link"
              type="button"
              :data-testid="`action-${row.id}-${action}`"
              @click="runAction(action, row)"
            >
              {{ action }}
            </button>
            <button
              v-if="isReportModule"
              class="link"
              type="button"
              :data-testid="`generate-report-${row.id}`"
              @click="generateDailyReport(row)"
            >
              生成日报
            </button>
            <button
              v-if="isReportModule"
              class="link"
              type="button"
              :data-testid="`report-detail-${row.id}`"
              @click="openReportDetail(row)"
            >
              日报详情
            </button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="meta.fields.length + 2" class="empty-state">
            暂无{{ meta.name }}数据，可先登记{{ meta.entity }}
          </td>
        </tr>
      </tbody>
    </table>

    <div v-if="showCreate" class="modal-backdrop" data-testid="create-modal">
      <form class="modal" @submit.prevent="submitCreate">
        <h3>登记{{ meta.entity }}</h3>
        <label v-for="field in meta.fields" :key="field" class="create-field">
          <span>{{ field }}</span>
          <input v-model="createValues[field]" :data-testid="`create-${field}`" required />
        </label>
        <div class="page-actions">
          <button class="btn" type="button" @click="showCreate = false">取消</button>
          <button class="btn primary" data-testid="submit-create" type="submit" :disabled="creating">
            {{ creating ? '提交中…' : '确认登记' }}
          </button>
        </div>
      </form>
    </div>

    <div v-if="reportDetail" class="modal-backdrop" data-testid="report-detail-modal">
      <div class="modal report-modal">
        <h3>值班日报详情：{{ reportDetail.name }}</h3>
        <p v-if="reportDetailLoading" class="page-desc">日报内容加载中…</p>
        <template v-else-if="reportDetail.outputs.length">
          <p class="status-legend">
            <span
              v-for="(output, index) in reportDetail.outputs"
              :key="output.id"
              class="legend-item"
            >
              <button
                class="link output-tab"
                :class="{ 'output-active': index === selectedOutputIndex }"
                type="button"
                :data-testid="`output-tab-${output.id}`"
                @click="selectedOutputIndex = index"
              >
                {{ output.generated_at }}
              </button>
            </span>
          </p>
          <div v-if="currentOutput" :data-testid="`output-${currentOutput.id}`">
            <p class="page-desc">
              生成于 {{ currentOutput.generated_at }} · 订阅核对
              {{ currentOutput.output_content.summary?.subscription_checked ?? 0 }} 条 · 触发
              {{ currentOutput.output_content.summary?.subscription_triggered ?? 0 }} 条 · 暂不可比
              {{ currentOutput.output_content.summary?.subscription_pending ?? 0 }} 条
              （内容已固化，后续采样不会覆盖本份判断依据）
            </p>
            <h4>订阅触发核对</h4>
            <SubscriptionFactTable
              :facts="currentOutput.output_content.subscription_comparisons ?? []"
            />
          </div>
        </template>
        <p v-else class="empty-state">该报告还没有生成过日报，可先在列表点击「生成日报」</p>
        <div class="page-actions">
          <button class="btn" type="button" data-testid="close-report-detail" @click="reportDetail = null">
            关闭
          </button>
        </div>
      </div>
    </div>

    <footer class="page-foot">
      <span>共 {{ total }} 条{{ meta.name }}记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import {
  checkSubscriptions,
  createEntry,
  downloadEntries,
  generateReport,
  listEntries,
  loadReportOutputs,
  moduleMeta,
  runAction as applyAction,
} from '@/api/backend-service'
import type { ReportOutput, SubscriptionCheckResult } from '@/api/backend-service'
import type { EntryRow } from '@/data/types'
import SubscriptionFactTable from '@/views/SubscriptionFactTable.vue'

const props = defineProps<{ moduleKey: string }>()
const meta = moduleMeta(props.moduleKey)
const isSubscriptionModule = meta.key === 'data_subscription'
const isReportModule = meta.key === 'data_report'
const rows = ref<EntryRow[]>([])
const total = ref(0)
const errorMessage = ref('')
const filters = ref<Record<string, string>>({})
const filterFields = meta.fields.slice(0, 3)
const showCreate = ref(false)
const creating = ref(false)
const createValues = ref<Record<string, string>>({})
const polling = ref(false)
const subscriptionResult = ref<SubscriptionCheckResult | null>(null)
const reportDetail = ref<{ id: number; name: string; outputs: ReportOutput[] } | null>(null)
const reportDetailLoading = ref(false)
const selectedOutputIndex = ref(0)
const currentOutput = computed(() =>
  reportDetail.value ? (reportDetail.value.outputs[selectedOutputIndex.value] ?? null) : null,
)
const stats = computed(() => [
  { label: meta.metrics[0], value: total.value },
  { label: meta.metrics[1], value: rows.value.filter((row) => row.status === meta.statuses[1]).length },
  {
    label: meta.metrics[2],
    value: rows.value.filter((row) => row.status === meta.statuses[meta.statuses.length - 1]).length,
  },
])
const statusSummary = computed(() =>
  meta.statuses.map((status) => ({
    status,
    count: rows.value.filter((row) => String(row.status) === status).length,
  })),
)

function resetFilters() {
  filters.value = {}
  void reload()
}

async function exportRows() {
  errorMessage.value = ''
  try {
    await downloadEntries(meta.key, filters.value)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '导出失败'
  }
}

function openCreate() {
  createValues.value = Object.fromEntries(meta.fields.map((field) => [field, '']))
  errorMessage.value = ''
  showCreate.value = true
}

async function submitCreate() {
  creating.value = true
  errorMessage.value = ''
  try {
    const result = await createEntry(meta.key, createValues.value)
    if (!result.ok) throw new Error(result.message)
    showCreate.value = false
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '登记失败'
  } finally {
    creating.value = false
  }
}

async function runAction(action: string, row: EntryRow) {
  errorMessage.value = ''
  try {
    const result = await applyAction(meta.key, Number(row.id), action)
    if (!result.ok) throw new Error(result.message)
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '操作失败'
  }
}

async function pollSubscriptions() {
  polling.value = true
  errorMessage.value = ''
  try {
    subscriptionResult.value = await checkSubscriptions()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '订阅轮询检查失败'
  } finally {
    polling.value = false
  }
}

async function openReportDetail(row: EntryRow) {
  const id = Number(row.id)
  reportDetail.value = {
    id,
    name: String(row['报告名称'] ?? row.report_name ?? `报告#${id}`),
    outputs: [],
  }
  selectedOutputIndex.value = 0
  reportDetailLoading.value = true
  errorMessage.value = ''
  try {
    const result = await loadReportOutputs(id)
    reportDetail.value.outputs = result.items
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '值班日报详情读取失败'
  } finally {
    reportDetailLoading.value = false
  }
}

async function generateDailyReport(row: EntryRow) {
  errorMessage.value = ''
  try {
    const result = await generateReport(Number(row.id))
    if (!result.ok) throw new Error(result.message ?? '日报生成失败')
    await openReportDetail(row)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '日报生成失败'
  }
}

async function reload() {
  errorMessage.value = ''
  try {
    const payload = await listEntries(meta.key, filters.value)
    rows.value = payload.items
    total.value = payload.total
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '业务列表读取失败'
  }
}

onMounted(reload)
</script>
