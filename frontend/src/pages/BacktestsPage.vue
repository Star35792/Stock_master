<script setup lang="ts">
import { onMounted } from "vue";

import DashboardHeader from "../components/operations/DashboardHeader.vue";
import RunTablesSection from "../components/operations/RunTablesSection.vue";
import { useOperationsDashboard } from "../composables/operationsDashboard";

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

onMounted(() => {
  void ensureLoaded();
});
</script>

<template>
  <DashboardHeader
    v-model:limit="limit"
    page="backtests"
    :error-message="errorMessage"
    :generated-at="generatedAt"
    :is-loading="isLoading"
    :source="source"
    @refresh="loadDashboard"
  />

  <RunTablesSection
    :backtests="displayOverview?.backtests"
    :factors="displayOverview?.factors"
  />
</template>
