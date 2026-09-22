<script setup lang="ts">
import { computed, onBeforeUnmount, reactive, ref, watch } from "vue";
import type { PriceValueRequest } from "../../priceValueApi";
import { runPriceValueBacktest, type PriceValueBacktestRequest, type PriceValueBacktestResponse } from "../../priceValueBacktestApi";

const props = defineProps<{ currentAssumptions: PriceValueRequest }>();
const form = reactive({ name: "价格价值假设历史模拟", symbol: "", start_date: "", end_date: "", initial_cash: "100000",
  position_weight: "0.20", required_margin: "0.30", max_participation_rate: "0.01", sell_stamp_duty_rate: "0.0005",
  fees: { commission_rate: "0.0003", min_commission: "5", transfer_fee_rate: "0", exchange_fee_rate: "0", slippage_bps: "2" },
  assumptions: [] as Array<{ version: string; valuation_at: string; available_at: string; valid_until: string; source_reference: string; diluted_shares: string; scenarioJson: string; thesis_broken: boolean }>,
});
const fees = { commission_rate: "佣金比例", min_commission: "最低佣金（元）", transfer_fee_rate: "过户费比例", exchange_fee_rate: "其他交易费比例", slippage_bps: "滑点（基点）" } as const;
const actionLabels: Record<string, string> = { cancelled: "取消信号", unfilled: "未成交", buy: "买入", sell: "卖出", hold: "持有", review: "复核持仓", wait: "等待", sell_signal: "卖出信号", buy_signal: "买入信号" };
const reasonLabels: Record<string, string> = {
  assumption_expired_before_open: "开盘前假设已过期", assumption_revised_before_open: "开盘前假设版本已更新",
  suspended: "停牌，暂不成交", price_limits_missing: "缺少涨跌停价格", limit_up: "开盘涨停，不买入", limit_down: "开盘跌停，不卖出",
  fill_outside_price_limits: "预计成交价超出涨跌停范围", prior_volume_capacity: "前一交易日成交量不足以支持整手交易",
  insufficient_budget: "资金预算不足", all_in_price_above_ceiling: "含费成本超过安全边际买价上限",
  insufficient_cash_for_exit_fees: "卖出后资金不足以支付费用", entry_filled: "买入已成交", exit_filled: "卖出已完成", exit_partial: "受成交容量限制，部分卖出",
  exit_pending: "继续等待卖出成交", assumption_missing: "尚无已可得的假设版本", assumption_expired: "当前假设已过期",
  thesis_broken: "投资假设失效", base_value_reached: "价格达到基准价值", end_of_window: "已到回测末日，不生成新的成交",
  existing_position_no_top_up: "维持已有持仓，不自动加仓", insufficient_budget_or_capacity: "资金或成交容量不足以支持整手买入", price_condition_met: "价格满足所需安全边际",
};
const result = ref<PriceValueBacktestResponse | null>(null);
const pending = ref(false); const error = ref(""); const changed = ref(false);
const pages = reactive({ trades: 1, equity: 1, decisions: 1 });
const pageSize = 20;
let generation = 0; let controller: AbortController | null = null;
watch(form, () => { generation++; controller?.abort(); if (result.value || pending.value) changed.value = true; result.value = null; pending.value = false; error.value = ""; }, { deep: true, flush: "sync" });
onBeforeUnmount(() => { generation++; controller?.abort(); });
const dailyRows = computed(() => result.value?.equity_curve.map(row => ({ ...row, quantity: result.value?.positions.find(position => position.trade_date === row.trade_date)?.quantity ?? 0 })) ?? []);
function addCurrent() {
  const current = props.currentAssumptions;
  if (current.company_profile !== "stable_non_financial") { error.value = "首版仅支持稳定经营的非金融企业，请先修改上方企业类型。"; return; }
  if (form.symbol && current.symbol !== form.symbol) { error.value = "上方证券与回测证券不同，请先统一证券，避免混用假设。"; return; }
  form.symbol = current.symbol;
  form.assumptions.push({ version: current.assumption_version, valuation_at: current.valuation_at, available_at: current.assumptions_available_at,
    valid_until: current.valid_until, source_reference: current.assumptions_note, diluted_shares: current.diluted_shares,
    scenarioJson: JSON.stringify(current.scenarios, null, 2), thesis_broken: current.thesis_broken });
}
function money(value: string, digits = 2) { return Number(value).toLocaleString("zh-CN", { minimumFractionDigits: digits, maximumFractionDigits: digits }); }
function percent(value: string) { return Number(value).toLocaleString("zh-CN", { style: "percent", minimumFractionDigits: 2, maximumFractionDigits: 2 }); }
function slice<T>(items: T[], page: number) { return items.slice((page - 1) * pageSize, page * pageSize); }
async function run() {
  controller?.abort(); const requestId = ++generation; controller = new AbortController(); const signal = controller.signal;
  pending.value = true; error.value = ""; changed.value = false; result.value = null;
  try {
    const assumptions = form.assumptions.map(({ scenarioJson, ...item }) => ({ ...item, scenarios: JSON.parse(scenarioJson) as PriceValueRequest["scenarios"] }));
    const payload: PriceValueBacktestRequest = { ...form, symbol: form.symbol.trim().toUpperCase(), assumptions, company_profile: "stable_non_financial", currency: "CNY", lot_size: 100 };
    const response = await runPriceValueBacktest(payload, signal);
    if (requestId === generation) { result.value = response; pages.trades = 1; pages.equity = 1; pages.decisions = 1; }
  } catch (cause) { if (requestId === generation && !signal.aborted) error.value = cause instanceof SyntaxError ? "情景 JSON 格式错误，请检查引号、逗号和括号。" : cause instanceof Error ? cause.message : "回测失败，请重试。"; }
  finally { if (requestId === generation) pending.value = false; }
}
function download() {
  if (!result.value) return;
  const url = URL.createObjectURL(new Blob([JSON.stringify(result.value, null, 2)], { type: "application/json" }));
  const link = document.createElement("a"); link.href = url; link.download = `price-value-backtest-${result.value.run_id}.json`; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
}
</script>

