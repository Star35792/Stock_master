<script setup lang="ts">
import { onMounted } from "vue";

import DashboardHeader from "../components/operations/DashboardHeader.vue";
import FailuresSection from "../components/operations/FailuresSection.vue";
import { useOperationsDashboard } from "../composables/operationsDashboard";

const {
  limit,
  source,
  errorMessage,
  isLoading,
  isDetailLoading,
  detailErrorMessage,
  generatedAt,
  displayFailures,
  displayFailureDetail,
  displayRerunPlan,
  selectedFailure,
  failureSections,
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
    page="operations"
    :error-message="errorMessage"
    :generated-at="generatedAt"
    :is-loading="isLoading"
    :source="source"
    @refresh="loadDashboard"
  />

  <FailuresSection
    v-if="displayFailures !== null"
    :detail-error-message="detailErrorMessage"
    :failure-detail="displayFailureDetail"
    :is-detail-loading="isDetailLoading"
    :rerun-plan="displayRerunPlan"
    :sections="failureSections"
    :selected-failure="selectedFailure"
    @select="selectFailure"
  />
  <p v-else class="inline-error">{{ isLoading ? "失败记录读取中…" : "失败记录暂不可用，请刷新后重试。" }}</p>
</template>
