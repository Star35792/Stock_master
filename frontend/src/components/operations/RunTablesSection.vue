<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { BarChart3, LineChart } from "@lucide/vue";
import { useRoute, useRouter } from "vue-router";

import type { BacktestRunSnapshot, OperationsOverview } from "../../types";
import { formatDate, formatDateTime, formatNumber, formatPercent, statusClass, statusLabel, strategyLabel } from "./formatters";
import { curvePolyline, normalizeEquity, sameEquityDates, type EquityObservation, type NormalizedEquity } from "./comparisonCurve";

const props = defineProps<{
  factors?: OperationsOverview["factors"];
  backtests?: OperationsOverview["backtests"];
}>();
const route = useRoute();
const router = useRouter();

type BacktestRunListItem = BacktestRunSnapshot & {
  data_version: string;
  code_version: string;
  parameters: Record<string, unknown>;
  sharpe_ratio: string | null;
  performance_status: "analyzed" | "derived" | "missing_equity" | "invalid_equity" | "not_applicable";
  performance_code_version: string | null;
};
type SelectedRun = { id: number; name: string; status: string };
type RunFilters = {
  strategy_name: string; status: string; validation_mode: string;
  data_version: string; start_date: string; end_date: string;
};
const emptyFilters = (): RunFilters => ({
  strategy_name: "", status: "", validation_mode: "", data_version: "",
  start_date: "", end_date: "",
});
type ComparisonPreviewItem = {
  run_id: number; name: string; strategy_name: string; start_date: string; end_date: string;
  data_version: string; run_code_version: string; initial_cash: string; validation_mode: string | null;
  fee_model_version: string | null; fee_parameters: Record<string, unknown>; adjust_type: string | null;
  strategy_contract: Record<string, unknown>; strategy_differences: string[];
  metric_source: "persisted" | "derived" | "missing_equity" | "invalid_equity";
  metric_code_version: string | null; total_return: string | null; annualized_return: string | null;
  maximum_drawdown: string | null; sharpe_ratio: string | null; turnover_rate: string | null;
  total_commission: string | null;
  delta_status: "baseline" | "available" | "unavailable"; delta_reasons: string[];
  total_return_delta: string | null; maximum_drawdown_delta: string | null;
  sharpe_ratio_delta: string | null;
};
type ComparisonCurve = NormalizedEquity & { runId: number; name: string; color: string };
type EquityPage = { items: Array<EquityObservation & { run_id: number }>; total: number };
const MAX_CURVE_EQUITY_ROWS = 10_000;
const CURVE_COLORS = ["#1d4ed8", "#b45309"];

const runPage = ref<BacktestRunListItem[]>([]);
const runTotal = ref(0);
const runOffset = ref(0);
const runError = ref("");
const runLoading = ref(false);
const loaded = ref(false);
const draftFilters = ref<RunFilters>(emptyFilters());
const activeFilters = ref<RunFilters>(emptyFilters());
const filterError = ref("");
let runRequestRevision = 0;
const selectedRuns = ref<SelectedRun[]>([]);
const comparison = ref<ComparisonPreviewItem[]>([]);
const comparisonError = ref("");
const comparisonLoading = ref(false);
const comparisonCurves = ref<ComparisonCurve[]>([]);
const curveNotes = ref<string[]>([]);
const curveLoading = ref(false);
const curveMode = ref<"return" | "drawdown">("return");
const curveCandidateId = ref<number | null>(null);
let comparisonRevision = 0;
let curveRequestRevision = 0;
let curveController: AbortController | null = null;
const curveCache = new Map<number, NormalizedEquity>();
let routeWriteQueue: Promise<void> = Promise.resolve();
let externalRouteRevision = 0;
let routeWriteInProgress = false;
let pendingRouteWrite: string | undefined;