<template>
  <section class="backtest-panel">
    <h2>假设历史模拟</h2>
    <p class="notice">手工版本化 FCFE 假设，不代表经过认证的历史预测。首版仅支持单只 A 股主板非金融股票与现金，100 股整手。运行会保存研究记录，不创建模拟账户订单或实盘订单。</p>
    <p>收盘产生信号，下一交易日开盘按实际数量和费用重新检查买价。已持仓不自动加仓；收盘达到基准价值或投资假设失效时发出退出信号，未能卖出的持仓继续等待成交。</p>
    <form @submit.prevent="run">
      <fieldset><legend>回测区间与资金</legend><div class="grid">
        <label>研究名称<input v-model="form.name" required maxlength="100" /></label>
        <label>证券代码<input v-model="form.symbol" required placeholder="例如 600519.SH" /></label>
        <label>开始日期<input v-model="form.start_date" required type="date" /></label>
        <label>结束日期<input v-model="form.end_date" required type="date" /></label>
        <label>初始资金（元）<input v-model="form.initial_cash" required inputmode="decimal" /></label>
        <label>单次开仓预算（比例）<input v-model="form.position_weight" required inputmode="decimal" /></label>
        <label>所需安全边际（比例）<input v-model="form.required_margin" required inputmode="decimal" /></label>
        <label>最大成交参与率（比例，至多 0.1）<input v-model="form.max_participation_rate" required inputmode="decimal" /></label>
      </div><p>比例 0.20 表示用空仓时现金的 20% 作为含费开仓预算，余款保留现金；不随每日股价自动调仓。区间最多 1096 个自然日。</p></fieldset>
      <fieldset><legend>费用与滑点</legend><p>下列预填值仅为研究示例，不代表真实费率；请按研究区间调整。</p><div class="grid"><label v-for="(label,key) in fees" :key="key">{{ label }}<input v-model="form.fees[key]" required inputmode="decimal" /></label><label>卖出印花税比例<input v-model="form.sell_stamp_duty_rate" required inputmode="decimal" /></label></div></fieldset>
      <fieldset><legend>版本化估值假设</legend><p>先填写上方估值表单，再将其复制为一个版本。复制保留原始时间，不会自动改为历史日期；之后两处独立编辑。估值时间 ≤ 可得时间 ≤ 有效期，版本及可得时间须各自唯一。</p><p>尚未可得的版本不影响过去；版本空档不新增买入，已有持仓进入复核。首次可得时间是用户声明，未经历史血缘认证。</p>
        <button type="button" @click="addCurrent">从上方表单添加假设版本</button>
        <p v-if="!form.assumptions.length">尚未添加版本。请至少添加一个版本后运行。</p>
        <section v-for="(item,index) in form.assumptions" :key="index" class="assumption">
          <div class="row"><h3>假设版本 {{ index + 1 }}</h3><button type="button" class="secondary" @click="form.assumptions.splice(index,1)">删除此版本</button></div>
          <div class="grid"><label>版本标识<input v-model="item.version" required /></label><label>稀释后普通股股数（股）<input v-model="item.diluted_shares" required inputmode="decimal" /></label><label>估值时间（含时区）<input v-model="item.valuation_at" required /></label><label>首次可得时间（含时区）<input v-model="item.available_at" required /></label><label>有效期至（含时区）<input v-model="item.valid_until" required /></label></div>
          <label>假设依据与来源<textarea v-model="item.source_reference" required rows="2" /></label>
          <label class="checkbox"><input v-model="item.thesis_broken" type="checkbox" /> 此版本声明投资假设失效</label>
          <details><summary>查看或编辑三情景现金流</summary><p>年度 FCFE 与终值 FCFE 均为人民币总额，非每股；保留 JSON 字符串中的引号以维持精度。</p><label>三情景 JSON<textarea v-model="item.scenarioJson" required rows="18" spellcheck="false" /></label></details>
        </section>
      </fieldset>
      <button type="submit" :disabled="pending || !form.assumptions.length">{{ pending ? '回测运行中…' : '运行假设历史模拟' }}</button>
      <p v-if="pending">研究请求已提交；修改参数会停止等待结果，但服务端已开始的运行可能仍完成并保存。</p>
      <p v-if="changed" role="status">回测参数已修改，旧结果已失效，请重新运行。</p><p v-if="error" class="error" role="alert">{{ error }}</p>
    </form>
    <section v-if="result" class="results" aria-live="polite">
      <div class="row"><h3>研究 #{{ result.run_id }} · 历史模拟结果</h3><button type="button" @click="download">导出完整 JSON</button></div>
      <p>仅假设研究 · 历史时点未认证 · 不可交易</p>
      <dl class="grid"><div><dt>期末净值（元）</dt><dd>{{ money(result.final_equity) }}</dd></div><div><dt>区间收益率</dt><dd>{{ percent(result.total_return) }}</dd></div><div><dt>最大回撤</dt><dd>{{ percent(result.maximum_drawdown) }}</dd></div><div><dt>期末现金（元）</dt><dd>{{ money(result.final_cash) }}</dd></div><div><dt>成交笔数</dt><dd>{{ result.trade_count }}</dd></div></dl>
      <ul><li v-for="(item,i) in result.limitations" :key="i">{{ item }}</li></ul>
      <details open><summary>模拟成交（{{ result.trades.length }} 笔）</summary><p v-if="!result.trades.length">本区间没有成交，请结合决策日志查看原因。</p><div class="table-wrap"><table v-if="result.trades.length"><thead><tr><th>信号日</th><th>成交日</th><th>方向</th><th>数量</th><th>参考价</th><th>成交价</th><th>交易费用</th><th>成交后现金</th></tr></thead><tbody><tr v-for="(trade,i) in slice(result.trades,pages.trades)" :key="i"><td>{{ trade.signal_date }}</td><td>{{ trade.trade_date }}</td><td>{{ trade.side === 'buy' ? '买入' : '卖出' }}</td><td>{{ trade.quantity }}</td><td>{{ money(trade.reference_price,4) }}</td><td>{{ money(trade.fill_price,4) }}</td><td>{{ money(trade.commission,4) }}</td><td>{{ money(trade.cash_after) }}</td></tr></tbody></table></div><div class="pagination"><button type="button" :disabled="pages.trades <= 1" @click="pages.trades--">上一页</button><span>第 {{ pages.trades }} 页</span><button type="button" :disabled="pages.trades * pageSize >= result.trades.length" @click="pages.trades++">下一页</button></div></details>
      <details><summary>每日净值、现金与持仓（{{ dailyRows.length }} 日）</summary><div class="table-wrap"><table><thead><tr><th>日期</th><th>净值</th><th>现金</th><th>持仓股数</th><th>持仓市值</th></tr></thead><tbody><tr v-for="row in slice(dailyRows,pages.equity)" :key="row.trade_date"><td>{{ row.trade_date }}</td><td>{{ money(row.total_equity) }}</td><td>{{ money(row.cash) }}</td><td>{{ row.quantity }}</td><td>{{ money(row.market_value) }}</td></tr></tbody></table></div><div class="pagination"><button type="button" :disabled="pages.equity <= 1" @click="pages.equity--">上一页</button><span>第 {{ pages.equity }} 页</span><button type="button" :disabled="pages.equity * pageSize >= dailyRows.length" @click="pages.equity++">下一页</button></div></details>
      <details><summary>决策与阻断日志（{{ result.decisions.length }} 条）</summary><div class="table-wrap"><table><thead><tr><th>日期</th><th>时点</th><th>决策</th><th>假设版本</th><th>信号日</th><th>原因</th></tr></thead><tbody><tr v-for="(row,i) in slice(result.decisions,pages.decisions)" :key="i"><td>{{ row.trade_date }}</td><td>{{ row.phase === 'open' ? '开盘' : '收盘' }}</td><td>{{ actionLabels[row.action] ?? '其他研究决策' }}</td><td>{{ row.assumption_version ?? '无有效版本' }}</td><td>{{ row.signal_date ?? '—' }}</td><td>{{ reasonLabels[row.reason] ?? '其他限制原因，请查看完整研究记录' }}</td></tr></tbody></table></div><div class="pagination"><button type="button" :disabled="pages.decisions <= 1" @click="pages.decisions--">上一页</button><span>第 {{ pages.decisions }} 页</span><button type="button" :disabled="pages.decisions * pageSize >= result.decisions.length" @click="pages.decisions++">下一页</button></div></details>
      <details><summary>研究留痕</summary><p>计算时间：{{ result.evaluated_at }}</p><p class="hash">合同指纹：{{ result.contract_hash }}</p></details>
    </section>
  </section>
