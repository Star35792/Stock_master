import {
  Activity,
  AlertTriangle,
  BriefcaseBusiness,
  ServerCog,
} from "@lucide/vue";
import { computed, ref, watch, type Component } from "vue";

import {
  fetchOperationsAlerts,
  fetchOperationsFailureDetail,
  fetchOperationsFailureRerunPlan,
  fetchOperationsFailures,
  fetchOperationsOverview,
} from "../api";
import { demoAlerts, demoFailureDetails, demoFailureReport, demoOverview, demoRerunPlans } from "../demoData";
import { formatDateTime, sideLabel, sumCounts } from "../components/operations/formatters";
import type { FailureItem, FailureSectionView, FailureType, SelectedFailure } from "../components/operations/viewTypes";
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
} from "../types";

/**
 * 运维工作台状态：总览 / 告警 / 失败记录 / 失败详情与重跑计划。
 *
 * 只被运维域页面（总览、运维、质量、回测列表）按需加载，研究主线页面
 * （行情、因子、估值、组合、持仓、模拟盘）不触发这些请求。
 * 状态是跨页共享的单例，页面间切换不重复拉取、不丢失失败记录选中态。
 */
export interface OperationsDashboardDeps {
  fetchOverview: typeof fetchOperationsOverview;
  fetchAlerts: typeof fetchOperationsAlerts;
  fetchFailures: typeof fetchOperationsFailures;
  fetchDetail: typeof fetchOperationsFailureDetail;
  fetchRerunPlan: typeof fetchOperationsFailureRerunPlan;
  demo: {
    overview: OperationsOverview;
    alerts: OperationsAlertSummary;
    failureReport: OperationsFailureReport;
    failureDetails: Record<string, Record<string, OperationsFailureDetail>>;
    rerunPlans: Record<string, Record<string, OperationsRerunPlan>>;
  };
}

const defaultDeps: OperationsDashboardDeps = {
  fetchOverview: fetchOperationsOverview,
  fetchAlerts: fetchOperationsAlerts,
  fetchFailures: fetchOperationsFailures,
  fetchDetail: fetchOperationsFailureDetail,
  fetchRerunPlan: fetchOperationsFailureRerunPlan,
  demo: {
    overview: demoOverview,
    alerts: demoAlerts,
    failureReport: demoFailureReport,
    failureDetails: demoFailureDetails,
    rerunPlans: demoRerunPlans,
  },
};

