<script setup lang="ts">
import { computed, onBeforeUnmount, reactive, ref, watch } from "vue";
import { evaluatePriceValue, type PriceValueRequest, type PriceValueResult } from "../priceValueApi";
import PriceValueBacktestPanel from "./components/PriceValueBacktestPanel.vue";
import PriceValueProfitPanel from "./components/PriceValueProfitPanel.vue";
import PriceValueScreenPanel from "./components/PriceValueScreenPanel.vue";

const activeView = ref<"screen" | "value" | "backtest">("screen");
const profitRefreshKey = ref(0);
const now = new Date();
const yesterday = new Date(now.getTime() - 86400000);
const form = reactive({
  symbol: "", trade_date: `${yesterday.getFullYear()}-${String(yesterday.getMonth() + 1).padStart(2, "0")}-${String(yesterday.getDate()).padStart(2, "0")}`,
  valuation_at: now.toISOString(), assumptions_available_at: now.toISOString(),
  valid_until: new Date(now.getTime() + 30 * 86400000).toISOString(),
  assumption_version: "research-v1", assumptions_note: "", company_profile: "stable_non_financial" as PriceValueRequest["company_profile"],
  diluted_shares: "", required_margin: "0.30", quantity: "100", has_position: false, thesis_broken: false,
  fees: { commission_rate: "0.0003", min_commission: "5", transfer_fee_rate: "0", exchange_fee_rate: "0", slippage_bps: "2" },
  scenarios: {
    low: { cash_flows: "", terminal_cash_flow: "", cost_of_equity: "0.12", terminal_growth: "0.02" },
    base: { cash_flows: "", terminal_cash_flow: "", cost_of_equity: "0.10", terminal_growth: "0.02" },
    high: { cash_flows: "", terminal_cash_flow: "", cost_of_equity: "0.08", terminal_growth: "0.02" },
  },
});
const scenarioLabels = { low: "悲观", base: "基准", high: "乐观" } as const;
const backtestAssumptions = computed<PriceValueRequest>(() => ({
  ...form, symbol: form.symbol.trim().toUpperCase(), currency: "CNY", quantity: Number(form.quantity),
  scenarios: Object.fromEntries(Object.entries(form.scenarios).map(([key, value]) => [key, {
    ...value, cash_flows: value.cash_flows.trim().split(/[\s,，;；]+/).filter(Boolean),
  }])) as PriceValueRequest["scenarios"],
}));
const feeLabels = { commission_rate: "佣金比例", min_commission: "最低佣金（元）", transfer_fee_rate: "过户费比例", exchange_fee_rate: "其他交易费比例", slippage_bps: "滑点（基点）" } as const;
const statusLabels = { blocked: "暂时无法评估", price_condition_met: "达到设定价格条件，仍需复核", wait_for_price: "价格尚未达到设定条件", review_required: "需要人工复核" };
// Formatting is display-only; request and export retain the original decimal strings.
function amount(value: string, digits = 4): string {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed.toLocaleString("zh-CN", { minimumFractionDigits: digits, maximumFractionDigits: digits }) : "—";
}
function percentage(value: string): string {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed.toLocaleString("zh-CN", { style: "percent", minimumFractionDigits: 2, maximumFractionDigits: 2 }) : "—";
}
const result = ref<PriceValueResult | null>(null);
const pending = ref(false);
const error = ref("");
const changed = ref(false);
let controller: AbortController | null = null;
let generation = 0;
watch(form, () => {
  generation += 1;
  controller?.abort();
  if (result.value || pending.value) changed.value = true;
  result.value = null; pending.value = false; error.value = "";
}, { deep: true, flush: "sync" });
onBeforeUnmount(() => { generation += 1; controller?.abort(); });
const reasons = computed(() => result.value ? [
  { label: "评估限制", items: result.value.blocking_reasons },
  { label: "复核事项", items: result.value.review_reasons },
  { label: "研究边界", items: result.value.limitations },
] : []);
function validTime(value: string): string {
  if (!/(Z|[+-]\d{2}:\d{2})$/i.test(value) || !Number.isFinite(Date.parse(value))) throw new Error("时间需使用含时区的 ISO 格式，例如 2026-09-22T13:00:00+08:00。");
  return value;
}
async function calculate() {
  controller?.abort();
  const requestId = ++generation;
  controller = new AbortController();
  const signal = controller.signal;
  error.value = ""; result.value = null; changed.value = false; pending.value = true;
  try {
    const quantity = Number(form.quantity);
    if (!Number.isSafeInteger(quantity) || quantity <= 0) throw new Error("拟买数量必须为正整数。");
    const scenarios = Object.fromEntries(Object.entries(form.scenarios).map(([key, value]) => [key, {
      ...value, cash_flows: value.cash_flows.trim().split(/[\s,，;；]+/).filter(Boolean),
    }])) as PriceValueRequest["scenarios"];
    const payload: PriceValueRequest = {
      ...form, symbol: form.symbol.trim().toUpperCase(), currency: "CNY", quantity, scenarios,
      valuation_at: validTime(form.valuation_at), assumptions_available_at: validTime(form.assumptions_available_at), valid_until: validTime(form.valid_until),
    };
    const response = await evaluatePriceValue(payload, signal);
    if (requestId === generation) result.value = response;
  } catch (cause) {
    if (requestId === generation && !signal.aborted) error.value = cause instanceof Error ? cause.message : "评估失败，请重试。";
  } finally { if (requestId === generation) pending.value = false; }
}
function exportResult() {
  if (!result.value) return;
  const url = URL.createObjectURL(new Blob([JSON.stringify(result.value, null, 2)], { type: "application/json" }));
  const anchor = document.createElement("a"); anchor.href = url; anchor.download = "price-value-research.json"; anchor.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
function revealInvalid(event: Event) {
  const details = (event.target as HTMLElement).closest("details");
  if (details) details.open = true;
}
</script>

<template>
  <section class="value-research">
    <header class="page-intro"><p class="eyebrow">价格与价值</p><h1>从找股票到验证想法</h1><p>先看自动初筛，再用自己的现金流假设研究一家公司；准备好历史版本后，可以做单股假设回测。</p><a href="#profit-summary">查看目前的收益记录 ↓</a></header>
    <div class="research-notice"><strong>这里是研究工具，不能直接交易</strong><p>初筛指标和手工现金流假设都需要核验。回测使用用户声明的历史假设，不代表真实获利，也不会创建订单。</p></div>
    <nav class="task-switch" aria-label="选择估值研究任务">
      <button type="button" :class="{ active: activeView === 'screen' }" :aria-pressed="activeView === 'screen'" @click="activeView = 'screen'"><span class="step">01 · 自动</span><strong>找待研究的股票</strong><small>看每日全市场初筛与数据状态</small></button>
      <button type="button" :class="{ active: activeView === 'value' }" :aria-pressed="activeView === 'value'" @click="activeView = 'value'"><span class="step">02 · 手工</span><strong>评估一只股票</strong><small>填入三种现金流预测，计算价格条件</small></button>
      <button type="button" :class="{ active: activeView === 'backtest' }" :aria-pressed="activeView === 'backtest'" @click="activeView = 'backtest'"><span class="step">03 · 历史研究</span><strong>查看假设历史表现</strong><small>用版本化假设运行单股回测</small></button>
    </nav>
    <div v-show="activeView === 'screen'"><PriceValueScreenPanel /></div>
    <section v-show="activeView === 'value'" class="task-panel" aria-label="单股现金流估值">
      <div class="section-intro"><p class="eyebrow">第二步 · 需要你的预测</p><h2>评估一只股票</h2><p>请先准备股票的稀释后股数，以及未来每年的股东自由现金流（FCFE）预测。系统不会替你猜测这些金额；没有可靠预测时，先看全市场初筛即可。</p></div>
    <form @submit.prevent="calculate" @invalid.capture="revealInvalid">
      <fieldset><legend>1. 选择股票，写明依据</legend><p class="field-help">金额填人民币元、股数填股；请在研究说明里写明数据来源与不确定性。</p><div class="input-grid">
        <label>证券代码<input v-model="form.symbol" required placeholder="例如 600519.SH" /></label>
        <label>报价交易日<input v-model="form.trade_date" required type="date" /><small>默认昨天；遇非交易日请修改。</small></label>
        <label>企业类型<select v-model="form.company_profile"><option value="stable_non_financial">稳定经营的非金融企业</option><option value="financial">金融企业（暂不适用）</option><option value="cyclical">周期企业（暂不适用）</option><option value="distressed">困境企业（暂不适用）</option></select></label>
        <label>稀释后普通股股数（股）<input v-model="form.diluted_shares" required inputmode="decimal" placeholder="总股数，不是万股" /></label>
      </div><label>假设依据与来源说明<textarea v-model="form.assumptions_note" required rows="2" placeholder="说明预测依据、股本口径、来源及不确定性" /></label>
      <details class="advanced"><summary>研究时间与版本（默认现在；历史模拟前请核对）</summary><p>当前时间仅用于今天的研究。若之后复制到历史模拟，请自行填写当时真实可得的时间；复制不会把今天的假设变成历史资料。</p><div class="input-grid"><label>估值时间（含时区）<input v-model="form.valuation_at" required /></label>
        <label>假设首次可得时间（含时区）<input v-model="form.assumptions_available_at" required /></label>
        <label>假设有效期至（含时区）<input v-model="form.valid_until" required /></label>
        <label>假设版本<input v-model="form.assumption_version" required /></label>
      </div></details></fieldset>
      <fieldset><legend>2. 填写三种现金流预测</legend><p class="field-help">FCFE 是可归普通股股东的自由现金流。分别考虑悲观、基准和乐观情况；每个框按年份写 1–10 个<strong>现金流总额（元）</strong>，用逗号或空格分开。首版只支持每年为正的预测，不能把“经营现金流－资本支出”直接当成 FCFE。</p><p class="field-help">“预测期后第 1 年”用于估计更远期价值。折现率与增长率采用小数，例如 0.12 表示 12%。下面的预填比率只是研究示例，需按你的依据核对。</p>
        <div class="scenario-grid"><section v-for="(label, key) in scenarioLabels" :key="key" class="scenario"><h3>{{ label }}情景</h3>
          <label>未来各年 FCFE 总额（元）<textarea v-model="form.scenarios[key].cash_flows" required rows="3" placeholder="依次填写第 1 年、第 2 年……的总额" /></label>
          <label>预测期后第 1 年 FCFE 总额（元）<input v-model="form.scenarios[key].terminal_cash_flow" required inputmode="decimal" /></label>
          <label>股权折现率（小数）<input v-model="form.scenarios[key].cost_of_equity" required inputmode="decimal" /></label>
          <label>长期增长率（小数）<input v-model="form.scenarios[key].terminal_growth" required inputmode="decimal" /></label>
        </section></div>
      </fieldset>
      <fieldset><legend>3. 设置价格条件</legend><p class="field-help">安全边际是你要求价格低于悲观情景价值的幅度，例如 0.30 表示 30%。预填的安全边际、费率和滑点都只是可修改的示例，不代表适合你的参数或真实收费。</p><div class="input-grid">
        <label>所需安全边际（小数）<input v-model="form.required_margin" required inputmode="decimal" /></label>
        <label>拟买数量（股，仅试算）<input v-model="form.quantity" required inputmode="numeric" /></label>
      </div><details class="advanced"><summary>核对交易费用（已填示例，计算前请检查）<small>佣金 {{ form.fees.commission_rate }} · 最低佣金 {{ form.fees.min_commission }} 元 · 滑点 {{ form.fees.slippage_bps }} 基点</small></summary><div class="input-grid"><label v-for="(label, key) in feeLabels" :key="key">{{ label }}<input v-model="form.fees[key]" required inputmode="decimal" /></label></div></details><div class="checks"><label><input v-model="form.has_position" type="checkbox" /> 当前已有持仓</label><label><input v-model="form.thesis_broken" type="checkbox" /> 原投资假设已失效</label></div></fieldset>
      <button type="submit" :disabled="pending">{{ pending ? "计算中…" : "计算价格条件（只读）" }}</button>
      <p v-if="changed" role="status">参数已修改，原结果已失效，请重新计算。</p><p v-if="error" class="research-error" role="alert">{{ error }}</p>
    </form>
    <section v-if="result" class="research-result" aria-live="polite"><div class="result-heading"><h2>{{ statusLabels[result.status] }}</h2><button type="button" @click="exportResult">导出研究 JSON</button></div><p><strong>不可交易 · 不新增配置</strong> · {{ result.existing_position_action === 'review' ? '已有持仓需复核' : '已有持仓保持不变' }}</p>
      <p v-if="result.market_snapshot">行情：{{ result.market_snapshot.symbol }} · {{ result.market_snapshot.trade_date }} 收盘 {{ amount(result.market_snapshot.close) }} 元 · 来源 {{ result.market_snapshot.source_name }} · 质量 {{ result.market_snapshot.quality_status }}</p><p v-else>没有可用于本次研究的行情快照。</p>
      <div v-if="result.valuation" class="scenario-grid"><div v-for="(label, key) in scenarioLabels" :key="key" class="value-card"><span>{{ label }}每股价值</span><strong>{{ amount(result.valuation[key]) }} 元</strong></div></div>
      <dl v-if="result.price_metrics" class="metrics"><div><dt>当前数量及费用下最高买入报价</dt><dd>{{ amount(result.price_metrics.max_buy_quote, 2) }} 元</dd></div><div><dt>含费每股买入成本上限</dt><dd>{{ amount(result.price_metrics.buy_cost_ceiling) }} 元</dd></div><div><dt>当前每股含费成本</dt><dd>{{ amount(result.price_metrics.all_in_price) }} 元</dd></div><div><dt>报价安全边际</dt><dd>{{ percentage(result.price_metrics.gross_margin) }}</dd></div><div><dt>成本后安全边际</dt><dd>{{ percentage(result.price_metrics.cost_adjusted_margin) }}</dd></div><div><dt>本次数量费用总额</dt><dd>{{ amount(result.price_metrics.fee_total) }} 元</dd></div><div><dt>滑点成本</dt><dd>{{ amount(result.price_metrics.slippage_cost) }} 元</dd></div></dl>
      <template v-for="group in reasons" :key="group.label"><section v-if="group.items.length"><h3>{{ group.label }}</h3><ul><li v-for="(reason, i) in group.items" :key="`${reason.code}-${i}`">{{ reason.message }}</li></ul></section></template>
      <details><summary>研究留痕</summary><dl><dt>计算时间</dt><dd>{{ result.evaluated_at }}</dd><dt>模型版本</dt><dd>{{ result.model_version }}</dd><dt>代码版本</dt><dd>{{ result.code_version }}</dd><dt>研究合同指纹</dt><dd>{{ result.contract_hash }}</dd></dl></details>
    </section>
    </section>
    <section v-show="activeView === 'backtest'" class="task-panel" aria-label="单股假设历史回测"><div class="section-intro"><p class="eyebrow">第三步 · 有历史假设才运行</p><h2>查看假设历史表现</h2><p>先在“评估一只股票”中填写现金流与来源，再复制为假设版本。请核对每个版本当时是否真的已可得；今天填写的预测不能自动当成过去已知信息。</p><button type="button" class="secondary-action" @click="activeView = 'value'">去填写单股估值假设</button></div><PriceValueBacktestPanel :current-assumptions="backtestAssumptions" @completed="profitRefreshKey++" /></section>
    <div id="profit-summary"><PriceValueProfitPanel :refresh-key="profitRefreshKey" /></div>
  </section>
</template>

<style scoped>
.value-research { max-width: 1200px; display: grid; gap: 18px; }
h1,h2,h3,p { margin: 0 0 12px; } h1 { font-size: clamp(28px, 4vw, 40px); letter-spacing: -.03em; } h2 { font-size: 24px; }
.eyebrow { margin-bottom: 7px; color: #326b58; font-size: 12px; font-weight: 800; letter-spacing: .04em; }
.page-intro p:not(.eyebrow) { max-width: 680px; color: #52645b; line-height: 1.6; }
.page-intro a { color: #24694f; font-size: 14px; font-weight: 700; }
.research-notice { border: 1px solid #e6cf91; border-radius: 12px; padding: 14px 18px; background: #fff8e6; }
.research-notice p { margin: 4px 0 0; color: #655734; font-size: 13px; line-height: 1.55; }
.task-switch { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 11px; }
.task-switch button { display: grid; justify-self: stretch; justify-items: start; gap: 5px; width: 100%; min-height: 112px; padding: 15px 16px; border: 1px solid #d8e0dd; border-radius: 12px; background: #fff; color: #223c2c; text-align: left; cursor: pointer; }
.task-switch button.active { border-color: #287353; box-shadow: inset 0 0 0 1px #287353; background: #edf6ef; }
.task-switch button:hover { border-color: #287353; }
.task-switch .step { color: #397457; font-size: 12px; font-weight: 800; }
.task-switch strong { font-size: 16px; } .task-switch small { color: #596b61; font-size: 12px; line-height: 1.45; }
.task-panel { display: grid; gap: 18px; min-width: 0; } .section-intro { border: 1px solid #d8e0dd; border-radius: 14px; padding: 20px 22px; background: #f4f8f6; }
.section-intro p:not(.eyebrow) { max-width: 760px; color: #52645b; line-height: 1.6; }
.secondary-action { border: 1px solid #b5cec0; background: white; color: #24694f; }
form { display: grid; gap: 18px; }
fieldset,.research-result { min-width: 0; border: 1px solid #d8e0dd; border-radius: 14px; padding: 22px; background: white; }
legend { padding: 0 8px; font-size: 17px; font-weight: 700; }
.field-help { color: #53675a; font-size: 13px; line-height: 1.6; }
.input-grid,.scenario-grid,.metrics { display: grid; grid-template-columns: repeat(3,minmax(0,1fr)); gap: 16px; }
label { display: grid; gap: 7px; margin-bottom: 14px; font-size: 14px; }
input,textarea,select { width: 100%; min-width: 0; box-sizing: border-box; padding: 10px; border: 1px solid #becbc5; border-radius: 7px; background: white; color: #23372f; font: inherit; }
textarea { resize: vertical; } small { color: #68786f; } .scenario { padding: 15px; border-radius: 10px; background: #f4f7f5; }
.scenario h3 { padding-bottom: 8px; border-bottom: 1px solid #dce7de; }
button { justify-self: start; border: 0; border-radius: 8px; padding: 11px 20px; background: #24694f; color: white; font: inherit; cursor: pointer; }
button:disabled { opacity: .6; cursor: wait; } button:focus-visible, a:focus-visible, summary:focus-visible, input:focus-visible, textarea:focus-visible, select:focus-visible { outline: 3px solid #87bda0; outline-offset: 2px; }
.advanced { margin-top: 12px; padding: 12px 15px; border: 1px solid #dce7de; border-radius: 9px; background: #f8faf8; }
.advanced summary { color: #265a3f; font-weight: 700; cursor: pointer; } .advanced summary small { display: block; margin: 5px 0 0 16px; color: #61736a; font-weight: 400; } .advanced p { margin: 12px 0; color: #52645b; font-size: 13px; line-height: 1.6; } .advanced .input-grid { margin-top: 15px; }
.checks { display: flex; gap: 25px; flex-wrap: wrap; margin-top: 14px; } .checks label { display: flex; align-items: center; } .checks input { width: auto; }
.research-error { color: #a0282a; } .result-heading { display: flex; align-items: center; justify-content: space-between; gap: 15px; }
.value-card { display: grid; gap: 12px; border-radius: 10px; padding: 18px; background: #eef5f1; } .value-card strong { overflow-wrap: anywhere; font-size: 21px; }
.metrics dt { color: #61736a; } dd { margin: 7px 0 14px; overflow-wrap: anywhere; } li { margin-bottom: 8px; } details { margin-top: 18px; } summary { cursor: pointer; }
#profit-summary { scroll-margin-top: 110px; }
@media(max-width: 900px) { .input-grid,.scenario-grid,.metrics { grid-template-columns: 1fr; } .result-heading { align-items: flex-start; flex-direction: column; } }
@media(max-width: 640px) { .task-switch { grid-template-columns: 1fr; gap: 8px; } .task-switch button { min-height: 0; } fieldset,.research-result { padding: 18px; } }
</style>