function selectedIds(): number[] { return selectedRuns.value.map(run => run.id); }
function linkedDetailQuery() {
  return { ...route.query, runs: selectedRuns.value.length ? selectedIds().join(",") : undefined };
}
function writeSelectionToRoute() {
  const ids = selectedIds().join(",");
  const routeRevision = externalRouteRevision;
  routeWriteQueue = routeWriteQueue.then(async () => {
    if (routeRevision !== externalRouteRevision || ids !== selectedIds().join(",") || (route.path && route.path !== "/backtests")) return;
    if (route.query?.runs === ids || (!ids && !route.query?.runs)) return;
    routeWriteInProgress = true;
    pendingRouteWrite = ids || undefined;
    try { await router.replace({ query: { ...route.query, runs: pendingRouteWrite } }); }
    finally { routeWriteInProgress = false; pendingRouteWrite = undefined; }
  }).catch(() => {
    if (selectedIds().join(",") === ids) comparisonError.value = "比较链接未能保存到地址栏。";
  });
}
function resetComparison() {
  comparisonRevision++;
  curveRequestRevision++;
  curveController?.abort();
  curveController = null;
  curveCache.clear();
  comparison.value = [];
  comparisonError.value = "";
  comparisonLoading.value = false;
  comparisonCurves.value = [];
  curveNotes.value = [];
  curveLoading.value = false;
  curveMode.value = "return";
  curveCandidateId.value = null;
}
function isSelected(runId: number): boolean { return selectedRuns.value.some(run => run.id === runId); }
function toggleSelection(run: SelectedRun) {
  if (!isSelected(run.id) && (run.status !== "success" || selectedRuns.value.length >= 20)) return;
  selectedRuns.value = isSelected(run.id)
    ? selectedRuns.value.filter(item => item.id !== run.id)
    : [...selectedRuns.value, { id: run.id, name: run.name, status: run.status }];
  resetComparison();
  writeSelectionToRoute();
}
function clearSelection() {
  selectedRuns.value = [];
  resetComparison();
  writeSelectionToRoute();
}
function setBaseline(runId: number) {
  const baseline = selectedRuns.value.find(run => run.id === runId);
  if (!baseline || selectedRuns.value[0]?.id === runId) return;
  selectedRuns.value = [baseline, ...selectedRuns.value.filter(run => run.id !== runId)];
  resetComparison();
  writeSelectionToRoute();
}
async function readComparison(runIds: number[]): Promise<ComparisonPreviewItem[]> {
    const response = await fetch("/api/v1/backtests/runs/compare-preview", {
      method: "POST", headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify({ run_ids: runIds }),
    });
    if (!response.ok) {
      const body = await response.json().catch(() => null) as { detail?: string } | null;
      throw new Error(body?.detail || `HTTP ${response.status}`);
    }
    const result = await response.json() as ComparisonPreviewItem[];
    if (!Array.isArray(result) || result.length !== runIds.length
      || result.some((item, index) => item.run_id !== runIds[index])) {
      throw new Error("比较响应与所选运行不一致");
    }
    return result;
}
async function readEquityRows(runId: number, signal: AbortSignal): Promise<EquityObservation[]> {
  const rows: EquityObservation[] = [];
  let total: number | null = null;
  while (total === null || rows.length < total) {
    if (signal.aborted) throw new Error("净值读取已取消");
    const response = await fetch(`/api/v1/backtests/runs/${runId}/equity?limit=5000&offset=${rows.length}`, {
      headers: { Accept: "application/json" }, signal,
    });
    if (!response.ok) throw new Error(`净值读取失败（HTTP ${response.status}）`);
    const page = await response.json() as EquityPage;
    if (!Number.isSafeInteger(page.total) || page.total < 0 || !Array.isArray(page.items)
      || page.items.length > 5000 || page.items.some(row => row.run_id !== runId)
      || (total !== null && page.total !== total)) throw new Error("净值分页响应无效");
    total = page.total;
    if (total > MAX_CURVE_EQUITY_ROWS) throw new Error(`净值超过 ${MAX_CURVE_EQUITY_ROWS} 日展示上限`);
    if (!page.items.length && rows.length < total) throw new Error("净值分页缺失");
    rows.push(...page.items);
    if (rows.length > total) throw new Error("净值分页行数不一致");
  }
  return rows;
}
async function loadComparisonCurves(items: ComparisonPreviewItem[], revision: number) {
  const requestRevision = ++curveRequestRevision;
  curveController?.abort();
  const controller = new AbortController();
  curveController = controller;
  comparisonCurves.value = [];
  curveNotes.value = [];
  curveLoading.value = false;
  const candidate = items.slice(1).find(item => item.run_id === curveCandidateId.value);
  if (!candidate || candidate.delta_status !== "available") {
    curveNotes.value = [candidate
      ? `#${candidate.run_id}：${deltaStatusText(candidate)}，不能绘制同口径曲线。`
      : "请选择一项候选运行。"];
    curveController = null;
    return;
  }
  const pair = [items[0], candidate];
  curveLoading.value = true;
  const results = await Promise.all(pair.map(async item => {
    const cached = curveCache.get(item.run_id);
    if (cached) return cached;
    try {
      const rows = await readEquityRows(item.run_id, controller.signal);
      const normalized = normalizeEquity(item.initial_cash, rows);
      if (!controller.signal.aborted) curveCache.set(item.run_id, normalized);
      return normalized;
    } catch (cause) {
      return cause instanceof Error ? cause : new Error("净值读取失败");
    }
  }));
  if (revision !== comparisonRevision || requestRevision !== curveRequestRevision || controller.signal.aborted) return;
  const notes: string[] = [];
  const baseline = results[0];
  if (baseline instanceof Error) {
    notes.push(`基准 #${pair[0].run_id}：${baseline.message}；未绘制比较曲线。`);
  } else {
    const curves: ComparisonCurve[] = [];
    pair.forEach((item, index) => {
      const result = results[index];
      if (result instanceof Error) {
        notes.push(`#${item.run_id}：${result.message}，未纳入曲线。`);
      } else if (!sameEquityDates(baseline.dates, result.dates)) {
        notes.push(`#${item.run_id}：净值日期与基准不一致，未纳入曲线。`);
      } else {
        curves.push({ ...result, runId: item.run_id, name: item.name, color: CURVE_COLORS[index] });
      }
    });
    comparisonCurves.value = curves.length >= 2 ? curves : [];
    if (curves.length < 2) notes.push("完整且同日对齐的曲线不足两条。");
  }
  curveNotes.value = notes;
  curveLoading.value = false;
  if (curveController === controller) curveController = null;
}
function selectCurveCandidate() {
  if (comparison.value.length > 1) void loadComparisonCurves(comparison.value, comparisonRevision);
}
function setComparison(items: ComparisonPreviewItem[], revision: number) {
  comparison.value = items;
  curveCache.clear();
  curveCandidateId.value = items.slice(1).find(item => item.delta_status === "available")?.run_id
    ?? items[1]?.run_id ?? null;
  void loadComparisonCurves(items, revision);
}
const curveGeometry = computed(() => {
  if (comparisonCurves.value.length < 2) return null;
  const key = curveMode.value === "return" ? "returns" : "drawdowns";
  let minimum = 0;
  let maximum = 0;
  for (const curve of comparisonCurves.value) {
    for (const value of [0, ...curve[key]]) {
      minimum = Math.min(minimum, value);
      maximum = Math.max(maximum, value);
    }
  }
  const flat = minimum === maximum;
  if (flat) { minimum -= 0.01; maximum += 0.01; }
  const labelValue = (value: number) => curveMode.value === "return" ? 100 + value * 100 : value * 100;
  let axisDigits = 1;
  while (axisDigits < 8 && labelValue(minimum).toFixed(axisDigits) === labelValue(maximum).toFixed(axisDigits)) axisDigits++;
  return {
    minimum, maximum, flat, axisDigits,
    zeroY: 208 - ((0 - minimum) / (maximum - minimum)) * 196,
    lines: comparisonCurves.value.map(curve => ({
      runId: curve.runId, color: curve.color,
      points: curvePolyline([0, ...curve[key]], minimum, maximum),
    })),
  };
});
const curveDateTicks = computed(() => {
  const dates = comparisonCurves.value[0]?.dates ?? [];
  if (!dates.length) return [];
  return [0, 0.25, 0.5, 0.75, 1].map(fraction => dates[Math.round((dates.length - 1) * fraction)]);
});
function curvePercent(value: number): string { return `${(value * 100).toFixed(1)}%`; }
function curveAxisText(value: number): string {
  const digits = curveGeometry.value?.axisDigits ?? 1;
  const number = (curveMode.value === "return" ? 100 + value * 100 : value * 100).toFixed(digits);
  return curveMode.value === "return" ? number : `${number}%`;
}
function curveWorstDrawdown(curve: ComparisonCurve): string {
  return curvePercent(curve.drawdowns.reduce((worst, value) => Math.min(worst, value), 0));
}
async function compareSelected() {
  if (selectedRuns.value.length < 2 || comparisonLoading.value) return;
  const revision = ++comparisonRevision;
  comparisonLoading.value = true;
  comparisonError.value = "";
  comparison.value = [];
  try {
    const result = await readComparison(selectedIds());
    if (revision === comparisonRevision) {
      setComparison(result, revision);
    }
  } catch (cause) {
    if (revision === comparisonRevision) comparisonError.value = `比较预览失败：${cause instanceof Error ? cause.message : "未知错误"}`;
  } finally { if (revision === comparisonRevision) comparisonLoading.value = false; }
}
function parseLinkedRunIds(raw: unknown): number[] {
  if (raw === undefined || raw === null || raw === "") return [];
  if (typeof raw !== "string") throw new Error("runs 参数只能出现一次");
  const parts = raw.split(",");
  if (parts.length > 20 || parts.some(part => !/^[1-9]\d*$/.test(part))) {
    throw new Error("运行编号须为 1–20 个正整数");
  }
  const ids = parts.map(Number);
  if (ids.some(id => !Number.isSafeInteger(id)) || new Set(ids).size !== ids.length) {
    throw new Error("运行编号无效或重复");
  }
  return ids;
}
async function restoreSelectionFromRoute() {
  let ids: number[];
  try { ids = parseLinkedRunIds(route.query?.runs); }
  catch (cause) {
    selectedRuns.value = [];
    resetComparison();
    comparisonError.value = `比较链接无效：${cause instanceof Error ? cause.message : "未知错误"}`;
    return;
  }
  if (ids.join(",") === selectedIds().join(",")) {
    comparisonRevision++;
    curveRequestRevision++;
    curveController?.abort();
    curveController = null;
    comparisonLoading.value = false;
    curveLoading.value = false;
    return;
  }
  resetComparison();
  selectedRuns.value = [];
  if (!ids.length) return;
  const revision = comparisonRevision;
  comparisonLoading.value = true;
  try {
    if (ids.length === 1) {
      const response = await fetch(`/api/v1/backtests/runs/${ids[0]}`, { headers: { Accept: "application/json" } });
      if (!response.ok) throw new Error(`运行 #${ids[0]} 读取失败（HTTP ${response.status}）`);
      const run = await response.json() as SelectedRun;
      if (run.id !== ids[0] || run.status !== "success") throw new Error(`运行 #${ids[0]} 不可用于比较`);
      if (revision === comparisonRevision) selectedRuns.value = [{ id: run.id, name: run.name, status: run.status }];
    } else {
      const result = await readComparison(ids);
      if (revision === comparisonRevision) {
        selectedRuns.value = result.map(item => ({ id: item.run_id, name: item.name, status: "success" }));
        setComparison(result, revision);
      }
    }
  } catch (cause) {
    if (revision === comparisonRevision) comparisonError.value = `比较链接恢复失败：${cause instanceof Error ? cause.message : "未知错误"}`;
  } finally { if (revision === comparisonRevision) comparisonLoading.value = false; }
}
function metricSourceText(source: ComparisonPreviewItem["metric_source"]): string {
  return ({ persisted: "已保存", derived: "净值推导", missing_equity: "缺净值", invalid_equity: "净值无效" })[source];
}
const deltaReasonLabels: Record<string, string> = {
  baseline_metrics_unavailable: "基准指标缺失", candidate_metrics_unavailable: "本项指标缺失",
  start_date: "起始日不同", end_date: "结束日不同", data_version: "数据版本不同",
  validation_mode: "验证模式不同", run_code_version: "运行代码版本不同",
  adjust_type: "复权口径不同", fee_model_version: "费用模型不同",
  fee_parameters: "费用参数不同", initial_cash: "初始资金不同",
  validation_mode_missing: "验证模式未记录", adjust_type_missing: "复权口径未记录",
  fee_contract_missing: "费用合同不完整", metric_code_version: "指标代码版本不同或缺失",
  equity_dates_missing: "净值日期缺失", equity_dates_differ: "净值日期不同",
};
function deltaStatusText(item: ComparisonPreviewItem): string {
  if (item.delta_status === "baseline") return "基准";
  if (item.delta_status === "available") return "基础字段对齐，策略与输入差异见下；不可归因";
  return (item.delta_reasons ?? []).map(reason => deltaReasonLabels[reason] || reason).join("；") || "差值不可用";
}
function percentagePointDelta(value: string | null): string {
  return value === null ? "—" : `${new Intl.NumberFormat("zh-CN", { signDisplay: "always", maximumFractionDigits: 2 }).format(Number(value) * 100)} 个百分点`;
}
function sharpeDeltaText(item: ComparisonPreviewItem): string {
  if (item.delta_status !== "available") return "—";
  if (item.sharpe_ratio_delta === null) return "不可计算";
  return new Intl.NumberFormat("zh-CN", { signDisplay: "always", maximumFractionDigits: 3 }).format(Number(item.sharpe_ratio_delta));
}
const strategyFieldLabels: Record<string, string> = {
  strategy_name: "策略", factor_specs: "因子配置", top_n: "选股数量",
  signal_lag: "信号滞后", rebalance_rule: "调仓规则", rebalance_interval: "调仓间隔",
  portfolio_constraints: "组合约束", holding_period: "持有期", benchmark: "基准证券",
  allowed_security_types: "允许证券类型", symbols: "运行证券池", coverage_ratio: "覆盖比例",
  weighting_mode: "权重模式", minimum_score_policy: "最低评分政策",
  minimum_score: "最低评分", invested_weight: "投资权重", selection_policy: "选参政策",
  lot_size: "交易手数", training_start_date: "训练起始日",
  training_end_date: "训练结束日", validation_window_id: "验证窗口",
  training_artifact_verified: "训练产物核验", training_artifact_id: "训练产物编号",
  strategy_input_sha256: "策略输入摘要", data_snapshot_sha256: "市场输入摘要",
};
function strategyFieldLabel(field: string): string { return strategyFieldLabels[field] || field; }
function strategyContractText(value: unknown, field?: string): string {
  if (value === null || value === undefined) return "未记录";
  if (field === "factor_specs" && Array.isArray(value)) {
    return value.map(spec => {
      if (!spec || typeof spec !== "object") return JSON.stringify(spec);
      const factor = spec as Record<string, unknown>;
      const direction = factor.higher_is_better === true ? "越高越好"
        : factor.higher_is_better === false ? "越低越好" : "方向未记录";
      return `${String(factor.factor_name ?? "未记录因子")} (${String(factor.factor_version ?? "版本未记录")}) · 权重 ${String(factor.weight ?? "未记录")} · ${direction}`;
    }).join("\n");
  }
  if (field === "symbols" && Array.isArray(value)) return `共 ${value.length} 只`;
  if (field === "training_artifact_verified" && typeof value === "boolean") return value ? "已核验" : "未核验";
  return typeof value === "string" ? value : JSON.stringify(value);
}
function strategyDifferenceText(item: ComparisonPreviewItem): string {
  if (item.delta_status === "baseline") return "基准";
  return item.strategy_differences.length ? `${item.strategy_differences.length} 项字段不同` : "已记录预览字段无差异";
}
function symbolChangeSummary(baseline: unknown, candidate: unknown): string {
  if (!Array.isArray(baseline) || !Array.isArray(candidate)
    || ![...baseline, ...candidate].every(value => typeof value === "string")) {
    return "证券池列表未完整记录";
  }
  const before = new Set(baseline as string[]);
  const after = new Set(candidate as string[]);
  const added = [...after].filter(symbol => !before.has(symbol)).sort();
  const removed = [...before].filter(symbol => !after.has(symbol)).sort();
  const shared = [...before].filter(symbol => after.has(symbol)).length;
  const sample = (symbols: string[]) => symbols.length
    ? `（如 ${symbols.slice(0, 5).join("、")}${symbols.length > 5 ? " 等" : ""}）` : "";
  return `交集 ${shared} 只；新增 ${added.length} 只${sample(added)}；移除 ${removed.length} 只${sample(removed)}`;
}
function fullSymbolList(value: unknown): string {
  return Array.isArray(value) ? value.join("、") : "未记录";
}

