<script setup lang="ts">
import { ShieldCheck } from "@lucide/vue";

import type { OperationsOverview } from "../../types";
import { barWidth, countEntries, formatDate, severityClass } from "./formatters";

defineProps<{
  dataQuality: OperationsOverview["data_quality"];
}>();
</script>

<template>
  <section id="quality" class="dashboard-section">
    <div class="section-title">
      <ShieldCheck :size="20" aria-hidden="true" />
      <h2>数据质量</h2>
    </div>

    <div class="section-layout">
      <div class="count-panel">
        <h3>问题状态</h3>
        <div
          v-for="[status, count] in countEntries(dataQuality.issue_status_counts)"
          :key="status"
          class="count-row"
        >
          <span>{{ status }}</span>
          <div class="bar-track">
            <i :style="{ width: barWidth(count, dataQuality.issue_status_counts) }" />
          </div>
          <strong>{{ count }}</strong>
        </div>
      </div>

      <div class="table-panel">
        <h3>最近问题</h3>
        <table>
          <thead>
            <tr>
              <th>标的</th>
              <th>数据集</th>
              <th>类型</th>
              <th>级别</th>
              <th>日期</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="issue in dataQuality.latest_issues" :key="issue.id">
              <td>{{ issue.symbol ?? "-" }}</td>
              <td>{{ issue.dataset }}</td>
              <td>{{ issue.issue_type }}</td>
              <td><span :class="severityClass(issue.severity)">{{ issue.severity }}</span></td>
              <td>{{ formatDate(issue.trade_date) }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  </section>
</template>
