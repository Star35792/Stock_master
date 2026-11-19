<script setup lang="ts">
import { onMounted } from "vue";

import DashboardHeader from "../components/operations/DashboardHeader.vue";
import FailuresSection from "../components/operations/FailuresSection.vue";
import OperationsActionPanel from "../components/operations/OperationsActionPanel.vue";
import { useOperationsActions } from "../composables/operationsActions";
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
const actions = useOperationsActions();

async function executePlan(plan: Parameters<typeof actions.executePlan>[0]) {
  if (source.value === "demo" || source.value === "loading") return;
  await actions.executePlan(plan, (checked) => window.confirm(`确认执行：${checked.summary}\n${checked.warnings.join("\n")}`));
}

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

  <OperationsActionPanel :enabled="source === 'api' || source === 'partial'" @completed="loadDashboard" />

  <FailuresSection
    v-if="displayFailures !== null"
    :detail-error-message="detailErrorMessage"
    :failure-detail="displayFailureDetail"
    :is-detail-loading="isDetailLoading"
    :rerun-plan="displayRerunPlan"
    :sections="failureSections"
    :selected-failure="selectedFailure"
    :actions-disabled="source === 'demo' || source === 'loading' || actions.isRunning.value || actions.isStarting.value || actions.isRestoring.value"
    @select="selectFailure"
    @execute="executePlan"
  />
  <p v-else class="inline-error">{{ isLoading ? "失败记录读取中…" : "失败记录暂不可用，请刷新后重试。" }}</p>
</template>
