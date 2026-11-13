<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { useRoute } from "vue-router";
import { fetchBacktestFinalHoldingsTyped } from "../factorSelectionApi";

type Run = { id: number; name: string; strategy_name: string; status: string; error_message: string | null; start_date: string; end_date: string; initial_cash: string; final_equity: string | null; total_return: string | null; parameters: Record<string, unknown>; code_version: string; data_version: string };
type Equity = { trade_date: string; cash: string; market_value: string; total_equity: string };
type Trade = { id: number; trade_date: string; signal_date: string; symbol: string; side: string; quantity: number; reference_price: string; fill_price: string; commission: string };
type Performance = { annualized_return: string; maximum_drawdown: string; sharpe_ratio: string | null; turnover_rate: string; total_commission: string; symbol_contributions: Record<string, unknown>; industry_exposure: Record<string, unknown>; style_exposure: Record<string, unknown> };
type Benchmark = { benchmark: string; strategy_total_return: string; benchmark_total_return: string; excess_return: string; start_date: string; end_date: string };
type Page<T> = { items: T[]; total: number; limit: number; offset: number };

const route = useRoute();
const runId = computed(() => Number(route.params.runId));
const run = ref<Run | null>(null);
const equities = ref<Equity[]>([]);
const trades = ref<Trade[]>([]);
const tradeTotal = ref(0);
const tradeOffset = ref(0);
const performance = ref<Performance | null>(null);
const performanceNote = ref("");
const benchmark = ref<Benchmark | null>(null);
const benchmarkNote = ref("");
const holdings = ref<Awaited<ReturnType<typeof fetchBacktestFinalHoldingsTyped>> | null>(null);
const holdingsNote = ref("");
const loading = ref(false);
const tradeLoading = ref(false);
const tradeError = ref("");
const error = ref("");
const equityNote = ref("");
let controller: AbortController | null = null;

async function getJson<T>(path: string, signal: AbortSignal): Promise<T> {
  const response = await fetch(path, { headers: { Accept: "application/json" }, signal });
  if (!response.ok) {
    const body = await response.json().catch(() => null) as { detail?: string } | null;
    throw new Error(body?.detail || `读取失败（HTTP ${response.status}）`);
  }
  return response.json() as Promise<T>;
}

function numberText(value: string | number | null | undefined, digits = 2): string {
  if (value === null || value === undefined) return "暂无";
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed.toLocaleString("zh-CN", { maximumFractionDigits: digits }) : "暂无";
}
function percent(value: string | null | undefined): string {
  return value === null || value === undefined ? "暂无" : `${numberText(Number(value) * 100)}%`;
}
function contractText(value: unknown): string {
  if (value === undefined || value === null) return "未记录";
  return typeof value === "string" ? value : JSON.stringify(value);
}

const drawdowns = computed(() => {
  let peak = Number(run.value?.initial_cash || 0);
  return equities.value.map((row) => {
    const equity = Number(row.total_equity);
    peak = Math.max(peak, equity);
    return peak > 0 ? (equity / peak - 1) * 100 : 0;
  });
});
const chartPoints = computed(() => {
  const values = equities.value.map((row) => Number(row.total_equity));
  if (values.length < 2 || values.some((value) => !Number.isFinite(value))) return "";
  const low = Math.min(...values);
  const high = Math.max(...values);
  const span = high - low || 1;
  return values.map((value, index) => `${(index / (values.length - 1)) * 100},${90 - ((value - low) / span) * 80}`).join(" ");
});

async function loadTrades(signal: AbortSignal, offset = 0) {
  tradeLoading.value = true;
  try {
    const page = await getJson<Page<Trade>>(`/api/v1/backtests/runs/${runId.value}/trades?limit=100&offset=${offset}`, signal);
    trades.value = page.items;
    tradeTotal.value = page.total;
    tradeOffset.value = offset;
    tradeError.value = "";
  } finally { tradeLoading.value = false; }
}