function applyFilters() {
  const filters = draftFilters.value;
  if (filters.start_date && filters.end_date && filters.start_date > filters.end_date) {
    filterError.value = "起始日不能晚于结束日。";
    return;
  }
  filterError.value = "";
  activeFilters.value = { ...filters, strategy_name: filters.strategy_name.trim(), data_version: filters.data_version.trim() };
  return loadRuns(0);
}
function clearFilters() {
  draftFilters.value = emptyFilters();
  activeFilters.value = emptyFilters();
  filterError.value = "";
  return loadRuns(0);
}

async function loadRuns(offset = 0) {
  const revision = ++runRequestRevision;
  runLoading.value = true;
  runError.value = "";
  try {
    const query = new URLSearchParams({ limit: "20", offset: String(offset) });
    for (const [key, value] of Object.entries(activeFilters.value)) {
      if (value) query.set(key, value);
    }
    const response = await fetch(`/api/v1/backtests/runs?${query}`);
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const page = await response.json() as {
      items: BacktestRunListItem[];
      total: number;
    };
    if (revision !== runRequestRevision) return;
    runPage.value = page.items;
    runTotal.value = page.total;
    runOffset.value = offset;
    loaded.value = true;
  } catch (cause) {
    if (revision !== runRequestRevision) return;
    const fallback = loaded.value ? "保留上次读取的列表" : props.backtests ? "下表显示工作台最近摘要" : "暂无历史运行";
    runError.value = `历史运行读取失败：${cause instanceof Error ? cause.message : "未知错误"}；${fallback}。`;
  } finally { if (revision === runRequestRevision) runLoading.value = false; }
}
onMounted(() => { void loadRuns(); void restoreSelectionFromRoute(); });
onBeforeUnmount(() => { comparisonRevision++; curveRequestRevision++; curveController?.abort(); });
watch(() => route.query?.runs, runs => {
  if (routeWriteInProgress && runs === pendingRouteWrite) return;
  externalRouteRevision++;
  void restoreSelectionFromRoute();
}, { flush: "sync" });

