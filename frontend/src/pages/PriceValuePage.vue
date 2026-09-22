<script setup lang="ts">
import { computed, onBeforeUnmount, reactive, ref, watch } from "vue";
import { evaluatePriceValue, type PriceValueRequest, type PriceValueResult } from "../priceValueApi";
import PriceValueBacktestPanel from "./components/PriceValueBacktestPanel.vue";

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
const statusLabels = { blocked: "无法评估", price_condition_met: "价格条件达标", wait_for_price: "等待价格", review_required: "需要复核" };
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
</script>

<template>
  <section class="value-research">
    <header><p class="eyebrow">价格与价值</p><h1>估值研究</h1><p>以三种现金流情景，检验当前价格是否提供所需安全边际。</p></header>
    <div class="research-notice"><strong>研究试算 · 不可交易</strong><p>现金流、股本及参数均为你提供的研究假设，未经系统核验；历史时点正确性尚未认证。报价仅取可信行情层。结果不创建候选认证、持仓或订单。</p></div>
    <form @submit.prevent="calculate">
      <fieldset><legend>证券与研究时点</legend><div class="input-grid">
        <label>证券代码<input v-model="form.symbol" required placeholder="例如 600519.SH" /></label>
        <label>报价交易日<input v-model="form.trade_date" required type="date" /><small>默认昨天；遇非交易日请修改。</small></label>
        <label>企业类型<select v-model="form.company_profile"><option value="stable_non_financial">稳定经营的非金融企业</option><option value="financial">金融企业（暂不适用）</option><option value="cyclical">周期企业（暂不适用）</option><option value="distressed">困境企业（暂不适用）</option></select></label>
        <label>稀释后普通股股数（股）<input v-model="form.diluted_shares" required inputmode="decimal" placeholder="总股数，不是万股" /></label>
        <label>估值时间（含时区）<input v-model="form.valuation_at" required /></label>
        <label>假设首次可得时间（含时区）<input v-model="form.assumptions_available_at" required /></label>
        <label>假设有效期至（含时区）<input v-model="form.valid_until" required /></label>
        <label>假设版本<input v-model="form.assumption_version" required /></label>
      </div><label>假设依据与来源说明<textarea v-model="form.assumptions_note" required rows="2" placeholder="说明预测依据、股本口径、来源及不确定性" /></label></fieldset>
      <fieldset><legend>三情景 FCFE 预测</legend><p>填写未来各年度归普通股股东的自由现金流<strong>总额（人民币元）</strong>，1–10 年，首版仅支持各年正现金流，以逗号或空格分隔，不是每股金额。经营现金流减资本支出不自动等同于 FCFE。</p><p>终值现金流为预测期之后第一年的 FCFE。比例使用小数，例如 0.12 表示 12%；折现率须高于永续增长率。</p>
        <div class="scenario-grid"><section v-for="(label, key) in scenarioLabels" :key="key" class="scenario"><h3>{{ label }}情景</h3>
          <label>年度 FCFE 总额序列（元）<textarea v-model="form.scenarios[key].cash_flows" required rows="3" placeholder="按未来年度顺序输入" /></label>
          <label>终值年度 FCFE 总额（元）<input v-model="form.scenarios[key].terminal_cash_flow" required inputmode="decimal" /></label>
          <label>股权资本成本（比例）<input v-model="form.scenarios[key].cost_of_equity" required inputmode="decimal" /></label>
          <label>永续增长率（比例）<input v-model="form.scenarios[key].terminal_growth" required inputmode="decimal" /></label>
        </section></div>
      </fieldset>
      <fieldset><legend>价格条件与费用假设</legend><p>预填费率、滑点和安全边际仅为可修改的示例，不代表真实收费或合适的投资参数。</p><div class="input-grid">
        <label>所需安全边际（比例）<input v-model="form.required_margin" required inputmode="decimal" /></label>
        <label>拟买数量（股，仅试算）<input v-model="form.quantity" required inputmode="numeric" /></label>
        <label v-for="(label, key) in feeLabels" :key="key">{{ label }}<input v-model="form.fees[key]" required inputmode="decimal" /></label>
      </div><div class="checks"><label><input v-model="form.has_position" type="checkbox" /> 当前已有持仓</label><label><input v-model="form.thesis_broken" type="checkbox" /> 原投资假设已失效</label></div></fieldset>
      <button type="submit" :disabled="pending">{{ pending ? "计算中…" : "计算研究结果" }}</button>
      <p v-if="changed" role="status">参数已修改，原结果已失效，请重新计算。</p><p v-if="error" class="research-error" role="alert">{{ error }}</p>
    </form>
    <section v-if="result" class="research-result" aria-live="polite"><div class="result-heading"><h2>{{ statusLabels[result.status] }}</h2><button type="button" @click="exportResult">导出研究 JSON</button></div><p><strong>不可交易 · 不新增配置</strong> · {{ result.existing_position_action === 'review' ? '已有持仓需复核' : '已有持仓保持不变' }}</p>
      <p v-if="result.market_snapshot">行情：{{ result.market_snapshot.symbol }} · {{ result.market_snapshot.trade_date }} 收盘 {{ amount(result.market_snapshot.close) }} 元 · 来源 {{ result.market_snapshot.source_name }} · 质量 {{ result.market_snapshot.quality_status }}</p><p v-else>没有可用于本次研究的行情快照。</p>
      <div v-if="result.valuation" class="scenario-grid"><div v-for="(label, key) in scenarioLabels" :key="key" class="value-card"><span>{{ label }}每股价值</span><strong>{{ amount(result.valuation[key]) }} 元</strong></div></div>
      <dl v-if="result.price_metrics" class="metrics"><div><dt>当前数量及费用下最高买入报价</dt><dd>{{ amount(result.price_metrics.max_buy_quote, 2) }} 元</dd></div><div><dt>含费每股买入成本上限</dt><dd>{{ amount(result.price_metrics.buy_cost_ceiling) }} 元</dd></div><div><dt>当前每股含费成本</dt><dd>{{ amount(result.price_metrics.all_in_price) }} 元</dd></div><div><dt>报价安全边际</dt><dd>{{ percentage(result.price_metrics.gross_margin) }}</dd></div><div><dt>成本后安全边际</dt><dd>{{ percentage(result.price_metrics.cost_adjusted_margin) }}</dd></div><div><dt>本次数量费用总额</dt><dd>{{ amount(result.price_metrics.fee_total) }} 元</dd></div><div><dt>滑点成本</dt><dd>{{ amount(result.price_metrics.slippage_cost) }} 元</dd></div></dl>
      <template v-for="group in reasons" :key="group.label"><section v-if="group.items.length"><h3>{{ group.label }}</h3><ul><li v-for="(reason, i) in group.items" :key="`${reason.code}-${i}`">{{ reason.message }}</li></ul></section></template>
      <details><summary>研究留痕</summary><dl><dt>计算时间</dt><dd>{{ result.evaluated_at }}</dd><dt>模型版本</dt><dd>{{ result.model_version }}</dd><dt>代码版本</dt><dd>{{ result.code_version }}</dd><dt>研究合同指纹</dt><dd>{{ result.contract_hash }}</dd></dl></details>
    </section>
    <PriceValueBacktestPanel :current-assumptions="backtestAssumptions" />
  </section>
