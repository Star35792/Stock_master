<script setup lang="ts">
import {
  Activity,
  AlertTriangle,
  BarChart3,
  BriefcaseBusiness,
  ClipboardList,
  LineChart,
  ServerCog,
  ShieldCheck,
  Wallet,
} from "@lucide/vue";
import { computed, onMounted, ref, watch } from "vue";
import { useRoute } from "vue-router";

import {
  fetchOperationsAlerts,
  fetchOperationsFailureDetail,
  fetchOperationsFailureRerunPlan,
  fetchOperationsFailures,
  fetchOperationsOverview,
} from "./api";
import AppSidebar from "./components/operations/AppSidebar.vue";
import DashboardHeader from "./components/operations/DashboardHeader.vue";
import { formatDateTime, sideLabel, sumCounts } from "./components/operations/formatters";
import type { FailureItem, FailureSectionView, FailureType, SelectedFailure } from "./components/operations/viewTypes";
import { demoAlerts, demoFailureDetails, demoFailureReport, demoOverview, demoRerunPlans } from "./demoData";
import type {
  BacktestRunFailure,
  DataQualityFailure,
  FactorRunFailure,
  JobFailure,
  OperationsAlertSummary,
  OperationsFailureDetail,
  OperationsFailureReport,
  OperationsOverview,
  OperationsRerunPlan,
  PaperOrderFailure,
} from "./types";

const overview = ref<OperationsOverview | null>(null);
const alerts = ref<OperationsAlertSummary | null>(null);
const failures = ref<OperationsFailureReport | null>(null);
const failureDetail = ref<OperationsFailureDetail | null>(null);
const rerunPlan = ref<OperationsRerunPlan | null>(null);
const selectedFailure = ref<SelectedFailure | null>(null);
const isLoading = ref(false);
const isDetailLoading = ref(false);
const source = ref<"api" | "demo">("demo");
const errorMessage = ref<string | null>(null);
const detailErrorMessage = ref<string | null>(null);
const limit = ref(5);
const route = useRoute();
const activePage = computed(() => String(route.name ?? "overview"));
const usesOwnPageHeader = computed(() => ["market", "factors", "paper", "price-value"].includes(activePage.value));

let activeController: AbortController | null = null;
let detailController: AbortController | null = null;

const displayOverview = computed(() => overview.value ?? demoOverview);
const displayAlerts = computed(() => alerts.value ?? demoAlerts);
const displayFailures = computed(() => failures.value ?? demoFailureReport);
const displayFailureDetail = computed(() => {
  if (failureDetail.value) {
    return failureDetail.value;
  }

  return source.value === "demo" && selectedFailure.value ? getDemoFailureDetail() : null;
});
const displayRerunPlan = computed(() => {
  if (rerunPlan.value) {
    return rerunPlan.value;
  }

  return source.value === "demo" && selectedFailure.value ? getDemoRerunPlan() : null;
});
const generatedAt = computed(() => formatDateTime(displayOverview.value.generated_at));
const criticalAlertCount = computed(
  () => displayAlerts.value.items.filter((item) => item.severity === "critical").length,
);
const failureSections = computed<FailureSectionView[]>(() => [
  {
    type: "data_quality_issue",
    title: "数据质量问题",
    total: displayFailures.value.data_quality_issues.total,
    hasMore: displayFailures.value.data_quality_issues.has_more,
    items: displayFailures.value.data_quality_issues.items,
  },
  {
    type: "job",
    title: "失败任务",
    total: displayFailures.value.jobs.total,
    hasMore: displayFailures.value.jobs.has_more,
    items: displayFailures.value.jobs.items,
  },
  {
    type: "factor_run",
    title: "因子运行",
    total: displayFailures.value.factor_runs.total,
    hasMore: displayFailures.value.factor_runs.has_more,
    items: displayFailures.value.factor_runs.items,
  },
  {
    type: "backtest_run",
    title: "回测运行",
    total: displayFailures.value.backtest_runs.total,
    hasMore: displayFailures.value.backtest_runs.has_more,
    items: displayFailures.value.backtest_runs.items,
  },
  {
    type: "paper_order",
    title: "模拟盘拒单",
    total: displayFailures.value.paper_orders.total,
    hasMore: displayFailures.value.paper_orders.has_more,
    items: displayFailures.value.paper_orders.items,
  },
]);

const navItems = [
  { id: "overview", label: "总览", icon: Activity },
  { id: "market", label: "行情", icon: LineChart },
  { id: "quality", label: "质量", icon: ShieldCheck },
  { id: "factors", label: "因子", icon: BarChart3 },
  { id: "price-value", label: "估值研究", icon: LineChart },
  { id: "backtests", label: "回测", icon: LineChart },
  { id: "positions", label: "持仓分析", icon: Wallet },
  { id: "portfolio", label: "组合草案", icon: ClipboardList },
  { id: "paper", label: "模拟盘", icon: BriefcaseBusiness },
  { id: "operations", label: "运维", icon: ServerCog },
];