function sharpeText(run: BacktestRunSnapshot | BacktestRunListItem): string {
  if (run.status !== "success") return "—";
  if (!("performance_status" in run)) return "待读取";
  if (run.performance_status === "missing_equity") return "缺净值";
  if (run.performance_status === "invalid_equity") return "净值无效";
  if (run.performance_status !== "analyzed" && run.performance_status !== "derived") return "—";
  return run.sharpe_ratio === null ? "不可计算" : Number(run.sharpe_ratio).toLocaleString("zh-CN", { maximumFractionDigits: 3 });
}
function sharpeTitle(run: BacktestRunSnapshot | BacktestRunListItem): string | undefined {
  if (!("performance_status" in run) || !run.performance_code_version) return undefined;
  return `${run.performance_status === "derived" ? "净值推导" : "已保存绩效"}；计算版本：${run.performance_code_version}`;
}
function validationModeLabel(value: unknown): string {
  if (value === "none") return "探索性（未验证）";
  if (value === "rolling") return "滚动窗口";
  if (value === "out_of_sample") return "样本外区间";
  return typeof value === "string" && value ? value : "未记录";
}
function runContractText(run: BacktestRunSnapshot | BacktestRunListItem, field: "validation_mode" | "fee_model_version" | "data_version"): string {
  if (field === "data_version") return "data_version" in run ? String(run.data_version) : "待读取";
  if (!("parameters" in run)) return "待读取";
  const value = run.parameters[field];
  if (field === "validation_mode") return validationModeLabel(value);
  return typeof value === "string" && value ? value : "未记录";
}
</script>

