<script setup lang="ts">
import { ShieldCheck } from "@lucide/vue";

import type { OperationsOverview } from "../../types";
import { barWidth, countEntries, formatDate, severityClass, severityLabel, statusLabel } from "./formatters";

defineProps<{
  dataQuality: OperationsOverview["data_quality"];
  updatingIssueId?: number | null;
  actionsDisabled?: boolean;
}>();

const emit = defineEmits<{
  updateStatus: [issueId: number, status: "fixed" | "ignored" | "false_positive"];
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
          <span>{{ statusLabel(status) }}</span>
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
              <th>处理</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="issue in dataQuality.latest_issues" :key="issue.id">
              <td>{{ issue.symbol ?? "-" }}</td>
              <td>{{ issue.dataset }}</td>
              <td>{{ issue.issue_type }}</td>
              <td><span :class="severityClass(issue.severity)">{{ severityLabel(issue.severity) }}</span></td>
              <td>{{ formatDate(issue.trade_date) }}</td>
              <td class="quality-actions">
                <template v-if="issue.status === 'open' || issue.status === 'reopened'">
                  <button type="button" :disabled="actionsDisabled || updatingIssueId === issue.id" @click="emit('updateStatus', issue.id, 'fixed')">已修复</button>
                  <button type="button" :disabled="actionsDisabled || updatingIssueId === issue.id" @click="emit('updateStatus', issue.id, 'ignored')">忽略</button>
                  <button type="button" :disabled="actionsDisabled || updatingIssueId === issue.id" @click="emit('updateStatus', issue.id, 'false_positive')">误报</button>
                </template>
                <span v-else>{{ statusLabel(issue.status) }}</span>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  </section>
</template>