</template>

<style scoped>
.value-research { max-width: 1200px; display: grid; gap: 20px; }
h1,h2,h3,p { margin: 0 0 12px; } .eyebrow { color: #326b58; font-weight: 700; }
.research-notice { background: #fff5db; border: 1px solid #e6cf91; padding: 18px; border-radius: 12px; }
.research-notice p { margin: 8px 0 0; } form { display: grid; gap: 20px; }
fieldset,.research-result { border: 1px solid #d8e0dd; border-radius: 14px; padding: 22px; background: white; min-width: 0; }
legend { font-size: 17px; font-weight: 700; padding: 0 8px; }
.input-grid,.scenario-grid,.metrics { display: grid; grid-template-columns: repeat(3,minmax(0,1fr)); gap: 18px; }
label { display: grid; gap: 7px; font-size: 14px; margin-bottom: 14px; } input,textarea,select { width: 100%; min-width: 0; box-sizing: border-box; padding: 10px; border: 1px solid #becbc5; border-radius: 7px; font: inherit; background: white; color: #23372f; }
textarea { resize: vertical; } small { color: #68786f; } .scenario { padding: 15px; background: #f4f7f5; border-radius: 10px; }
button { border: 0; border-radius: 8px; padding: 11px 20px; background: #24694f; color: white; font: inherit; cursor: pointer; justify-self: start; } button:disabled { opacity: .6; cursor: wait; }
.checks { display: flex; gap: 25px; flex-wrap: wrap; } .checks label { display: flex; align-items: center; } .checks input { width: auto; }
.research-error { color: #a0282a; } .result-heading { display: flex; align-items: center; justify-content: space-between; gap: 15px; }
.value-card { display: grid; gap: 12px; background: #eef5f1; border-radius: 10px; padding: 18px; } .value-card strong { font-size: 21px; overflow-wrap: anywhere; }
.metrics dt { color: #61736a; } dd { margin: 7px 0 14px; overflow-wrap: anywhere; } li { margin-bottom: 8px; } details { margin-top: 18px; } summary { cursor: pointer; }
@media(max-width: 900px) { .input-grid,.scenario-grid,.metrics { grid-template-columns: 1fr; } .result-heading { align-items: flex-start; flex-direction: column; } }
</style>
