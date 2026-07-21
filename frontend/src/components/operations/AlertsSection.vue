<script setup lang="ts">
import { AlertTriangle } from "@lucide/vue";

import type { OperationsAlertSummary } from "../../types";
import { formatNumber, severityClass } from "./formatters";

defineProps<{
  alerts: OperationsAlertSummary;
}>();

function alertClass(severity: string) {
  return `alert-card alert-${severity}`;
}
</script>

<template>
  <section id="alerts" class="dashboard-section">
    <div class="section-title">
      <AlertTriangle :size="20" aria-hidden="true" />
      <h2>告警候选</h2>
      <span class="section-note">仅展示候选，不会通知或自动执行</span>
    </div>

    <div class="alert-grid">
      <article v-for="alert in alerts.items" :key="alert.key" :class="alertClass(alert.severity)">
        <div>
          <span :class="severityClass(alert.severity)">{{ alert.severity }}</span>
          <strong>{{ alert.title }}</strong>
        </div>
        <p>{{ alert.detail }}</p>
        <small>{{ formatNumber(alert.count) }} 条相关记录</small>
      </article>
      <article v-if="alerts.items.length === 0" class="empty-panel">当前没有告警候选。</article>
    </div>
  </section>
</template>
