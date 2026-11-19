<script setup lang="ts">
import { onMounted } from "vue";

import AlertsSection from "../components/operations/AlertsSection.vue";
import DashboardHeader from "../components/operations/DashboardHeader.vue";
import FailuresSection from "../components/operations/FailuresSection.vue";
import MetricGrid from "../components/operations/MetricGrid.vue";
import { useOperationsDashboard } from "../composables/operationsDashboard";

const {
  limit,
  source,
  errorMessage,
  isLoading,
  isDetailLoading,
  detailErrorMessage,
  generatedAt,
  displayAlerts,
  displayFailures,
  displayFailureDetail,
  displayRerunPlan,
  selectedFailure,
  failureSections,
  snapshotCards,
  ensureLoaded,
  loadDashboard,
  selectFailure,
} = useOperationsDashboard();

onMounted(() => {
  void ensureLoaded();
});
</script>

<template>
  <DashboardHeader
    v-model:limit="limit"
    page="overview"
    :error-message="errorMessage"
    :generated-at="generatedAt"
    :is-loading="isLoading"
    :source="source"
    @refresh="loadDashboard"
  />

  <MetricGrid :cards="snapshotCards" />
  <AlertsSection v-if="displayAlerts" :alerts="displayAlerts" />
  <p v-else class="inline-error">{{ isLoading ? "告警读取中…" : "告警暂不可用，请刷新后重试。" }}</p>
  <FailuresSection
    v-if="displayFailures !== null"
    :detail-error-message="detailErrorMessage"
    :failure-detail="displayFailureDetail"
    :is-detail-loading="isDetailLoading"
    :rerun-plan="displayRerunPlan"
    :sections="failureSections"
    :selected-failure="selectedFailure"
    execution-route="/operations"
    @select="selectFailure"
  />
  <p v-else class="inline-error">{{ isLoading ? "失败记录读取中…" : "失败记录暂不可用，请刷新后重试。" }}</p>
</template>