async function load() {
  controller?.abort();
  controller = new AbortController();
  const signal = controller.signal;
  run.value = null; equities.value = []; trades.value = []; performance.value = null; benchmark.value = null; holdings.value = null;
  performanceNote.value = ""; benchmarkNote.value = ""; holdingsNote.value = ""; equityNote.value = ""; tradeError.value = ""; error.value = "";
  if (!Number.isSafeInteger(runId.value) || runId.value <= 0) { error.value = "无效的回测编号。"; return; }
  loading.value = true;
  try {
    const loaded = await getJson<Run>(`/api/v1/backtests/runs/${runId.value}`, signal);
    if (signal.aborted) return;
    run.value = loaded;
    if (loaded.status !== "success") return;
    const [equityResult, tradeResult, performanceResult, benchmarkResult, holdingsResult] = await Promise.allSettled([
      getJson<Page<Equity>>(`/api/v1/backtests/runs/${runId.value}/equity?limit=5000&offset=0`, signal),
      loadTrades(signal),
      getJson<Performance>(`/api/v1/backtests/runs/${runId.value}/performance`, signal),
      getJson<Benchmark>(`/api/v1/backtests/runs/${runId.value}/benchmark`, signal),
      fetchBacktestFinalHoldingsTyped(runId.value, signal),
    ]);
    if (signal.aborted) return;
    if (equityResult.status === "fulfilled") {
      equities.value = equityResult.value.items;
      if (equityResult.value.total > equities.value.length) equityNote.value = "净值记录超过 5000 条，曲线仅显示前 5000 条；请以完整历史数据为准。";
    } else equityNote.value = `净值读取失败：${equityResult.reason.message}`;
    if (tradeResult.status === "rejected") tradeError.value = `成交记录读取失败：${tradeResult.reason.message}`;
    if (performanceResult.status === "fulfilled") performance.value = performanceResult.value;
    else performanceNote.value = `绩效暂无：${performanceResult.reason.message}`;
    if (benchmarkResult.status === "fulfilled") benchmark.value = benchmarkResult.value;
    else benchmarkNote.value = `基准不可比较：${benchmarkResult.reason.message}`;
    if (holdingsResult.status === "fulfilled") holdings.value = holdingsResult.value;
    else holdingsNote.value = `最终持仓暂无：${holdingsResult.reason.message}`;
  } catch (cause) {
    if (!signal.aborted) error.value = cause instanceof Error ? cause.message : "回测读取失败";
  } finally { if (!signal.aborted) loading.value = false; }
}

async function changeTradePage(offset: number) {
  if (!controller || offset < 0 || offset >= tradeTotal.value) return;
  try { await loadTrades(controller.signal, offset); }
  catch (cause) { tradeError.value = cause instanceof Error ? `成交记录读取失败：${cause.message}` : "成交记录读取失败"; }
}

onMounted(load);
watch(runId, load);
</script>