<template>
  <section v-if="props.factors" id="factors" class="dashboard-section">
    <div class="section-title">
      <BarChart3 :size="20" aria-hidden="true" />
      <h2>因子运行</h2>
    </div>

    <div class="table-panel full">
      <table>
        <thead>
          <tr>
            <th>因子</th>
            <th>版本</th>
            <th>状态</th>
            <th>输入区间</th>
            <th>写入行</th>
            <th>结束</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="run in props.factors?.latest_runs ?? []" :key="run.id">
            <td>{{ run.factor_name }}</td>
            <td>{{ run.factor_version }}</td>
            <td><span :class="statusClass(run.status)">{{ statusLabel(run.status) }}</span></td>
            <td>{{ formatDate(run.input_start_date) }} - {{ formatDate(run.input_end_date) }}</td>
            <td>{{ formatNumber(run.rows_written) }}</td>
            <td>{{ formatDateTime(run.finished_at) }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </section>

  <section id="backtests" class="dashboard-section">
    <div class="section-title">
      <LineChart :size="20" aria-hidden="true" />
      <h2>回测结果</h2>
    </div>
    <form class="backtest-filters" @submit.prevent="applyFilters">
      <label>策略标识（精确）<input v-model="draftFilters.strategy_name" type="text" maxlength="100" placeholder="例如 factor_selection" /></label>
      <label>状态<select v-model="draftFilters.status"><option value="">全部</option><option value="success">成功</option><option value="failed">失败</option><option value="running">运行中</option></select></label>
      <label>验证模式<select v-model="draftFilters.validation_mode"><option value="">全部</option><option value="none">探索性（未验证）</option><option value="rolling">滚动窗口</option><option value="out_of_sample">样本外区间</option></select></label>
      <label>数据版本（精确）<input v-model="draftFilters.data_version" type="text" maxlength="100" placeholder="全部版本" /></label>
      <label>起始日不早于<input v-model="draftFilters.start_date" type="date" /></label>
      <label>结束日不晚于<input v-model="draftFilters.end_date" type="date" /></label>
      <div class="backtest-filter-actions"><button type="submit" :disabled="runLoading">筛选运行</button><button type="button" :disabled="runLoading" @click="clearFilters">清除筛选</button></div>
    </form>
    <p v-if="filterError" role="alert" class="warning">{{ filterError }}</p>
    <p v-if="runError" role="alert">{{ runError }} <button type="button" :disabled="runLoading" @click="loadRuns(runOffset)">重试读取</button></p>
    <p v-if="runLoading" role="status">正在读取历史运行…</p>

    <div class="table-panel full">
      <table>
        <thead>
          <tr>
            <th>比较</th>
            <th>名称</th>
            <th>策略</th>
            <th>状态</th>
            <th>区间</th>
            <th>验证模式</th>
            <th>数据版本</th>
            <th>费用模型</th>
            <th>最终权益</th>
            <th>收益</th>
            <th>年化夏普</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="run in loaded ? runPage : (props.backtests?.latest_runs ?? [])" :key="run.id">
            <td><input v-if="loaded && run.status === 'success'" type="checkbox" :aria-label="`选择回测 ${run.name}`" :checked="isSelected(run.id)" :disabled="!isSelected(run.id) && selectedRuns.length >= 20" @change="toggleSelection(run as BacktestRunListItem)" /></td>
            <td><RouterLink v-if="loaded" :to="{ path: `/backtests/runs/${run.id}`, query: linkedDetailQuery() }">{{ run.name }}</RouterLink><span v-else>{{ run.name }}</span></td>
            <td>{{ strategyLabel(run.strategy_name) }}<small>{{ run.strategy_name }}</small></td>
            <td><span :class="statusClass(run.status)">{{ statusLabel(run.status) }}</span></td>
            <td>{{ formatDate(run.start_date) }} - {{ formatDate(run.end_date) }}</td>
            <td>{{ runContractText(run, 'validation_mode') }}</td>
            <td :title="runContractText(run, 'data_version')">{{ runContractText(run, 'data_version') }}</td>
            <td>{{ runContractText(run, 'fee_model_version') }}</td>
            <td>{{ formatNumber(run.final_equity) }}</td>
            <td>{{ formatPercent(run.total_return) }}</td>
            <td :title="sharpeTitle(run)">{{ sharpeText(run) }}</td>
          </tr>
        </tbody>
      </table>
    </div>
    <p class="table-note">夏普按日净值收益、252 个交易日年化，无风险利率取 0。净值推导仅用于只读浏览；完整绩效可在详情生成。不同区间和研究合同的结果不可直接排名。</p>
    <p v-if="loaded && runTotal === 0">当前筛选条件下无回测运行。</p>
    <p v-if="loaded && runTotal > 0">
      第 {{ runOffset + 1 }}–{{ Math.min(runOffset + 20, runTotal) }} / {{ runTotal }} 条
      <button type="button" :disabled="runOffset === 0 || runLoading" @click="loadRuns(runOffset - 20)">上一页</button>
      <button type="button" :disabled="runOffset + 20 >= runTotal || runLoading" @click="loadRuns(runOffset + 20)">下一页</button>
    </p>
    <div v-if="loaded || selectedRuns.length || comparisonError" class="comparison-controls">
      <p>已选 {{ selectedRuns.length }} / 20 项（跨页保留）</p>
      <div v-if="selectedRuns.length" class="comparison-selection">
        <span v-for="(item, index) in selectedRuns" :key="item.id">
          #{{ item.id }} {{ item.name }} <strong v-if="index === 0">基准</strong>
          <button v-else type="button" :aria-label="`设回测 ${item.name} 为基准`" @click="setBaseline(item.id)">设为基准</button>
          <button type="button" :aria-label="`移除回测 ${item.name}`" @click="toggleSelection(item)">移除</button>
        </span>
      </div>
      <button type="button" :disabled="selectedRuns.length < 2 || comparisonLoading" @click="compareSelected">{{ comparisonLoading ? '比较中…' : '比较所选回测' }}</button>
      <button type="button" :disabled="selectedRuns.length === 0 && !route.query?.runs" @click="clearSelection">清空选择与链接</button>
      <p v-if="comparisonError" role="alert" class="warning">{{ comparisonError }}</p>
    </div>
    <section v-if="comparison.length" class="comparison-preview">
      <h3>研究比较预览</h3>
      <p class="table-note">首项 #{{ comparison[0]?.run_id }} 为基准；差值是本项减基准，收益与回撤用百分点，夏普用原值。只在基础合同、指标代码版本及实际净值日期一致时展示差值；仍需核对股票池、调仓、约束与训练区间。显式费用不含已计入成交价的滑点，净值指标已反映滑点。预览不代表因果归因或样本外认证。</p>
      <section class="comparison-curve" aria-label="回测净值走势比较">
        <h4>同日净值与回撤走势</h4>
        <label>对比运行
          <select v-model.number="curveCandidateId" @change="selectCurveCandidate">
            <option v-for="item in comparison.slice(1)" :key="item.run_id" :value="item.run_id">#{{ item.run_id }} {{ item.name }}{{ item.delta_status === 'available' ? '' : '（基础口径未对齐）' }}</option>
          </select>
        </label>
        <p class="table-note">仅画基准与当前候选；完整净值逐日对齐后，以各自初始资金为 100，首日变化计入。回撤从初始资金作为首个峰值计算。曲线是展示近似值，表格指标以服务端数值为准；费用和滑点已计入净值。策略字段差异仍需人工核对，走势不构成收益归因、样本外或实盘资格。</p>
        <div class="comparison-curve-modes">
          <button type="button" :aria-pressed="curveMode === 'return'" @click="curveMode = 'return'">归一化净值</button>
          <button type="button" :aria-pressed="curveMode === 'drawdown'" @click="curveMode = 'drawdown'">回撤</button>
          <button type="button" :disabled="curveLoading" @click="selectCurveCandidate">重试读取曲线</button>
        </div>
        <p v-if="curveLoading" role="status">正在读取完整净值…</p>
        <figure v-if="curveGeometry" class="comparison-curve-figure">
          <svg viewBox="0 0 800 240" role="img" :aria-label="`${curveMode === 'return' ? '归一化净值' : '回撤'}：基准 #${comparisonCurves[0]?.runId} 与候选 #${comparisonCurves[1]?.runId}`">
            <line x1="45" x2="790" :y1="curveGeometry.zeroY" :y2="curveGeometry.zeroY" stroke="#94a3b8" stroke-dasharray="4 4" />
            <polyline v-for="line in curveGeometry.lines" :key="line.runId" :points="line.points" fill="none" :stroke="line.color" stroke-width="2" vector-effect="non-scaling-stroke" />
            <text v-if="curveGeometry.flat" x="3" y="112">{{ curveMode === 'return' ? '100' : '0%' }}（全期无变化）</text>
            <template v-else>
              <text x="3" y="16">{{ curveAxisText(curveGeometry.maximum) }}</text>
              <text x="3" y="218">{{ curveAxisText(curveGeometry.minimum) }}</text>
            </template>
          </svg>
          <div class="comparison-curve-dates"><span v-for="(date, index) in curveDateTicks" :key="index">{{ index === 0 ? '期初 → ' : '' }}{{ date }}</span></div>
          <figcaption><span v-for="curve in comparisonCurves" :key="curve.runId"><i :style="{ backgroundColor: curve.color }" aria-hidden="true"></i>#{{ curve.runId }} {{ curve.name }} · 期末 {{ curvePercent(curve.returns[curve.returns.length - 1]) }} · 最深回撤 {{ curveWorstDrawdown(curve) }}</span></figcaption>
        </figure>
        <p v-for="note in curveNotes" :key="note" class="table-note">{{ note }}</p>
      </section>
      <div class="scroll"><table><thead><tr><th>回测</th><th>差值条件</th><th>区间</th><th>数据版本</th><th>验证模式</th><th>费用合同</th><th>复权</th><th>初始资金</th><th>收益</th><th>收益差值</th><th>年化收益</th><th>最大回撤</th><th>回撤差值</th><th>夏普</th><th>夏普差值</th><th>换手</th><th>显式费用</th><th>指标来源</th></tr></thead>
        <tbody><tr v-for="item in comparison" :key="item.run_id"><td><RouterLink :to="{ path: `/backtests/runs/${item.run_id}`, query: linkedDetailQuery() }">#{{ item.run_id }} {{ item.name }}</RouterLink><small>{{ strategyLabel(item.strategy_name) }} · {{ item.strategy_name }}</small><small>{{ strategyDifferenceText(item) }}</small></td><td>{{ deltaStatusText(item) }}</td><td>{{ formatDate(item.start_date) }}–{{ formatDate(item.end_date) }}</td><td>{{ item.data_version }}</td><td>{{ validationModeLabel(item.validation_mode) }}</td><td :title="JSON.stringify(item.fee_parameters)">{{ item.fee_model_version || '未记录' }}<small>{{ JSON.stringify(item.fee_parameters) }}</small></td><td>{{ item.adjust_type || '未记录' }}</td><td>{{ formatNumber(item.initial_cash) }}</td><td>{{ formatPercent(item.total_return) }}</td><td>{{ item.delta_status === 'available' ? percentagePointDelta(item.total_return_delta) : '—' }}</td><td>{{ formatPercent(item.annualized_return) }}</td><td>{{ formatPercent(item.maximum_drawdown) }}</td><td>{{ item.delta_status === 'available' ? percentagePointDelta(item.maximum_drawdown_delta) : '—' }}</td><td>{{ item.sharpe_ratio === null ? item.metric_source === 'persisted' || item.metric_source === 'derived' ? '不可计算' : '—' : Number(item.sharpe_ratio).toLocaleString('zh-CN', { maximumFractionDigits: 3 }) }}</td><td>{{ sharpeDeltaText(item) }}</td><td>{{ formatPercent(item.turnover_rate) }}</td><td>{{ formatNumber(item.total_commission) }}</td><td>{{ metricSourceText(item.metric_source) }}<small v-if="item.metric_code_version">{{ item.metric_code_version }}</small><small>运行 {{ item.run_code_version }}</small></td></tr></tbody></table></div>
      <div class="comparison-contract-review">
        <h4>相对基准的策略合同差异</h4>
        <p class="table-note">仅核对下列已记录字段；缺失显示“未记录”。差异帮助定位研究假设变化，不能单独用于收益归因。</p>
        <details v-for="item in comparison.slice(1)" :key="`contract-${item.run_id}`">
          <summary>#{{ item.run_id }} {{ item.name }} · {{ strategyDifferenceText(item) }}</summary>
          <p v-if="!item.strategy_differences.length">预览范围内的策略字段一致，仍需核对数据血缘、时点资格和其他研究假设。</p>
          <div v-else class="scroll"><table><thead><tr><th>字段</th><th>基准 #{{ comparison[0]?.run_id }}</th><th>本项 #{{ item.run_id }}</th></tr></thead><tbody>
            <tr v-for="field in item.strategy_differences" :key="field">
              <th>{{ strategyFieldLabel(field) }}<small>{{ field }}</small><small v-if="field === 'symbols'">{{ symbolChangeSummary(comparison[0]?.strategy_contract?.symbols, item.strategy_contract.symbols) }}</small></th>
              <td><code>{{ strategyContractText(comparison[0]?.strategy_contract?.[field], field) }}</code><details v-if="field === 'symbols'"><summary>查看基准完整代码</summary><code>{{ fullSymbolList(comparison[0]?.strategy_contract?.symbols) }}</code></details></td>
              <td><code>{{ strategyContractText(item.strategy_contract[field], field) }}</code><details v-if="field === 'symbols'"><summary>查看本项完整代码</summary><code>{{ fullSymbolList(item.strategy_contract.symbols) }}</code></details></td>
            </tr>
          </tbody></table></div>
        </details>
      </div>
    </section>
  </section>
</template>

<style scoped>
.comparison-contract-review { margin-top: 1.25rem; }
.comparison-contract-review h4 { margin: 0 0 .35rem; }
.comparison-contract-review details { border-top: 1px solid #dce3ed; padding: .65rem 0; }
.comparison-contract-review summary { cursor: pointer; font-weight: 600; }
.comparison-contract-review table { min-width: 680px; }
.comparison-contract-review th { max-width: 310px; vertical-align: top; }
.comparison-contract-review td { min-width: 240px; max-width: 450px; vertical-align: top; }
.comparison-contract-review code { white-space: pre-wrap; overflow-wrap: anywhere; }
</style>