const snapshotCards = computed(() => {
  const current = displayOverview.value;
  return [
    {
      label: "开放质量问题",
      value: sumCounts(current.data_quality.open_issue_severity_counts),
      detail: `${current.data_quality.latest_issues.length} 条最近记录`,
      tone: "danger",
      icon: AlertTriangle,
    },
    {
      label: "失败任务",
      value: current.jobs.status_counts.failed ?? 0,
      detail: `${current.jobs.status_counts.running ?? 0} 个运行中`,
      tone: "warning",
      icon: ServerCog,
    },
    {
      label: "告警候选",
      value: displayAlerts.value.items.length,
      detail: `${criticalAlertCount.value} 个关键告警`,
      tone: "info",
      icon: Activity,
    },
    {
      label: "模拟盘持仓",
      value: current.paper_trading.position_count,
      detail: `${current.paper_trading.fill_count} 笔成交`,
      tone: "success",
      icon: BriefcaseBusiness,
    },
  ];
});

async function loadDashboard() {
  activeController?.abort();
  const controller = new AbortController();
  activeController = controller;
  isLoading.value = true;
  errorMessage.value = null;

  try {
    const [nextOverview, nextAlerts, nextFailures] = await Promise.all([
      fetchOperationsOverview(limit.value, controller.signal),
      fetchOperationsAlerts(controller.signal),
      fetchOperationsFailures(limit.value, controller.signal),
    ]);
    overview.value = nextOverview;
    alerts.value = nextAlerts;
    failures.value = nextFailures;
    source.value = "api";
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") {
      return;
    }

    overview.value = demoOverview;
    alerts.value = demoAlerts;
    failures.value = demoFailureReport;
    source.value = "demo";
    errorMessage.value = error instanceof Error ? error.message : "无法连接后端 API";
  } finally {
    if (activeController === controller) {
      isLoading.value = false;
    }
  }
}

async function selectFailure(type: FailureType, item: FailureItem) {
  const id = item.id;
  selectedFailure.value = {
    type,
    id,
    label: getFailureTitle(type, item),
  };
  detailController?.abort();
  const controller = new AbortController();
  detailController = controller;
  isDetailLoading.value = true;
  detailErrorMessage.value = null;
  failureDetail.value = null;
  rerunPlan.value = null;

  if (source.value === "demo") {
    failureDetail.value = getDemoFailureDetail();
    rerunPlan.value = getDemoRerunPlan();
    isDetailLoading.value = false;
    return;
  }

  try {
    const [nextDetail, nextPlan] = await Promise.all([
      fetchOperationsFailureDetail(type, id, controller.signal),
      fetchOperationsFailureRerunPlan(type, id, controller.signal),
    ]);
    failureDetail.value = nextDetail;
    rerunPlan.value = nextPlan;
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") {
      return;
    }

    detailErrorMessage.value = error instanceof Error ? error.message : "无法加载失败详情";
  } finally {
    if (detailController === controller) {
      isDetailLoading.value = false;
    }
  }
}

watch(limit, loadDashboard);
onMounted(loadDashboard);

function getFailureTitle(type: FailureType, item: FailureItem) {
  if (type === "data_quality_issue") {
    const issue = item as DataQualityFailure;
    return `${issue.symbol ?? issue.dataset} · ${issue.issue_type}`;
  }
  if (type === "job") {
    return (item as JobFailure).job_name;
  }
  if (type === "factor_run") {
    return (item as FactorRunFailure).factor_name;
  }
  if (type === "backtest_run") {
    return (item as BacktestRunFailure).name;
  }

  const order = item as PaperOrderFailure;
  return `${order.symbol} · ${sideLabel(order.side)} ${order.order_quantity}`;
}

function getDemoFailureDetail() {
  if (!selectedFailure.value) {
    return null;
  }

  return demoFailureDetails[selectedFailure.value.type][selectedFailure.value.id] ?? null;
}

function getDemoRerunPlan() {
  if (!selectedFailure.value) {
    return null;
  }

  return demoRerunPlans[selectedFailure.value.type][selectedFailure.value.id] ?? null;
}
</script>

<template>
  <div class="app-shell">
    <AppSidebar :nav-items="navItems" />

    <main class="workspace">
      <DashboardHeader
        v-if="!usesOwnPageHeader"
        v-model:limit="limit"
        :error-message="errorMessage"
        :generated-at="generatedAt"
        :is-loading="isLoading"
        :page="activePage"
        :source="source"
        @refresh="loadDashboard"
      />

      <RouterView v-slot="{ Component, route: currentRoute }">
        <component
          :is="Component"
          :alerts="displayAlerts"
          :detail-error-message="detailErrorMessage"
          :failure-detail="displayFailureDetail"
          :failure-sections="failureSections"
          :is-detail-loading="isDetailLoading"
          :overview="displayOverview"
          :page="String(currentRoute.name ?? 'overview')"
          :rerun-plan="displayRerunPlan"
          :selected-failure="selectedFailure"
          :snapshot-cards="snapshotCards"
          :source="source"
          @select-failure="selectFailure"
        />
      </RouterView>
    </main>
  </div>
</template>
