<script setup lang="ts">
import { onMounted, ref } from "vue";

import DashboardHeader from "../components/operations/DashboardHeader.vue";
import OperationsActionPanel from "../components/operations/OperationsActionPanel.vue";
import QualitySection from "../components/operations/QualitySection.vue";
import { useOperationsDashboard } from "../composables/operationsDashboard";
import { updateDataQualityIssueStatus } from "../api";

const {
  limit,
  source,
  errorMessage,
  isLoading,
  generatedAt,
  displayOverview,
  ensureLoaded,
  loadDashboard,
} = useOperationsDashboard();
const updatingIssueId = ref<number | null>(null);
const statusError = ref("");

async function updateStatus(issueId: number, status: "fixed" | "ignored" | "false_positive") {
  if (source.value === "demo" || source.value === "loading" || !displayOverview.value) return;
  if (!window.confirm(`确认将质量问题 #${issueId} 标记为${status === 'fixed' ? '已修复' : status === 'ignored' ? '忽略' : '误报'}？`)) return;
  updatingIssueId.value = issueId;
  statusError.value = "";
  try {
    await updateDataQualityIssueStatus(issueId, status);
    await loadDashboard();
  } catch (error) {
    statusError.value = error instanceof Error ? error.message : "质量问题状态更新失败";
  } finally {
    updatingIssueId.value = null;
  }
}

onMounted(() => {
  void ensureLoaded();
});
</script>

<template>
  <DashboardHeader
    v-model:limit="limit"
    page="quality"
    :error-message="errorMessage"
    :generated-at="generatedAt"
    :is-loading="isLoading"
    :source="source"
    @refresh="loadDashboard"
  />

  <p v-if="statusError" class="inline-error" role="alert">{{ statusError }}</p>

  <OperationsActionPanel :enabled="source === 'api' || source === 'partial'" @completed="loadDashboard" />

  <QualitySection
    v-if="displayOverview"
    :data-quality="displayOverview.data_quality"
    :updating-issue-id="updatingIssueId"
    :actions-disabled="source === 'demo' || source === 'loading'"
    @update-status="updateStatus"
  />
  <p v-else class="inline-error">{{ isLoading ? "数据质量概览读取中…" : "数据质量概览暂不可用，请刷新后重试。" }}</p>
</template>
