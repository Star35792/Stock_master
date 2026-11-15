<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from "vue";
import { BarChart3, Check, Filter, Info, Play, Trash2 } from "@lucide/vue";

import { fetchFactorDefinitions, fetchFactorProfiles } from "../api";
import { securityTypeLabel } from "../components/operations/formatters";
import type { FactorDefinition, FactorProfile } from "../types";
import {
  fetchBacktestFinalHoldingsTyped,
  preflightFactorSelectionBacktest,
  runFactorSelectionBacktest,
} from "../factorSelectionApi";
import type { FactorScoreSpecInput, FactorSelectionBacktestRequest, FactorSelectionPreflight } from "../factorSelectionApi";

type FactorDirection = "unconfigured" | "higher" | "lower";

interface SelectedFactorConfig {
  factor: FactorDefinition;
  direction: FactorDirection;
}

interface FactorDirectionSnapshot {
  factor_name: string;
  factor_version: string;
  display_name: string;
  weight: "1";
  higher_is_better: boolean;
  direction_source: "manual";
}

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
    preflight.value = await preflightFactorSelectionBacktest(
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
    const result = await runFactorSelectionBacktest(
      createBacktestPayload(factorSpecs), controller.signal,
    );
    if (requestId !== backtestRequestId) return;
    backtestResult.value = result;
    backtestFactorSnapshot.value = factorSnapshot;
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
    const holdings = await fetchBacktestFinalHoldingsTyped(runId, controller.signal);
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

const factorCategoryLabels: Record<string, string> = {
  price_volume_risk: "价量与风险",
  financial_health: "财务健康",
  fundamental_quality: "基本面质量与成长",
  valuation: "估值与安全边际",
  industry_prosperity: "行业景气",
  sentiment: "市场情绪",
  macro: "宏观与信用",
  policy: "政策与监管",
  earnings_revision: "盈利预期修正",
  capital_flow: "资金结构与拥挤度",
  composite: "复合决策",
};

const factorCategoryOptions = [
  { value: "", label: "全部分类" },
  ...Object.entries(factorCategoryLabels).map(([value, label]) => ({ value, label })),
];

function categoryLabel(factor: FactorDefinition) {
  return factorCategoryLabels[factor.category] ?? factor.category ?? "未分类";
}

const isLoading = ref(false);
const isLoadingProfiles = ref(false);
const errorMessage = ref<string | null>(null);
const parameterValues = reactive<Record<string, Record<string, string | number>>>({});
let profileRequestId = 0;

const parameterLabels: Record<string, string> = {
  window: "计算窗口",
  window_days: "窗口天数",
  lag: "滞后期",
  quantile: "分位数",
  winsorize: "缩尾比例",
  sentiment_history_days: "情绪历史窗口（天）",
};

const factorCopy: Record<string, { name: string; description: string }> = {
  debt_to_assets: { name: "资产负债率", description: "最近一期总负债除以总资产，用于衡量企业杠杆水平与偿债压力。" },
  ma_gap_5: { name: "5 日均线偏离", description: "收盘价相对近 5 个交易日平均收盘价的偏离程度，用于观察短期趋势。" },
  momentum_5: { name: "5 日动量", description: "当前收盘价相对 5 个交易日前收盘价的变化，用于衡量短期价格动量。" },
  ret_20d: { name: "20 日收益率", description: "衡量标的近 20 个交易日的价格变化，用于观察短中期动量。" },
  ret_1d: { name: "1 日收益率", description: "当前可信收盘价相对前一可信交易日收盘价的变化。" },
  market_valuation_pe_ttm: { name: "滚动市盈率", description: "比较当前价格与过去十二个月盈利，数值越低通常代表估值更低。" },
  net_profit_growth_yoy: { name: "净利润同比增长", description: "衡量公司净利润相对上年同期的增长幅度。" },
  ocf_to_net_income_ttm: { name: "经营现金流净利润比", description: "最近报告期经营活动现金流相对净利润的比例，用于观察盈利的现金含量。" },
  revenue_growth_yoy: { name: "营业收入同比增长", description: "最近报告期营业收入相对上年同期的变化，用于衡量企业成长速度。" },
  roe_ttm: { name: "单期 ROE 代理", description: "最近报告单期净利润除以期末股东权益；字段名沿用 roe_ttm，不代表滚动十二个月。" },
  turnover_rate: { name: "换手率", description: "成交量相对可交易流通规模的比例，用于观察交易活跃度。" },
  volatility_5: { name: "5 日波动率", description: "最近 5 个日收益率的总体标准差，用于衡量短期价格波动。" },
  volume_chg_1d: { name: "1 日成交量变化", description: "当前成交量相对上一可信交易日成交量的变化。" },
};

const factorNameLabels: Record<string, string> = {
  turnover_percentile_120: "120 日换手率分位数", momentum_20: "20 日动量", momentum_60: "60 日动量",
  volatility_20: "20 日波动率", downside_volatility_20: "20 日下行波动率", max_drawdown_20: "20 日最大回撤",
  liquidity_20: "20 日平均成交额", volume_stability_20: "20 日量能稳定性", abnormal_volume_pressure: "异常成交量压力",
  reversal_5: "5 日反转", beta_60: "60 日 Beta", residual_volatility_60: "60 日残差波动率",
  main_fund_net_inflow: "主力资金净流入", main_fund_net_inflow_ratio: "主力资金净流入率", main_fund_rank_score: "主力资金排名得分",
  main_fund_inflow_change_5: "主力资金 5 日流入变化", main_fund_inflow_percentile_20: "主力资金 20 日流入分位数",
  northbound_flow_ratio: "北向资金流入率", margin_financing_change_20: "20 日融资余额变化", etf_flow_industry_20: "20 日行业 ETF 资金流",
  institutional_crowding_score: "机构拥挤度得分", crowding_score: "市场拥挤度得分", decision_score: "综合决策得分",
  rate_trend_10y: "10 年期利率趋势", yield_curve_slope: "收益率曲线斜率", social_financing_impulse: "社融脉冲",
  m1_m2_gap: "M1-M2 剪刀差", usd_cnh_trend: "美元兑离岸人民币趋势", commodity_inflation_proxy: "商品通胀代理",
  credit_spread_change: "信用利差变化", earnings_revision_1m: "1 个月盈利预测修正", earnings_revision_breadth: "盈利预测修正广度",
  forecast_dispersion: "盈利预测分歧", guidance_surprise: "业绩指引超预期", profit_warning_risk: "业绩预警风险",
  current_ratio: "流动比率", interest_coverage: "利息保障倍数", operating_cash_debt: "经营现金流负债比",
  roic_ttm: "滚动投入资本回报率", roa_ttm: "滚动总资产回报率", gross_margin_ttm: "滚动毛利率",
  operating_margin_ttm: "滚动营业利润率", accrual_ratio: "应计利润比率", ocf_growth_yoy: "经营现金流同比增长",
  roe_stability_3y: "3 年净资产收益率稳定性", revenue_cagr_3y: "3 年营业收入复合增长", net_profit_cagr_3y: "3 年净利润复合增长",
  receivable_growth_revenue_gap: "应收账款与收入增速差", inventory_growth_revenue_gap: "存货与收入增速差", financial_anomaly_score: "财务异常得分",
  industry_momentum_60: "60 日行业动量", industry_breadth_20: "20 日行业上涨广度", industry_turnover_share_change_20: "20 日行业成交占比变化",
  hot_rank_score: "热度排名得分", hot_heat: "市场热度", hot_rank_change: "热度排名变化", limit_up_strength: "涨停强度",
  dragon_tiger_net_buy_ratio: "龙虎榜净买入率", limit_up_breadth: "涨停广度", limit_up_failure_rate: "涨停开板率",
  consecutive_limit_up_height: "连板高度", market_sentiment_score: "市场情绪得分", sentiment_temperature_percentile: "情绪温度分位数",
  overheat_reversal_signal: "过热反转信号", policy_event_score: "政策事件得分", market_cap: "总市值",
  earnings_yield: "盈利收益率", book_to_price: "账面市值比", sales_to_price: "销售收入市值比", fcf_yield: "自由现金流收益率",
  ev_to_ebitda: "企业价值倍数", industry_valuation_gap: "行业估值偏离",
};

const filteredDefinitions = computed(() => definitions.value.filter((factor) => {
  const query = search.value.trim().toLowerCase();
  const matchesText = !query || [factor.name, factor.display_name ?? "", localizedName(factor)].some((value) => value.toLowerCase().includes(query));
  return matchesText && (!frequency.value || factor.frequency === frequency.value) && (!category.value || factor.category === category.value);
}));

function localizedName(factor: FactorDefinition) {
  const transformed = localizedTransform(factor.name);
  if (transformed) return transformed.name;
  return factorCopy[factor.name]?.name ?? factorNameLabels[factor.name] ?? factor.display_name ?? factor.name;
}

function localizedDescription(factor: FactorDefinition) {
  const transformed = localizedTransform(factor.name);
  if (transformed) return transformed.description;
  if (factorCopy[factor.name]?.description) return factorCopy[factor.name].description;
  if (factor.description && /[\u3400-\u9fff]/.test(factor.description)) return factor.description;
  return `${localizedName(factor)}反映相关财务、市场或资金变化，可用于横截面比较与组合筛选。请结合输入数据、计算窗口和最近画像理解该指标。`;
}

function localizedTransform(name: string) {
  const match = name.match(/^(.*)__(industry_neutral|rank|winsor|zscore)$/);
  if (!match) return null;
  const base = factorCopy[match[1]]?.name ?? factorNameLabels[match[1]] ?? match[1];
  const labels: Record<string, { suffix: string; description: string }> = {
    industry_neutral: { suffix: "行业中性化", description: "在行业分组内调整原始因子，降低行业暴露对横截面比较的影响。" },
    rank: { suffix: "排名转换", description: "把原始因子转换为横截面排名，降低极端数值对比较的影响。" },
    winsor: { suffix: "缩尾处理", description: "对原始因子的极端值进行缩尾处理，提高统计稳定性。" },
    zscore: { suffix: "标准化", description: "将原始因子转换为均值为零、标准差为一的标准分数。" },
  };
  const copy = labels[match[2]];
  return { name: `${base}（${copy.suffix}）`, description: copy.description };
}

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
    result.push({ key: `weight:${name}`, label: `${factorCopy[name]?.name ?? factorNameLabels[name] ?? name}权重`, value: { type: "number", default: item.weight ?? "" } });
  }
  for (const [key, value] of Object.entries(schema)) {
    if (key !== "components" && ["string", "number", "boolean"].includes(typeof value)) {
      result.push({ key, label: parameterLabels[key] ?? key, value: { type: typeof value === "number" ? "number" : "string", default: value } });
    }
  }
  return result;
}

