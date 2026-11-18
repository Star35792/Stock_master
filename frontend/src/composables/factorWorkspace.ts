import { computed, nextTick, onBeforeUnmount, reactive, ref, watch } from "vue";

import { fetchFactorDefinitions, fetchFactorProfiles } from "../api";
import {
  fetchBacktestFinalHoldingsTyped,
  preflightFactorSelectionBacktest,
  runFactorSelectionBacktest,
} from "../factorSelectionApi";
import type { FactorScoreSpecInput, FactorSelectionBacktestRequest, FactorSelectionPreflight } from "../factorSelectionApi";
import type { FactorDefinition, FactorProfile } from "../types";
import { categoryLabel, factorCategoryOptions, factorDisplayName, localizedDescription, localizedName, parameterLabels } from "../components/operations/factorLabels";

/**
 * 因子工作区状态：因子目录浏览、方向选择、画像 IC 时序，
 * 以及「选因子 → 预检 → 发起选股回测 → 读取最终持仓」链路。
 * 逻辑与视图分离，外部依赖与回写研究上下文的钩子均可注入。
 */
export interface FactorWorkspaceDeps {
  fetchFactorDefinitions: typeof fetchFactorDefinitions;
  fetchFactorProfiles: typeof fetchFactorProfiles;
  fetchBacktestFinalHoldingsTyped: typeof fetchBacktestFinalHoldingsTyped;
  preflightFactorSelectionBacktest: typeof preflightFactorSelectionBacktest;
  runFactorSelectionBacktest: typeof runFactorSelectionBacktest;
}

export interface FactorWorkspaceOptions {
  /** 回测成功后回写全局研究上下文（run_id / 因子方向 / 基准），供组合草案页预填。 */
  onBacktestRun?: (context: {
    runId: number;
    name: string | null;
    directions: Array<{ factor_name: string; factor_version: string; display_name: string; higher_is_better: boolean }>;
    benchmark: string | null;
  }) => void;
}

type FactorDirection = "unconfigured" | "higher" | "lower";

export interface SelectedFactorConfig {
  factor: FactorDefinition;
  direction: FactorDirection;
}

export interface FactorDirectionSnapshot {
  factor_name: string;
  factor_version: string;
  display_name: string;
  weight: "1";
  higher_is_better: boolean;
  direction_source: "manual";
}

const defaultDeps: FactorWorkspaceDeps = {
  fetchFactorDefinitions,
  fetchFactorProfiles,
  fetchBacktestFinalHoldingsTyped,
  preflightFactorSelectionBacktest,
  runFactorSelectionBacktest,
};

