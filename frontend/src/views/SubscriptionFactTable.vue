<template>
  <table class="data-table fact-table">
    <thead>
      <tr>
        <th>订阅</th>
        <th>关联指标</th>
        <th>本次值</th>
        <th>比较基准</th>
        <th>采用口径</th>
        <th>变化比例</th>
        <th>判定</th>
      </tr>
    </thead>
    <tbody>
      <tr
        v-for="fact in facts"
        :key="String(fact.check_id ?? fact.subscription_id)"
        :data-testid="`fact-${fact.check_id}`"
      >
        <td>{{ fact.sub_name || '—' }}</td>
        <td>{{ fact.metric_name || '—' }}</td>
        <td>
          <template v-if="fact.current_value != null">
            {{ fact.current_value }}
            <span class="fact-tag">口径v{{ fact.current_calibration }}</span>
          </template>
          <template v-else>—</template>
        </td>
        <td>
          <template v-if="fact.baseline_value != null">
            {{ fact.baseline_value }}
            <span class="fact-tag">口径v{{ fact.baseline_calibration }}</span>
          </template>
          <template v-else>—</template>
        </td>
        <td>{{ fact.baseline_source_label }}</td>
        <td>{{ fact.change_pct != null ? `${fact.change_pct}%` : '—' }}</td>
        <td>
          <span v-if="!fact.comparable" class="fact-pending">
            暂未判：{{ fact.reason || '数据还不能比较' }}
          </span>
          <span v-else-if="fact.triggered" class="fact-triggered">
            已触发（阈值{{ fact.threshold_pct }}%）<template v-if="fact.pushed_now">·本次已推送</template>
            <template v-else-if="fact.pushed">·已推送过</template>
            <template v-else>·待推送</template>
          </span>
          <span v-else>未触发（阈值{{ fact.threshold_pct }}%）</span>
        </td>
      </tr>
      <tr v-if="!facts.length">
        <td colspan="7" class="empty-state">暂无订阅比较记录</td>
      </tr>
    </tbody>
  </table>
</template>

<script setup lang="ts">
import type { SubscriptionFact } from '@/api/backend-service'

defineProps<{ facts: SubscriptionFact[] }>()
</script>