async function loadDefinitions() {
  isLoading.value = true;
  errorMessage.value = null;
  try {
    const result = await fetchFactorDefinitions({ is_active: true });
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
    const result = await fetchFactorProfiles({ factor_name: factor.name, factor_version: factor.version });
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

onMounted(loadDefinitions);
</script>

<template>
  <section class="page-view factor-page">
    <header class="page-heading">
      <div>
        <p class="eyebrow">因子研究</p>
        <h1>因子工作区</h1>
        <p class="page-subtitle">浏览因子定义、理解数据含义，并把需要的因子加入研究组合。</p>
      </div>
      <span class="readonly-pill"><Info :size="15" />当前只选择研究因子，不会自动交易</span>
    </header>

    <div class="factor-toolbar">
      <label class="search-field">搜索因子<input v-model="search" placeholder="代码、中文名称或描述" /></label>
      <label>频率<select v-model="frequency"><option value="">全部</option><option value="daily">日频</option><option value="weekly">周频</option><option value="monthly">月频</option></select></label>
      <label>分类<select v-model="category"><option v-for="opt in factorCategoryOptions" :key="opt.value" :value="opt.value">{{ opt.label }}</option></select></label>
      <button class="secondary-action" type="button" @click="loadDefinitions"><Filter :size="16" />{{ isLoading ? "读取中" : "刷新定义" }}</button>
    </div>

    <p v-if="errorMessage" class="inline-error">{{ errorMessage }}</p>
    <div class="factor-layout">
      <div class="factor-catalog">
        <div v-if="!filteredDefinitions.length && !isLoading" class="empty-panel">没有符合条件的因子。</div>
        <article v-for="factor in filteredDefinitions" :key="factor.id" class="factor-card" :class="{ active: activeFactor?.id === factor.id }" @click="loadProfiles(factor)">
          <div class="factor-card-top"><div><strong>{{ localizedName(factor) }}</strong><code>{{ factor.name }} · {{ factor.version }}</code></div><button class="icon-button" type="button" :title="isSelected(factor) ? '移出选择' : '加入选择'" @click.stop="toggleFactor(factor)"><Check v-if="isSelected(factor)" :size="16" /><span v-else>+</span></button></div>
          <p>{{ localizedDescription(factor) }}</p>
          <footer><span class="category-tag">{{ categoryLabel(factor) }}</span><span>{{ factor.frequency === "daily" ? "日频" : factor.frequency === "weekly" ? "周频" : "月频" }}</span><span>{{ factor.applicable_security_types.map(securityTypeLabel).join("、") || "未限制证券类型" }}</span><span v-if="factor.window_days">窗口 {{ factor.window_days }} 日</span></footer>
        </article>
      </div>

      <aside ref="selectionPanel" class="factor-selection">
        <div class="panel-heading"><div><h2>已选择因子</h2><span>{{ selected.length }} 个因子</span></div></div>
        <div v-if="!selected.length" class="empty-panel">从左侧因子卡片加入研究组合。</div>
        <template v-else>
          <p class="direction-guidance">方向是本次回测预先设定的研究假设，决定横截面排序；请勿根据本次回测结果反向挑选。</p>
          <ul class="selection-list">
            <li
              v-for="item in selected"
              :key="factorKey(item.factor)"
              :class="{ 'direction-missing': missingDirectionKeys.includes(factorKey(item.factor)) }"
            >
              <div class="selected-factor-copy">
                <strong>{{ localizedName(item.factor) }}</strong>
                <code>{{ item.factor.name }} · {{ item.factor.version }}</code>
              </div>
              <label class="direction-field">
                <span>排序方向</span>
                <select
                  v-model="item.direction"
                  class="direction-select"
                  :aria-invalid="missingDirectionKeys.includes(factorKey(item.factor))"
                  :aria-label="`${localizedName(item.factor)}排序方向`"
                >
                  <option value="unconfigured" disabled>请选择方向</option>
                  <option value="higher">数值越高越优先</option>
                  <option value="lower">数值越低越优先</option>
                </select>
              </label>
              <button class="icon-button" type="button" title="移除因子" @click="removeFactor(item.factor)"><Trash2 :size="15" /></button>
            </li>
          </ul>
        </template>

        <div v-if="activeFactor" class="factor-detail"><h2>{{ localizedName(activeFactor) }}</h2><p>{{ localizedDescription(activeFactor) }}</p><dl><div><dt>输入数据</dt><dd>{{ activeFactor.inputs.join("、") || "由服务端定义" }}</dd></div><div><dt>依赖</dt><dd>{{ activeFactor.dependencies.join("、") || "无" }}</dd></div><div><dt>适用类型</dt><dd>{{ activeFactor.applicable_security_types.map(securityTypeLabel).join("、") || "未限制" }}</dd></div></dl><div v-if="schemaProperties(activeFactor).length" class="parameter-list"><strong>参数配置</strong><label v-for="parameter in schemaProperties(activeFactor)" :key="parameter.key">{{ parameter.label }}<input v-model="parameterValues[activeFactor.name][parameter.key]" :type="parameter.value.type === 'number' || parameter.value.type === 'integer' ? 'number' : 'text'" :placeholder="String(parameter.value.default ?? '')" /></label></div><div class="profile-summary"><strong>最近画像</strong><span v-if="isLoadingProfiles">读取中…</span><span v-else-if="!profiles.length">暂无画像数据</span><template v-else><span>截至 {{ profiles[profiles.length - 1].trade_date }} · 覆盖率 {{ (Number(profiles[profiles.length - 1].coverage_rate) * 100).toFixed(1) }}% · IC {{ profiles[profiles.length - 1].ic_1d ?? "-" }}</span><span class="profile-caveat">仅用于诊断，不会自动设置方向；可能包含所选回测期间或其后的信息。</span><span v-if="profileOverlapsBacktest(profiles[profiles.length - 1])" class="profile-leakage-warning" role="alert">该画像日期与测试期重叠或更晚，存在信息泄漏风险，请勿据此选择方向。</span></template></div></div>
      </aside>
    </div>

    <section class="backtest-launcher" aria-label="因子选股回测">
      <div class="panel-heading">
        <div>
          <h2><Play :size="16" />因子选股回测</h2>
          <span>用上方选中的因子发起研究回测（T+1 执行、A 股真实成本、只读研究）</span>
        </div>
      </div>
      <form class="backtest-form" @submit.prevent="runBacktest">
        <label>回测名称<input v-model="backtestForm.name" required maxlength="100" /></label>
        <label>开始日期<input v-model="backtestForm.start_date" type="date" required /></label>
        <label>结束日期<input v-model="backtestForm.end_date" type="date" required /></label>
        <label>初始资金<input v-model="backtestForm.initial_cash" inputmode="decimal" required /></label>
        <label>持仓数量<input v-model="backtestForm.top_n" inputmode="numeric" required /></label>
        <label>调仓规则
          <select v-model="backtestForm.rebalance_rule">
            <option value="daily">每日</option>
            <option value="weekly">每周（最后交易日）</option>
            <option value="monthly">每月（最后交易日）</option>
            <option value="every_n_trading_days">每 N 个交易日</option>
          </select>
        </label>
        <label v-if="backtestForm.rebalance_rule === 'every_n_trading_days'">
          调仓间隔（交易日）<input v-model="backtestForm.rebalance_interval" inputmode="numeric" />
        </label>
        <label>基准（可选）<input v-model="backtestForm.benchmark" placeholder="如 000300.SH" /></label>
      </form>
      <div class="constraint-row">
        <label class="constraint-toggle">
          <input v-model="backtestForm.useConstraints" type="checkbox" />
          启用组合约束（与模拟盘同源：单标 / 行业 / 现金留存）
        </label>
        <template v-if="backtestForm.useConstraints">
          <label>单标的上限<input v-model="backtestForm.max_symbol_weight" inputmode="decimal" /></label>
          <label>行业上限<input v-model="backtestForm.max_industry_weight" inputmode="decimal" /></label>
          <label>现金留存率<input v-model="backtestForm.cash_reserve_rate" inputmode="decimal" /></label>
        </template>
      </div>
      <div class="preflight-panel">
        <button class="secondary-action" type="button" :disabled="preflightLoading || isRunningBacktest" @click="checkBacktestReadiness">
          {{ preflightLoading ? "正在检查…" : "运行前检查" }}
        </button>
        <p v-if="preflightError" role="alert" class="inline-error">{{ preflightError }}</p>
        <template v-if="preflight">
          <strong>{{ preflight.runnable_now ? "当前输入可尝试运行" : "发现运行阻断" }}</strong>
          <p>检查于 {{ preflight.checked_at }}；正式运行会重新读取并校验数据。{{ preflight.evidence_qualified ? "证据已认证" : "此检查不授予收益认证" }}。</p>
          <ul><li v-for="item in preflight.checks" :key="item.code"><b>{{ item.status === "passed" ? "通过" : item.status === "blocked" ? "阻断" : "限制" }}</b> · {{ item.detail }}<span v-if="item.next_action">；下一步：{{ item.next_action }}</span></li></ul>
        </template>
      </div>
      <p v-if="backtestError" class="inline-error" role="alert">{{ backtestError }}</p>
      <button class="primary-action backtest-run" type="button" :disabled="isRunningBacktest" @click="runBacktest">
        <Play :size="16" />{{ isRunningBacktest ? "回测运行中…（约需数十秒）" : "发起回测" }}
      </button>

      <div v-if="backtestResult" class="backtest-result">
        <div class="result-metrics">
          <div><span>run ID</span><strong>#{{ backtestResult.run_id }}</strong></div>
          <div><span>净收益</span><strong>{{ (Number(backtestResult.total_return) * 100).toFixed(2) }}%</strong></div>
          <div><span>期末权益</span><strong>{{ Number(backtestResult.final_equity).toLocaleString("zh-CN", { maximumFractionDigits: 0 }) }}</strong></div>
          <div><span>成交笔数</span><strong>{{ backtestResult.trade_count }}</strong></div>
        </div>
        <div class="result-assumptions">
          <strong>本次提交的因子方向</strong>
          <ul>
            <li v-for="factor in backtestFactorSnapshot" :key="`${factor.factor_name}@${factor.factor_version}`">
              <span>{{ factor.display_name }}</span>
              <code>{{ factor.factor_name }} · {{ factor.factor_version }}</code>
              <b>{{ factor.higher_is_better ? "↑ 高值优先" : "↓ 低值优先" }}</b>
            </li>
          </ul>
          <p>方向来源：人工预设；因子等权。当前为探索性单次回测（validation_mode=none），未经过训练窗自动选参或样本外验证，不构成收益认证。</p>
        </div>
        <div class="result-actions">
          <RouterLink :to="`/backtests/runs/${backtestResult.run_id}`" class="secondary-action">查看完整回测详情</RouterLink>
          <button class="secondary-action" type="button" :disabled="isLoadingHoldings" @click="loadBacktestHoldings">
            {{ isLoadingHoldings ? "读取中…" : "查看最终持仓" }}
          </button>
          <span class="result-hint">可在「组合草案」页用 run ID {{ backtestResult.run_id }} 一键预填方案。</span>
        </div>
        <div v-if="backtestHoldings && backtestHoldings.holdings.length" class="table-panel full">
          <table>
            <thead><tr><th>代码</th><th>名称</th><th>行业</th><th>数量</th><th>收盘价</th><th>市值</th><th>权重</th></tr></thead>
            <tbody>
              <tr v-for="row in backtestHoldings.holdings" :key="row.symbol">
                <td>{{ row.symbol }}</td>
                <td>{{ row.name ?? "-" }}</td>
                <td>{{ row.industry ?? "-" }}</td>
                <td>{{ row.quantity }}</td>
                <td>{{ Number(row.close).toFixed(2) }}</td>
                <td>{{ Number(row.market_value).toLocaleString("zh-CN", { maximumFractionDigits: 0 }) }}</td>
                <td>{{ (Number(row.weight) * 100).toFixed(2) }}%</td>
              </tr>
            </tbody>
          </table>
        </div>
        <p v-else-if="backtestHoldings && !backtestHoldings.holdings.length" class="empty-panel">该回测最末日没有持仓快照。</p>
      </div>
    </section>
  </section>
</template>

<style scoped>
.factor-page { max-width: 1240px; margin: 0 auto; padding: 30px 28px 72px; }
.page-heading, .factor-toolbar, .factor-card-top, .factor-card footer, .selection-list li { display: flex; align-items: center; }
.page-heading { justify-content: space-between; gap: 24px; margin-bottom: 20px; }
.page-heading h1 { margin: 5px 0; font-size: clamp(28px, 4vw, 40px); }
.eyebrow { margin: 0; color: #2563eb; font-size: 12px; font-weight: 800; letter-spacing: .08em; text-transform: uppercase; }
.page-subtitle { margin: 0; color: #667085; font-size: 14px; }
.factor-toolbar { align-items: end; gap: 12px; padding: 16px; border: 1px solid #e3e7ee; border-radius: 8px; background: #fff; }
.factor-toolbar label, .parameter-list label { display: grid; gap: 6px; color: #667085; font-size: 12px; font-weight: 700; }
.search-field { flex: 1; }
input, select { min-height: 40px; border: 1px solid #cbd5e1; border-radius: 6px; padding: 0 10px; background: #fff; }
.factor-layout { display: grid; grid-template-columns: minmax(0, 1.55fr) minmax(300px, .8fr); gap: 16px; margin-top: 16px; align-items: start; }
.factor-catalog { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }
.factor-card, .factor-selection { border: 1px solid #e3e7ee; border-radius: 8px; background: #fff; }
.factor-card { padding: 16px; cursor: pointer; }
.factor-card.active { border-color: #7aa7ef; box-shadow: inset 0 0 0 1px #7aa7ef; }
.factor-card-top, .factor-card footer, .selection-list li { justify-content: space-between; gap: 10px; }
.factor-card code, .selection-list code { display: block; margin-top: 4px; color: #667085; font-size: 11px; }
.factor-card p, .factor-detail p { color: #667085; font-size: 13px; line-height: 1.55; }
.factor-card footer { justify-content: flex-start; flex-wrap: wrap; color: #667085; font-size: 11px; }
.category-tag { color: #2563eb; font-weight: 700; }
.factor-selection { padding: 18px; }
.selection-list { display: grid; gap: 8px; padding: 0; list-style: none; }
.selection-list li { padding: 10px 0; border-bottom: 1px solid #eef1f5; }
.selection-list li.direction-missing { margin: 0 -8px; padding: 10px 8px; border: 1px solid #f2b8b5; border-radius: 7px; background: #fff7f6; }
.selected-factor-copy { flex: 1; min-width: 150px; }
.direction-guidance { margin: 12px 0 4px; color: #667085; font-size: 12px; line-height: 1.55; }
.direction-field { display: grid; gap: 4px; color: #667085; font-size: 11px; font-weight: 700; }
.direction-field select { min-width: 150px; min-height: 34px; }
.direction-field select[aria-invalid="true"] { border-color: #c43d3d; }
.icon-button { display: grid; place-items: center; width: 32px; height: 32px; border: 1px solid #d7dde6; border-radius: 6px; background: #fff; cursor: pointer; }
.factor-detail { margin-top: 18px; padding-top: 18px; border-top: 1px solid #e3e7ee; }
.factor-detail dl { display: grid; gap: 8px; }
.factor-detail dl div { display: grid; grid-template-columns: 76px 1fr; gap: 8px; font-size: 12px; }
.factor-detail dt { color: #667085; }.factor-detail dd { margin: 0; }
.parameter-list { display: grid; gap: 10px; margin-top: 18px; }
.profile-summary { display: grid; gap: 5px; margin-top: 18px; padding: 12px; border-radius: 7px; background: #f8fafc; font-size: 12px; }
.profile-caveat { color: #667085; line-height: 1.55; }
.profile-leakage-warning { color: #b42318; font-weight: 700; line-height: 1.55; }
.empty-panel { padding: 24px; color: #667085; text-align: center; }
@media (max-width: 900px) { .factor-page { padding: 24px 16px 48px; }.factor-layout { grid-template-columns: 1fr; } }
@media (max-width: 620px) { .page-heading, .factor-toolbar { align-items: stretch; flex-direction: column; }.factor-catalog { grid-template-columns: 1fr; }.selection-list li { align-items: stretch; flex-wrap: wrap; }.direction-field { width: 100%; }.direction-field select { width: 100%; } }
</style>
<style scoped>
.backtest-launcher {
  margin-top: 20px;
  border: 1px solid #e3e7ee;
  border-radius: 8px;
  padding: 18px;
  background: #fff;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.panel-heading h2 {
  display: flex;
  align-items: center;
  gap: 6px;
  margin: 0 0 2px;
  font-size: 16px;
}

.panel-heading span {
  color: #667085;
  font-size: 12px;
}

.backtest-form {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(170px, 1fr));
  gap: 10px;
}

.backtest-form label,
.constraint-row label {
  display: grid;
  gap: 5px;
  color: #667085;
  font-size: 12px;
  font-weight: 700;
}

.constraint-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 14px;
}

.constraint-toggle {
  display: flex !important;
  align-items: center;
  gap: 6px;
  grid-auto-flow: column;
}

.constraint-row label:not(.constraint-toggle) input {
  width: 110px;
}

.backtest-run {
  align-self: flex-start;
}

.result-metrics {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
  gap: 10px;
}

.result-metrics > div {
  border: 1px solid #eef1f5;
  border-radius: 8px;
  padding: 8px 12px;
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.result-metrics span {
  font-size: 11px;
  color: #667085;
}

.result-metrics strong {
  font-size: 15px;
}

.result-actions {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}

.result-assumptions {
  display: grid;
  gap: 8px;
  border: 1px solid #dbe5f3;
  border-radius: 8px;
  padding: 12px;
  background: #f8fbff;
}

.result-assumptions > strong {
  font-size: 13px;
}

.result-assumptions ul {
  display: grid;
  gap: 6px;
  margin: 0;
  padding: 0;
  list-style: none;
}

.result-assumptions li {
  display: grid;
  grid-template-columns: minmax(140px, 1fr) minmax(150px, 1fr) auto;
  align-items: center;
  gap: 10px;
  font-size: 12px;
}

.result-assumptions code,
.result-assumptions p {
  color: #667085;
  font-size: 11px;
}

.result-assumptions p {
  margin: 0;
  line-height: 1.55;
}

@media (max-width: 620px) {
  .result-assumptions li { grid-template-columns: 1fr; gap: 2px; }
}

.result-hint {
  color: #667085;
  font-size: 12px;
}
</style>