export function createFactorWorkspace(
  deps: FactorWorkspaceDeps = defaultDeps,
  options: FactorWorkspaceOptions = {},
) {
  const backtestForm = reactive({
    name: `工作台选股回测 ${new Date().toISOString().slice(0, 10)}`,
    top_n: "20",
    start_date: "",
    end_date: "",
    initial_cash: "1000000",
    rebalance_rule: "every_n_trading_days" as
      | "daily"
      | "weekly"
      | "monthly"
      | "every_n_trading_days",
    rebalance_interval: "20",
    benchmark: "",
    useConstraints: false,
    max_symbol_weight: "0.20",
    max_industry_weight: "0.40",
    cash_reserve_rate: "0.05",
  });
  const isRunningBacktest = ref(false);
  const backtestError = ref<string | null>(null);
  const preflight = ref<FactorSelectionPreflight | null>(null);
  const preflightError = ref("");
  const preflightLoading = ref(false);
  let preflightController: AbortController | null = null;
  const selected = ref<SelectedFactorConfig[]>([]);
  const missingDirectionKeys = ref<string[]>([]);
  const selectionPanel = ref<HTMLElement | null>(null);
  const backtestResult = ref<Awaited<
    ReturnType<typeof runFactorSelectionBacktest>
  > | null>(null);
  const backtestFactorSnapshot = ref<FactorDirectionSnapshot[]>([]);
  const backtestHoldings = ref<Awaited<
    ReturnType<typeof fetchBacktestFinalHoldingsTyped>
  > | null>(null);
  const isLoadingHoldings = ref(false);
  let backtestRequestId = 0;
  let backtestController: AbortController | null = null;
  let holdingsRequestId = 0;
  let holdingsController: AbortController | null = null;

  function factorKey(factor: FactorDefinition) {
    return `${factor.name}@${factor.version}`;
  }

  function invalidateBacktestState() {
    preflightController?.abort();
    preflightController = null;
    preflight.value = null;
    preflightError.value = "";
    preflightLoading.value = false;
    backtestRequestId += 1;
    backtestController?.abort();
    backtestController = null;
    holdingsRequestId += 1;
    holdingsController?.abort();
    holdingsController = null;
    isRunningBacktest.value = false;
    isLoadingHoldings.value = false;
    backtestResult.value = null;
    backtestFactorSnapshot.value = [];
    backtestHoldings.value = null;
    backtestError.value = null;
    missingDirectionKeys.value = [];
  }

  watch([backtestForm, selected], invalidateBacktestState, { deep: true });

  onBeforeUnmount(() => {
    preflightController?.abort();
    backtestRequestId += 1;
    holdingsRequestId += 1;
    backtestController?.abort();
    holdingsController?.abort();
  });

  function toFactorScoreSpec(config: SelectedFactorConfig): FactorScoreSpecInput {
    let higherIsBetter: boolean;
    switch (config.direction) {
      case "higher":
        higherIsBetter = true;
        break;
      case "lower":
        higherIsBetter = false;
        break;
      case "unconfigured":
        throw new Error(`因子 ${localizedName(config.factor)} 尚未确认方向`);
    }
    return {
      factor_name: config.factor.name,
      factor_version: config.factor.version,
      weight: "1",
      higher_is_better: higherIsBetter,
    };
  }

  function directionSnapshot(
    config: SelectedFactorConfig,
    spec: FactorScoreSpecInput,
  ): FactorDirectionSnapshot {
    return {
      factor_name: spec.factor_name,
      factor_version: spec.factor_version,
      display_name: localizedName(config.factor),
      weight: "1",
      higher_is_better: spec.higher_is_better,
      direction_source: "manual",
    };
  }

  function createBacktestPayload(factorSpecs: FactorScoreSpecInput[]): FactorSelectionBacktestRequest {
    const interval = Number(backtestForm.rebalance_interval);
    return {
      name: backtestForm.name || "工作台选股回测",
      strategy_name: "factor_selection",
      factor_specs: factorSpecs,
      top_n: Number(backtestForm.top_n),
      signal_lag: 1,
      rebalance_rule: backtestForm.rebalance_rule,
      rebalance_interval: backtestForm.rebalance_rule === "every_n_trading_days" ? interval : 1,
      holding_period: 1,
      benchmark: backtestForm.benchmark.trim() || null,
      allowed_security_types: ["stock"],
      coverage_ratio: "0.8",
      weighting_mode: "equal_weight_fully_invested",
      minimum_score_policy: "always_fully_invested",
      invested_weight: "1",
      portfolio_constraints: backtestForm.useConstraints ? {
        max_symbol_weight: backtestForm.max_symbol_weight,
        max_industry_weight: backtestForm.max_industry_weight,
        cash_reserve_rate: backtestForm.cash_reserve_rate,
        security_type_budgets: null,
      } : null,
      start_date: backtestForm.start_date,
      end_date: backtestForm.end_date,
      adjust_type: "qfq",
      initial_cash: backtestForm.initial_cash,
      commission_rate: "0.0003",
      slippage_bps: "2",
      lot_size: 100,
      fee_model_version: "a_share_cost_v2",
      min_commission: "5",
      sell_stamp_duty_rate: null,
      transfer_fee_rate: "0.00001",
      exchange_fee_rate: "0.0000487",
      code_version: "auto",
      data_version: "auto",
      validation_mode: "none",
    };
  }

  async function checkBacktestReadiness() {
    if (!selected.value.length || selected.value.some((item) => item.direction === "unconfigured")) {
      preflightError.value = "请先选择因子并确认方向。";
      return;
    }
    preflightController?.abort();
    const controller = new AbortController();
    preflightController = controller;
    preflight.value = null;
    preflightError.value = "";
    preflightLoading.value = true;
    try {
      preflight.value = await deps.preflightFactorSelectionBacktest(
        createBacktestPayload(selected.value.map(toFactorScoreSpec)), controller.signal,
      );
    } catch (cause) {
      if (!controller.signal.aborted) preflightError.value = cause instanceof Error ? cause.message : "预检失败";
    } finally {
      if (!controller.signal.aborted) preflightLoading.value = false;
    }
  }

  async function runBacktest() {
    if (selected.value.length === 0) {
      backtestError.value = "请先在左侧选择至少一个因子。";
      return;
    }
    const missing = selected.value.filter((item) => item.direction === "unconfigured");
    if (missing.length > 0) {
      missingDirectionKeys.value = missing.map((item) => factorKey(item.factor));
      const names = missing.slice(0, 3).map((item) => localizedName(item.factor));
      const remainder = missing.length - names.length;
      backtestError.value = `请先确认因子方向：${names.join("、")}${remainder > 0 ? ` 等 ${missing.length} 项` : ""}。`;
      selectionPanel.value?.scrollIntoView({ behavior: "smooth", block: "center" });
      void nextTick(() => {
        selectionPanel.value
          ?.querySelector<HTMLSelectElement>(".direction-select[aria-invalid='true']")
          ?.focus();
      });
      return;
    }
    const topN = Number(backtestForm.top_n);
    const interval = Number(backtestForm.rebalance_interval);
    if (!Number.isFinite(topN) || topN <= 0) {
      backtestError.value = "持仓数量必须是正整数。";
      return;
    }
    if (
      backtestForm.rebalance_rule === "every_n_trading_days" &&
      (!Number.isFinite(interval) || interval < 1)
    ) {
      backtestError.value = "调仓间隔必须是正整数。";
      return;
    }
    const factorSpecs = selected.value.map(toFactorScoreSpec);
    const factorSnapshot = selected.value.map((config, index) =>
      directionSnapshot(config, factorSpecs[index]),
    );
    invalidateBacktestState();
    isRunningBacktest.value = true;
    const requestId = backtestRequestId;
    const controller = new AbortController();
    backtestController = controller;
    try {
      const result = await deps.runFactorSelectionBacktest(
        createBacktestPayload(factorSpecs), controller.signal,
      );
      if (requestId !== backtestRequestId) return;
      backtestResult.value = result;
      backtestFactorSnapshot.value = factorSnapshot;
      // 写入全局研究上下文，供组合草案页一键预填。
      options.onBacktestRun?.({
        runId: result.run_id,
        name: result.name,
        directions: factorSnapshot.map((factor) => ({
          factor_name: factor.factor_name,
          factor_version: factor.factor_version,
          display_name: factor.display_name,
          higher_is_better: factor.higher_is_better,
        })),
        benchmark: backtestForm.benchmark.trim() || null,
      });
    } catch (error) {
      if (requestId !== backtestRequestId) return;
      if (error instanceof DOMException && error.name === "AbortError") return;
      backtestError.value = error instanceof Error ? error.message : "回测发起失败";
    } finally {
      if (requestId === backtestRequestId) {
        isRunningBacktest.value = false;
        backtestController = null;
      }
    }
  }

  async function loadBacktestHoldings() {
    if (!backtestResult.value) return;
    const runId = backtestResult.value.run_id;
    holdingsController?.abort();
    const requestId = ++holdingsRequestId;
    const controller = new AbortController();
    holdingsController = controller;
    isLoadingHoldings.value = true;
    try {
      const holdings = await deps.fetchBacktestFinalHoldingsTyped(runId, controller.signal);
      if (requestId !== holdingsRequestId || backtestResult.value?.run_id !== runId) return;
      backtestHoldings.value = holdings;
    } catch (error) {
      if (requestId !== holdingsRequestId) return;
      if (error instanceof DOMException && error.name === "AbortError") return;
      backtestError.value = error instanceof Error ? error.message : "最终持仓读取失败";
    } finally {
      if (requestId === holdingsRequestId) {
        isLoadingHoldings.value = false;
        holdingsController = null;
      }
    }
  }

  const definitions = ref<FactorDefinition[]>([]);
  const activeFactor = ref<FactorDefinition | null>(null);
  const profiles = ref<FactorProfile[]>([]);
  const search = ref("");
  const frequency = ref("");
  const category = ref("");

  function profileOverlapsBacktest(profile: FactorProfile) {
    return Boolean(backtestForm.start_date && profile.trade_date >= backtestForm.start_date);
  }

  // 画像 IC 时序：优先 rank_ic_1d（横截面秩相关更稳健），缺失时退回 ic_1d。
  const icMetric = computed<"ic_1d" | "rank_ic_1d">(() => {
    const hasRank = profiles.value.some((profile) => profile.rank_ic_1d !== null);
    const hasRaw = profiles.value.some((profile) => profile.ic_1d !== null);
    return hasRank ? "rank_ic_1d" : hasRaw ? "ic_1d" : "ic_1d";
  });
  const icPoints = computed(() =>
    profiles.value.map((profile) => ({
      trade_date: profile.trade_date,
      value: icMetric.value === "rank_ic_1d"
        ? (profile.rank_ic_1d === null ? null : Number(profile.rank_ic_1d))
        : (profile.ic_1d === null ? null : Number(profile.ic_1d)),
    })),
  );

  const filteredDefinitions = computed(() => definitions.value.filter((factor) => {
    const query = search.value.trim().toLowerCase();
    const matchesText = !query || [factor.name, factor.display_name ?? "", localizedName(factor)].some((value) => value.toLowerCase().includes(query));
    return matchesText && (!frequency.value || factor.frequency === frequency.value) && (!category.value || factor.category === category.value);
  }));

  function isSelected(factor: FactorDefinition) {
    return selected.value.some((item) => item.factor.id === factor.id);
  }

  function toggleFactor(factor: FactorDefinition) {
    if (isSelected(factor)) {
      selected.value = selected.value.filter((item) => item.factor.id !== factor.id);
    } else {
      selected.value = [
        ...selected.value,
        { factor, direction: "unconfigured" },
      ];
      parameterValues[factor.name] ??= Object.fromEntries(
        schemaProperties(factor).map(({ key, value }) => [key, String(value.default ?? "")]),
      );
    }
    void loadProfiles(factor);
  }

  function removeFactor(factor: FactorDefinition) {
    selected.value = selected.value.filter((item) => item.factor.id !== factor.id);
    if (activeFactor.value?.id === factor.id) activeFactor.value = null;
  }

  function schemaProperties(factor: FactorDefinition | null) {
    const schema = factor?.parameters_schema;
    if (!schema || typeof schema !== "object") return [];
    const properties = schema.properties;
    if (properties && typeof properties === "object") {
      return Object.entries(properties as Record<string, Record<string, unknown>>).map(([key, value]) => ({ key, label: parameterLabels[key] ?? key, value }));
    }
    const result: Array<{ key: string; label: string; value: Record<string, unknown> }> = [];
    const components = Array.isArray(schema.components) ? schema.components : [];
    for (const component of components) {
      if (!component || typeof component !== "object") continue;
      const item = component as Record<string, unknown>;
      const name = String(item.factor_name ?? "component");
      result.push({ key: `weight:${name}`, label: `${factorDisplayName(name)}权重`, value: { type: "number", default: item.weight ?? "" } });
    }
    for (const [key, value] of Object.entries(schema)) {
      if (key !== "components" && ["string", "number", "boolean"].includes(typeof value)) {
        result.push({ key, label: parameterLabels[key] ?? key, value: { type: typeof value === "number" ? "number" : "string", default: value } });
      }
    }
    return result;
  }

  const isLoading = ref(false);
  const isLoadingProfiles = ref(false);
  const errorMessage = ref<string | null>(null);
  const parameterValues = reactive<Record<string, Record<string, string | number>>>({});
  let profileRequestId = 0;

  async function loadDefinitions() {
    isLoading.value = true;
    errorMessage.value = null;
    try {
      const result = await deps.fetchFactorDefinitions({ is_active: true });
      definitions.value = result.items;
    } catch (error) {
      errorMessage.value = error instanceof Error ? error.message : "因子定义读取失败";
    } finally {
      isLoading.value = false;
    }
  }

  async function loadProfiles(factor: FactorDefinition) {
    const requestId = ++profileRequestId;
    activeFactor.value = factor;
    parameterValues[factor.name] ??= Object.fromEntries(
      schemaProperties(factor).map(({ key, value }) => [key, String(value.default ?? "")]),
    );
    isLoadingProfiles.value = true;
    try {
      const result = await deps.fetchFactorProfiles({ factor_name: factor.name, factor_version: factor.version });
      if (requestId !== profileRequestId) return;
      profiles.value = result.items;
    } catch (error) {
      if (requestId !== profileRequestId) return;
      profiles.value = [];
      errorMessage.value = error instanceof Error ? error.message : "因子画像读取失败";
    } finally {
      if (requestId === profileRequestId) isLoadingProfiles.value = false;
    }
  }

  return {
    backtestForm,
    isRunningBacktest,
    backtestError,
    preflight,
    preflightError,
    preflightLoading,
    selected,
    missingDirectionKeys,
    selectionPanel,
    backtestResult,
    backtestFactorSnapshot,
    backtestHoldings,
    isLoadingHoldings,
    definitions,
    activeFactor,
    profiles,
    search,
    frequency,
    category,
    isLoading,
    isLoadingProfiles,
    errorMessage,
    parameterValues,
    factorKey,
    factorCategoryOptions,
    profileOverlapsBacktest,
    icMetric,
    icPoints,
    filteredDefinitions,
    isSelected,
    toggleFactor,
    removeFactor,
    schemaProperties,
    loadDefinitions,
    loadProfiles,
    localizedName,
    localizedDescription,
    categoryLabel,
    checkBacktestReadiness,
    runBacktest,
    loadBacktestHoldings,
  };
}

export type FactorWorkspace = ReturnType<typeof createFactorWorkspace>;