export function createOperationsDashboard(deps: OperationsDashboardDeps = defaultDeps) {
  const overview = ref<OperationsOverview | null>(null);
  const alerts = ref<OperationsAlertSummary | null>(null);
  const failures = ref<OperationsFailureReport | null>(null);
  const failureDetail = ref<OperationsFailureDetail | null>(null);
  const rerunPlan = ref<OperationsRerunPlan | null>(null);
  const selectedFailure = ref<SelectedFailure | null>(null);
  const isLoading = ref(true);
  const isDetailLoading = ref(false);
  const source = ref<"loading" | "api" | "partial" | "demo">("loading");
  const errorMessage = ref<string | null>(null);
  const detailErrorMessage = ref<string | null>(null);
  const limit = ref(5);

  let activeController: AbortController | null = null;
  let detailController: AbortController | null = null;
  let hasLoaded = false;

  const displayOverview = computed(() => source.value === "demo" ? deps.demo.overview : overview.value);
  const displayAlerts = computed(() => source.value === "demo" ? deps.demo.alerts : alerts.value);
  const displayFailures = computed(() => source.value === "demo" ? deps.demo.failureReport : failures.value);
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
  const generatedAt = computed(() => formatDateTime(displayOverview.value?.generated_at ?? null));
  const criticalAlertCount = computed(
    () => displayAlerts.value?.items.filter((item) => item.severity === "critical").length ?? 0,
  );
  const failureSections = computed<FailureSectionView[]>(() => displayFailures.value ? [
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
  ] : []);

  const snapshotCards = computed<Array<{ label: string; value: number | null; detail: string; tone: string; icon: Component }>>(() => {
    const current = displayOverview.value;
    const overviewUnavailable = source.value === "loading" ? "读取中" : "概览不可用";
    const alertsUnavailable = source.value === "loading" ? "读取中" : "告警不可用";
    return [
      {
        label: "开放质量问题",
        value: current ? sumCounts(current.data_quality.open_issue_severity_counts) : null,
        detail: current ? `${current.data_quality.latest_issues.length} 条最近记录` : overviewUnavailable,
        tone: "danger",
        icon: AlertTriangle,
      },
      {
        label: "失败任务",
        value: current ? current.jobs.status_counts.failed ?? 0 : null,
        detail: current ? `${current.jobs.status_counts.running ?? 0} 个运行中` : overviewUnavailable,
        tone: "warning",
        icon: ServerCog,
      },
      {
        label: "告警候选",
        value: displayAlerts.value?.items.length ?? null,
        detail: displayAlerts.value ? `${criticalAlertCount.value} 个关键告警` : alertsUnavailable,
        tone: "info",
        icon: Activity,
      },
      {
        label: "模拟盘持仓",
        value: current?.paper_trading.position_count ?? null,
        detail: current ? `${current.paper_trading.fill_count} 笔成交` : overviewUnavailable,
        tone: "success",
        icon: BriefcaseBusiness,
      },
    ];
  });

  function clearFailureSelection() {
    detailController?.abort();
    detailController = null;
    selectedFailure.value = null;
    failureDetail.value = null;
    rerunPlan.value = null;
    detailErrorMessage.value = null;
    isDetailLoading.value = false;
  }

  async function loadDashboard() {
    activeController?.abort();
    clearFailureSelection();
    const controller = new AbortController();
    activeController = controller;
    isLoading.value = true;
    errorMessage.value = null;

    try {
      const [nextOverview, nextAlerts, nextFailures] = await Promise.allSettled([
        deps.fetchOverview(limit.value, controller.signal),
        deps.fetchAlerts(controller.signal),
        deps.fetchFailures(limit.value, controller.signal),
      ]);
      if (controller.signal.aborted || activeController !== controller) return;
      clearFailureSelection();
      const results = [nextOverview, nextAlerts, nextFailures];
      if (results.every((result) => result.status === "rejected")) {
        overview.value = null;
        alerts.value = null;
        failures.value = null;
        source.value = "demo";
        errorMessage.value = "运维接口均不可用，当前显示演示数据；组合操作仍由服务端独立核验。";
        return;
      }
      overview.value = nextOverview.status === "fulfilled" ? nextOverview.value : null;
      alerts.value = nextAlerts.status === "fulfilled" ? nextAlerts.value : null;
      failures.value = nextFailures.status === "fulfilled" ? nextFailures.value : null;
      const unavailable = [
        nextOverview.status === "rejected" ? "概览" : null,
        nextAlerts.status === "rejected" ? "告警" : null,
        nextFailures.status === "rejected" ? "失败记录" : null,
      ].filter((name): name is string => name !== null);
      source.value = unavailable.length ? "partial" : "api";
      errorMessage.value = unavailable.length ? `${unavailable.join("、")}暂不可用；其他数据来自 API。` : null;
    } catch (error) {
      if (controller.signal.aborted || activeController !== controller) return;
      clearFailureSelection();
      overview.value = null;
      alerts.value = null;
      failures.value = null;
      source.value = "demo";
      errorMessage.value = error instanceof Error ? error.message : "无法连接后端 API";
    } finally {
      if (activeController === controller) {
        isLoading.value = false;
        hasLoaded = true;
      }
    }
  }

  /** 页面挂载时调用：只在首次进入运维域时拉取，之后跨页复用。 */
  async function ensureLoaded() {
    if (!hasLoaded) {
      await loadDashboard();
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
        deps.fetchDetail(type, id, controller.signal),
        deps.fetchRerunPlan(type, id, controller.signal),
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

  watch(limit, () => {
    void loadDashboard();
  });

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

    return deps.demo.failureDetails[selectedFailure.value.type]?.[selectedFailure.value.id] ?? null;
  }

  function getDemoRerunPlan() {
    if (!selectedFailure.value) {
      return null;
    }

    return deps.demo.rerunPlans[selectedFailure.value.type]?.[selectedFailure.value.id] ?? null;
  }

  return {
    overview,
    alerts,
    failures,
    failureDetail,
    rerunPlan,
    selectedFailure,
    isLoading,
    isDetailLoading,
    source,
    errorMessage,
    detailErrorMessage,
    limit,
    displayOverview,
    displayAlerts,
    displayFailures,
    displayFailureDetail,
    displayRerunPlan,
    generatedAt,
    criticalAlertCount,
    failureSections,
    snapshotCards,
    clearFailureSelection,
    loadDashboard,
    ensureLoaded,
    selectFailure,
  };
}

export type OperationsDashboard = ReturnType<typeof createOperationsDashboard>;

let shared: OperationsDashboard | null = null;

/** 运维域页面共享的仪表盘单例。 */
export function useOperationsDashboard(): OperationsDashboard {
  shared ??= createOperationsDashboard();
  return shared;
}
