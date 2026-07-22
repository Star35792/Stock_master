<script setup lang="ts">
import type { Component } from "vue";

import AlertsSection from "../components/operations/AlertsSection.vue";
import FailuresSection from "../components/operations/FailuresSection.vue";
import MetricGrid from "../components/operations/MetricGrid.vue";
import PortfolioPlanSection from "../components/operations/PortfolioPlanSection.vue";
import QualitySection from "../components/operations/QualitySection.vue";
import RunTablesSection from "../components/operations/RunTablesSection.vue";
import type { FailureItem, FailureSectionView, FailureType, SelectedFailure } from "../components/operations/viewTypes";
import type {
  OperationsAlertSummary,
  OperationsFailureDetail,
  OperationsOverview,
  OperationsRerunPlan,
} from "../types";
import FactorPage from "./FactorPage.vue";
import MarketPage from "./MarketPage.vue";
import PaperTradingPage from "./PaperTradingPage.vue";

defineProps<{
  page: string;
  source: "api" | "demo";
  overview: OperationsOverview;
  alerts: OperationsAlertSummary;
  failureSections: FailureSectionView[];
  selectedFailure: SelectedFailure | null;
  failureDetail: OperationsFailureDetail | null;
  rerunPlan: OperationsRerunPlan | null;
  isDetailLoading: boolean;
  detailErrorMessage: string | null;
  snapshotCards: Array<{ label: string; value: number; detail: string; tone: string; icon: Component }>;
}>();

defineEmits<{ selectFailure: [type: FailureType, item: FailureItem] }>();
</script>

<template>
  <template v-if="page === 'overview'">
    <MetricGrid :cards="snapshotCards" />
    <AlertsSection :alerts="alerts" />
    <FailuresSection
      :detail-error-message="detailErrorMessage"
      :failure-detail="failureDetail"
      :is-detail-loading="isDetailLoading"
      :rerun-plan="rerunPlan"
      :sections="failureSections"
      :selected-failure="selectedFailure"
      @select="(type, item) => $emit('selectFailure', type, item)"
    />
  </template>
  <MarketPage v-else-if="page === 'market'" />
  <QualitySection v-else-if="page === 'quality'" :data-quality="overview.data_quality" />
  <FactorPage v-else-if="page === 'factors'" />
  <RunTablesSection
    v-else-if="page === 'backtests'"
    :backtests="overview.backtests"
    :factors="overview.factors"
  />
  <PortfolioPlanSection v-else-if="page === 'portfolio'" :write-enabled="source === 'api'" />
  <PaperTradingPage v-else-if="page === 'paper'" />
  <AlertsSection v-else-if="page === 'alerts'" :alerts="alerts" />
  <FailuresSection
    v-else
    :detail-error-message="detailErrorMessage"
    :failure-detail="failureDetail"
    :is-detail-loading="isDetailLoading"
    :rerun-plan="rerunPlan"
    :sections="failureSections"
    :selected-failure="selectedFailure"
    @select="(type, item) => $emit('selectFailure', type, item)"
  />
</template>
