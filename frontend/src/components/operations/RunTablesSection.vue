<script setup lang="ts">
import { BarChart3, LineChart } from "@lucide/vue";

import type { OperationsOverview } from "../../types";
import { formatDate, formatDateTime, formatNumber, formatPercent, statusClass, statusLabel, strategyLabel } from "./formatters";

defineProps<{
  factors: OperationsOverview["factors"];
  backtests: OperationsOverview["backtests"];
}>();
</script>

<template>
  <section id="factors" class="dashboard-section">
    <div class="section-title">
      <BarChart3 :size="20" aria-hidden="true" />
      <h2>因子运行</h2>
    </div>

    <div class="table-panel full">
      <table>
        <thead>
          <tr>
            <th>因子</th>
            <th>版本</th>
            <th>状态</th>
            <th>输入区间</th>
            <th>写入行</th>
            <th>结束</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="run in factors.latest_runs" :key="run.id">
            <td>{{ run.factor_name }}</td>
            <td>{{ run.factor_version }}</td>
            <td><span :class="statusClass(run.status)">{{ statusLabel(run.status) }}</span></td>
            <td>{{ formatDate(run.input_start_date) }} - {{ formatDate(run.input_end_date) }}</td>
            <td>{{ formatNumber(run.rows_written) }}</td>
            <td>{{ formatDateTime(run.finished_at) }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </section>

  <section id="backtests" class="dashboard-section">
    <div class="section-title">
      <LineChart :size="20" aria-hidden="true" />
      <h2>回测结果</h2>
    </div>

    <div class="table-panel full">
      <table>
        <thead>
          <tr>
            <th>名称</th>
            <th>策略</th>
            <th>状态</th>
            <th>区间</th>
            <th>最终权益</th>
            <th>收益</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="run in backtests.latest_runs" :key="run.id">
            <td>{{ run.name }}</td>
            <td>{{ strategyLabel(run.strategy_name) }}</td>
            <td><span :class="statusClass(run.status)">{{ statusLabel(run.status) }}</span></td>
            <td>{{ formatDate(run.start_date) }} - {{ formatDate(run.end_date) }}</td>
            <td>{{ formatNumber(run.final_equity) }}</td>
            <td>{{ formatPercent(run.total_return) }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </section>
</template>
