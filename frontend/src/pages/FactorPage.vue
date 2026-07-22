<script setup lang="ts">
import { computed, onMounted, reactive, ref } from "vue";
import { BarChart3, Check, Filter, Info, Trash2 } from "@lucide/vue";

import { fetchFactorDefinitions, fetchFactorProfiles } from "../api";
import { securityTypeLabel } from "../components/operations/formatters";
import type { FactorDefinition, FactorProfile } from "../types";

const definitions = ref<FactorDefinition[]>([]);
const selected = ref<FactorDefinition[]>([]);
const activeFactor = ref<FactorDefinition | null>(null);
const profiles = ref<FactorProfile[]>([]);
const search = ref("");
const frequency = ref("");
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
  roe_ttm: { name: "滚动净资产收益率", description: "最近十二个月净利润相对股东权益的比例，用于衡量权益资本回报。" },
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
  return matchesText && (!frequency.value || factor.frequency === frequency.value);
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
  return selected.value.some((item) => item.id === factor.id);
}

function toggleFactor(factor: FactorDefinition) {
  if (isSelected(factor)) {
    selected.value = selected.value.filter((item) => item.id !== factor.id);
  } else {
    selected.value = [...selected.value, factor];
    parameterValues[factor.name] ??= Object.fromEntries(
      schemaProperties(factor).map(({ key, value }) => [key, String(value.default ?? "")]),
    );
  }
  void loadProfiles(factor);
}

function removeFactor(factor: FactorDefinition) {
  selected.value = selected.value.filter((item) => item.id !== factor.id);
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
      <button class="secondary-action" type="button" @click="loadDefinitions"><Filter :size="16" />{{ isLoading ? "读取中" : "刷新定义" }}</button>
    </div>

    <p v-if="errorMessage" class="inline-error">{{ errorMessage }}</p>
    <div class="factor-layout">
      <div class="factor-catalog">
        <div v-if="!filteredDefinitions.length && !isLoading" class="empty-panel">没有符合条件的因子。</div>
        <article v-for="factor in filteredDefinitions" :key="factor.id" class="factor-card" :class="{ active: activeFactor?.id === factor.id }" @click="loadProfiles(factor)">
          <div class="factor-card-top"><div><strong>{{ localizedName(factor) }}</strong><code>{{ factor.name }} · {{ factor.version }}</code></div><button class="icon-button" type="button" :title="isSelected(factor) ? '移出选择' : '加入选择'" @click.stop="toggleFactor(factor)"><Check v-if="isSelected(factor)" :size="16" /><span v-else>+</span></button></div>
          <p>{{ localizedDescription(factor) }}</p>
          <footer><span>{{ factor.frequency === "daily" ? "日频" : factor.frequency === "weekly" ? "周频" : "月频" }}</span><span>{{ factor.applicable_security_types.map(securityTypeLabel).join("、") || "未限制证券类型" }}</span><span v-if="factor.window_days">窗口 {{ factor.window_days }} 日</span></footer>
        </article>
      </div>

      <aside class="factor-selection">
        <div class="panel-heading"><div><h2>已选择因子</h2><span>{{ selected.length }} 个因子</span></div></div>
        <div v-if="!selected.length" class="empty-panel">从左侧因子卡片加入研究组合。</div>
        <ul v-else class="selection-list"><li v-for="factor in selected" :key="factor.id"><div><strong>{{ localizedName(factor) }}</strong><code>{{ factor.name }} · {{ factor.version }}</code></div><button class="icon-button" type="button" title="移除因子" @click="removeFactor(factor)"><Trash2 :size="15" /></button></li></ul>

        <div v-if="activeFactor" class="factor-detail"><h2>{{ localizedName(activeFactor) }}</h2><p>{{ localizedDescription(activeFactor) }}</p><dl><div><dt>输入数据</dt><dd>{{ activeFactor.inputs.join("、") || "由服务端定义" }}</dd></div><div><dt>依赖</dt><dd>{{ activeFactor.dependencies.join("、") || "无" }}</dd></div><div><dt>适用类型</dt><dd>{{ activeFactor.applicable_security_types.map(securityTypeLabel).join("、") || "未限制" }}</dd></div></dl><div v-if="schemaProperties(activeFactor).length" class="parameter-list"><strong>参数配置</strong><label v-for="parameter in schemaProperties(activeFactor)" :key="parameter.key">{{ parameter.label }}<input v-model="parameterValues[activeFactor.name][parameter.key]" :type="parameter.value.type === 'number' || parameter.value.type === 'integer' ? 'number' : 'text'" :placeholder="String(parameter.value.default ?? '')" /></label></div><div class="profile-summary"><strong>最近画像</strong><span v-if="isLoadingProfiles">读取中…</span><span v-else-if="!profiles.length">暂无画像数据</span><span v-else>覆盖率 {{ (Number(profiles[profiles.length - 1].coverage_rate) * 100).toFixed(1) }}% · IC {{ profiles[profiles.length - 1].ic_1d ?? "-" }}</span></div></div>
      </aside>
    </div>
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
.factor-selection { padding: 18px; }
.selection-list { display: grid; gap: 8px; padding: 0; list-style: none; }
.selection-list li { padding: 10px 0; border-bottom: 1px solid #eef1f5; }
.icon-button { display: grid; place-items: center; width: 32px; height: 32px; border: 1px solid #d7dde6; border-radius: 6px; background: #fff; cursor: pointer; }
.factor-detail { margin-top: 18px; padding-top: 18px; border-top: 1px solid #e3e7ee; }
.factor-detail dl { display: grid; gap: 8px; }
.factor-detail dl div { display: grid; grid-template-columns: 76px 1fr; gap: 8px; font-size: 12px; }
.factor-detail dt { color: #667085; }.factor-detail dd { margin: 0; }
.parameter-list { display: grid; gap: 10px; margin-top: 18px; }
.profile-summary { display: grid; gap: 5px; margin-top: 18px; padding: 12px; border-radius: 7px; background: #f8fafc; font-size: 12px; }
.empty-panel { padding: 24px; color: #667085; text-align: center; }
@media (max-width: 900px) { .factor-page { padding: 24px 16px 48px; }.factor-layout { grid-template-columns: 1fr; } }
@media (max-width: 620px) { .page-heading, .factor-toolbar { align-items: stretch; flex-direction: column; }.factor-catalog { grid-template-columns: 1fr; } }
</style>