</template>

<style scoped>
.backtest-panel { border-top: 2px solid #cfddd4; padding-top: 26px; margin-top: 12px; } h2,h3,p { margin: 0 0 14px; } .notice { background: #fff5db; padding: 18px; border-radius: 10px; }
form { display: grid; gap: 20px; } fieldset,.results { border: 1px solid #d8e0dd; background: white; border-radius: 12px; padding: 22px; min-width: 0; } legend { font-weight: 700; padding: 0 8px; }
.grid { display: grid; grid-template-columns: repeat(3,minmax(0,1fr)); gap: 16px; } label { display: grid; gap: 7px; margin-bottom: 12px; font-size: 14px; } input,textarea { width: 100%; box-sizing: border-box; min-width: 0; border: 1px solid #becbc5; padding: 10px; border-radius: 7px; font: inherit; background: white; color: #23372f; } textarea { resize: vertical; }
button { background: #24694f; color: white; border: 0; padding: 10px 16px; border-radius: 7px; cursor: pointer; font: inherit; justify-self: start; } button:disabled { opacity: .5; cursor: default; } .secondary { background: #eef3ef; color: #364b3d; }
.row,.pagination { display: flex; justify-content: space-between; gap: 15px; align-items: center; } .assumption { background: #f5f8f6; padding: 18px; border-radius: 10px; margin-top: 18px; } .checkbox { display: flex; align-items: center; } .checkbox input { width: auto; }
.error { color: #a0282a; } .results { margin-top: 22px; } dd { margin: 8px 0 16px; font-size: 20px; } dt { color: #62766a; } details { margin-top: 18px; } summary { cursor: pointer; font-weight: 600; margin-bottom: 14px; }
.table-wrap { overflow-x: auto; } table { width: 100%; border-collapse: collapse; font-size: 13px; } th,td { text-align: left; padding: 10px; border-bottom: 1px solid #e1e8e3; } th { white-space: nowrap; } .pagination { justify-content: flex-end; margin-top: 10px; } .hash { overflow-wrap: anywhere; }
@media(max-width: 900px) { .grid { grid-template-columns: 1fr; } .row { align-items: flex-start; flex-direction: column; } }
</style>