<template>
  <div class="run-page">
    <p><RouterLink to="/backtests">← 回测列表</RouterLink></p>
    <h1>回测运行详情</h1>
    <p v-if="loading" role="status">正在读取回测…</p>
    <p v-if="error" class="warning" role="alert">{{ error }}</p>
    <template v-if="run">
      <header class="card"><h2>{{ run.name }} · #{{ run.id }}</h2><p>{{ run.strategy_name }} · {{ run.start_date }} 至 {{ run.end_date }} · {{ run.status }}</p><p v-if="run.status === 'failed'" class="warning">失败原因：{{ run.error_message || '未记录' }}</p></header>
      <section class="card"><h2>研究合同与资格</h2><dl class="grid"><div><dt>代码版本</dt><dd>{{ run.code_version }}</dd></div><div><dt>数据版本</dt><dd>{{ run.data_version }}</dd></div><div><dt>因子</dt><dd>{{ contractText(run.parameters.factor_specs) }}</dd></div><div><dt>费用模型</dt><dd>{{ contractText(run.parameters.fee_model_version) }}</dd></div><div><dt>费用参数</dt><dd>佣金率 {{ contractText(run.parameters.commission_rate) }} · 最低佣金 {{ contractText(run.parameters.min_commission) }} · 卖出印花税 {{ contractText(run.parameters.sell_stamp_duty_rate) }} · 过户费 {{ contractText(run.parameters.transfer_fee_rate) }} · 规费 {{ contractText(run.parameters.exchange_fee_rate) }} · 滑点 {{ contractText(run.parameters.slippage_bps) }} bps</dd></div><div><dt>复权与成交</dt><dd>{{ contractText(run.parameters.adjust_type) }} / 未复权执行价</dd></div><div><dt>验证模式</dt><dd>{{ contractText(run.parameters.validation_mode) }}</dd></div><div><dt>数据快照</dt><dd>{{ contractText(run.parameters.data_snapshot) }}</dd></div></dl><p class="caveat">单次探索性回测不构成样本外收益认证。历史 ST、行业、退市与公司行为等证据资格以实际研究合同和上游数据为准；缺涨跌停依据时可交易性判断可能放行。</p></section>
      <template v-if="run.status === 'success'">
        <section class="card"><h2>收益与回撤</h2><div class="grid"><div>期末权益<strong>{{ numberText(run.final_equity) }}</strong></div><div>全期收益<strong>{{ percent(run.total_return) }}</strong></div><div>最大回撤<strong>{{ performance ? percent(performance.maximum_drawdown) : '暂无' }}</strong></div><div>夏普<strong>{{ performance ? numberText(performance.sharpe_ratio) : '暂无' }}</strong></div></div><p v-if="equityNote" class="warning">{{ equityNote }}</p><svg v-if="chartPoints && !equityNote" viewBox="0 0 100 100" preserveAspectRatio="none" role="img" aria-label="每日权益变化曲线"><polyline :points="chartPoints" fill="none" stroke="#2563eb" stroke-width="1.4" vector-effect="non-scaling-stroke" /></svg><p v-if="!equities.length && !equityNote">暂无净值记录。</p><p v-if="equities.length">净值区间：{{ equities[0].trade_date }} 至 {{ equities[equities.length - 1].trade_date }}；末日回撤 {{ numberText(drawdowns[drawdowns.length - 1]) }}%。</p><p v-if="performanceNote">{{ performanceNote }}</p></section>
        <section class="card"><h2>基准比较</h2><p v-if="benchmark">{{ benchmark.benchmark }} · {{ benchmark.start_date }} 至 {{ benchmark.end_date }}：策略 {{ percent(benchmark.strategy_total_return) }}，基准 {{ percent(benchmark.benchmark_total_return) }}，超额 {{ percent(benchmark.excess_return) }}</p><p v-else class="warning">{{ benchmarkNote || '基准不可比较。' }}</p></section>
        <section class="card"><h2>最终持仓</h2><p v-if="holdingsNote" class="warning">{{ holdingsNote }}</p><p v-else-if="holdings && !holdings.holdings.length">末日无持仓。</p><div v-if="holdings?.holdings.length" class="scroll"><table><thead><tr><th>证券</th><th>名称</th><th>数量</th><th>市值</th><th>权重</th></tr></thead><tbody><tr v-for="item in holdings.holdings" :key="item.symbol"><td>{{ item.symbol }}</td><td>{{ item.name || '—' }}</td><td>{{ item.quantity }}</td><td>{{ numberText(item.market_value) }}</td><td>{{ percent(item.weight) }}</td></tr></tbody></table></div></section>
        <section class="card"><h2>成交记录</h2><p v-if="tradeError" class="warning" role="alert">{{ tradeError }}</p><p v-else-if="tradeTotal === 0 && !tradeLoading">本次回测无成交；请核对信号和可交易性限制。</p><div class="scroll" v-if="trades.length"><table><thead><tr><th>成交日</th><th>证券</th><th>方向</th><th>数量</th><th>参考价</th><th>成交价</th><th>费用合计</th></tr></thead><tbody><tr v-for="trade in trades" :key="trade.id"><td>{{ trade.trade_date }}</td><td>{{ trade.symbol }}</td><td>{{ trade.side === 'buy' ? '买入' : '卖出' }}</td><td>{{ trade.quantity }}</td><td>{{ numberText(trade.reference_price) }}</td><td>{{ numberText(trade.fill_price) }}</td><td>{{ numberText(trade.commission) }}</td></tr></tbody></table></div><p v-if="tradeTotal">第 {{ tradeOffset + 1 }}–{{ Math.min(tradeOffset + 100, tradeTotal) }} / {{ tradeTotal }} 笔 <button :disabled="tradeOffset === 0 || tradeLoading" @click="changeTradePage(tradeOffset - 100)">上一页</button> <button :disabled="tradeOffset + 100 >= tradeTotal || tradeLoading" @click="changeTradePage(tradeOffset + 100)">下一页</button></p><p class="caveat">费用合计包含显式交易费用；滑点已体现在成交价，不重复计入费用。</p></section>
      </template>
    </template>
  </div>
</template>

<style scoped>
.run-page{max-width:1160px;margin:auto;padding:28px 20px 70px;color:#182230}.card{background:white;border:1px solid #dce3ed;border-radius:10px;padding:20px;margin:18px 0}.card h2{margin:0 0 14px;font-size:20px}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:15px}.grid>div{overflow-wrap:anywhere}.grid strong{display:block;font-size:22px}.grid dt{font-weight:700;color:#667085}.grid dd{margin:4px 0}.warning{color:#a34700}.caveat{color:#667085;font-size:13px}.scroll{overflow:auto}table{border-collapse:collapse;min-width:750px;width:100%}td,th{padding:9px;text-align:left;border-bottom:1px solid #e5e7eb}svg{height:250px;width:100%;background:#f8fafc;border:1px solid #e5e7eb;margin-top:16px}button{padding:6px 12px;border:1px solid #cbd5e1;border-radius:5px;background:white;cursor:pointer}button:disabled{opacity:.5;cursor:default}
</style>
