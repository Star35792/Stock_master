<script setup lang="ts">
import { computed, onBeforeUnmount } from "vue";
import { createDividendScenario } from "../../composables/dividendScenario";
const state = createDividendScenario();
const { input, report, loading, error, calculate, sample, dispose } = state;
const event = computed(() => input.events[0]!);
onBeforeUnmount(dispose);
const statuses: Record<string, string> = { awaiting_record: "待登记", awaiting_ex: "待除息", receivable: "待到账", paid: "已到账" };
</script>

<template>
  <details class="scenario-panel">
    <summary>现金分红情景账本 <span>合成研究</span></summary>
    <p class="muted">按给定价格和成交推演现金、股数与应收。应收在到账前不能用于买入，清仓后仍保留已取得的分红。</p>
    <p class="notice">这里的价格、成交与固定税率均为显式假设。正式回测仍拒绝未处理公司行为，本工具不改动持仓或产生订单。</p>
    <div class="sample-actions"><button type="button" :disabled="loading" @click="sample('hold')">持有至到账</button><button type="button" :disabled="loading" @click="sample('sell')">到账前清仓</button><button type="button" :disabled="loading" @click="sample('pending')">期末未到账</button><button type="button" :disabled="loading" @click="sample('tax')">假设税率 20%</button></div>
    <form @submit.prevent="calculate">
      <fieldset :disabled="loading"><legend>期初与分红假设</legend>
        <div class="scenario-fields">
          <label>证券标识<input v-model="input.symbol" required maxlength="20"></label>
          <label>期初现金<input v-model="input.initial_cash" inputmode="decimal" required></label>
          <label>期初已结算股数<input v-model.number="input.initial_quantity" type="number" min="0" max="10000000" step="1" required></label>
          <label>期初未复权价格<input v-model="input.initial_price" inputmode="decimal" required></label>
          <label>登记日<input v-model="event.record_date" type="date" required></label>
          <label>除息日<input v-model="event.ex_date" type="date" required></label>
          <label>到账日<input v-model="event.payment_date" type="date" required></label>
          <label>税前每股现金<input v-model="event.cash_per_share" inputmode="decimal" required></label>
          <label>假设税率（0–1）<input v-model="event.assumed_tax_rate" inputmode="decimal" required></label>
        </div>
        <h3>未复权合成价格</h3>
        <p class="muted">窗口内登记、除息、到账日期都需列出。到账日在期末之后时保留应收。</p>
        <div v-for="(row, index) in input.prices" :key="index" class="scenario-row">
          <label>日期<input v-model="row.trade_date" type="date" required></label><label>收盘价<input v-model="row.close" inputmode="decimal" required></label>
          <button type="button" :disabled="input.prices.length <= 1" :aria-label="`移除第 ${index + 1} 个价格`" @click="input.prices.splice(index, 1)">移除</button>
        </div>
        <button type="button" :disabled="input.prices.length >= 366" @click="input.prices.push({ trade_date: '', close: '' })">添加价格日</button>
        <h3>给定成交（可选）</h3><p class="muted">同日按下列顺序处理。费用是给定金额；当日买入股份不可当日卖出。</p>
        <div v-for="(fill, index) in input.fills" :key="index" class="scenario-row fills-row">
          <label>成交日<input v-model="fill.trade_date" type="date" required></label>
          <label>方向<select v-model="fill.side"><option value="buy">买入</option><option value="sell">卖出</option></select></label>
          <label>股数<input v-model.number="fill.quantity" type="number" min="1" max="10000000" step="1" required></label>
          <label>成交价<input v-model="fill.price" inputmode="decimal" required></label>
          <label>费用金额<input v-model="fill.fee" inputmode="decimal" required></label>
          <button type="button" :aria-label="`移除第 ${index + 1} 笔成交`" @click="input.fills.splice(index, 1)">移除</button>
        </div>
        <button type="button" :disabled="input.fills.length >= 500" @click="input.fills.push({ trade_date: event.ex_date, side: 'sell', quantity: 100, price: '9.80', fee: '0' })">添加给定成交</button>
      </fieldset>
      <button class="calculate" type="submit" :disabled="loading">{{ loading ? '正在计算…' : '计算情景账本' }}</button>
    </form>
    <p v-if="error" role="alert" class="notice">{{ error }}</p>
    <div v-if="report" aria-live="polite">
      <p class="scenario-total">期初权益 <strong>{{ report.initial_equity }}</strong> → 期末权益 <strong>{{ report.final_equity }}</strong> · 合成情景结果</p>
      <div class="scenario-scroll"><table><caption>每日现金、应收与权益</caption><thead><tr><th>日期</th><th>可用现金</th><th>股数</th><th>净应收</th><th>市值</th><th>总权益</th><th>累计假设税额</th></tr></thead><tbody><tr v-for="day in report.days" :key="day.trade_date"><td>{{ day.trade_date }}</td><td>{{ day.cash }}</td><td>{{ day.quantity }}</td><td>{{ day.receivable }}</td><td>{{ day.market_value }}</td><td>{{ day.total_equity }}</td><td>{{ day.cumulative_assumed_tax }}</td></tr></tbody></table></div>
      <p v-for="item in report.events" :key="item.event_id">{{ statuses[item.status] || item.status }} · 资格 {{ item.eligible_quantity }} 股 · 税前 {{ item.gross }} · 假设税额 {{ item.assumed_tax }} · 净额 {{ item.net }}</p>
      <details><summary>计算口径与输入摘要</summary><ul><li v-for="item in report.declarations" :key="item">{{ item }}</li></ul><p>{{ report.version }} · {{ report.contract_sha256 }}</p></details>
    </div>
  </details>
</template>

<style scoped>
.scenario-panel{padding:20px;background:white;border:1px solid #dce3ed;border-radius:12px;margin:20px 0;color:#182230;overflow-wrap:anywhere}.scenario-panel>summary{cursor:pointer;font-size:18px;font-weight:700}.scenario-panel>summary span{font-size:12px;color:#667085;margin-left:10px}.muted{color:#667085;font-size:13px}.notice{padding:10px;background:#fff7e8;border-left:3px solid #ba7417;color:#8a4b00;font-size:13px}.sample-actions,.scenario-row{display:flex;gap:10px;flex-wrap:wrap;margin:12px 0}.scenario-fields{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px}fieldset{border:1px solid #e2e8f0;border-radius:8px;margin:16px 0;padding:16px}legend{font-weight:600}label{display:flex;flex-direction:column;gap:5px;font-size:13px;color:#475467}input,select{font:inherit;min-width:0;padding:8px;border:1px solid #cbd5e1;border-radius:5px;background:white}.scenario-row{align-items:end}.scenario-row label{flex:1;min-width:120px}.fills-row label{max-width:180px}button{font:inherit;padding:8px 12px;border:1px solid #cbd5e1;border-radius:5px;background:#fff;cursor:pointer;font-size:13px}button:disabled{opacity:.5;cursor:default}.calculate{background:#1d4ed8;color:white;border-color:#1d4ed8}h3{font-size:15px;margin-top:20px}.scenario-scroll{overflow:auto}table{border-collapse:collapse;min-width:700px;width:100%;font-size:13px}th,td{text-align:right;padding:10px;border-bottom:1px solid #e5e7eb}th:first-child,td:first-child{text-align:left}caption{text-align:left;font-weight:600;margin:10px 0}.scenario-total{font-size:15px}details details{font-size:13px;margin-top:15px}
</style>
