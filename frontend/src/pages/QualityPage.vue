<script setup lang="ts">
import { onMounted } from "vue";

import DashboardHeader from "../components/operations/DashboardHeader.vue";
import QualitySection from "../components/operations/QualitySection.vue";
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
    page="quality"
    :error-message="errorMessage"
    :generated-at="generatedAt"
    :is-loading="isLoading"
    :source="source"
    @refresh="loadDashboard"
  />

  <QualitySection v-if="displayOverview" :data-quality="displayOverview.data_quality" />
  <p v-else class="inline-error">{{ isLoading ? "数据质量概览读取中…" : "数据质量概览暂不可用，请刷新后重试。" }}</p>
</template>
