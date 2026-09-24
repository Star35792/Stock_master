<script setup lang="ts">
import { onMounted, onUnmounted, ref, watch } from "vue";
import { fetchPriceValueProfitStatus, type PriceValueProfitStatus } from "../../priceValueScreenApi";

const props = defineProps<{ refreshKey: number }>();
const profit = ref<PriceValueProfitStatus | null>(null);
const loading = ref(false);
const error = ref("");
let controller: AbortController | null = null;

function money(value: string | null): string {
  if (value === null) return "—";
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed.toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : "—";
}
function percent(value: string): string {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed.toLocaleString("zh-CN", { style: "percent", minimumFractionDigits: 2, maximumFractionDigits: 2 }) : "—";
}
async function refresh() {
  controller?.abort();
  const current = new AbortController();
  controller = current;
  loading.value = true;
  error.value = "";
  try {
    profit.value = await fetchPriceValueProfitStatus(current.signal);
  } catch (cause) {
    if (!current.signal.aborted) {
      profit.value = null;
      error.value = cause instanceof Error ? cause.message : "收益状态读取失败";
    }
  } finally {
    if (!current.signal.aborted) loading.value = false;
  }
}
onMounted(refresh);
watch(() => props.refreshKey, refresh);
onUnmounted(() => controller?.abort());
</script>

<template>
  <section class="profit-panel" aria-labelledby="profit-heading">
    <div class="heading"><div><p class="eyebrow">先看结果</p><h2 id="profit-heading">目前的收益记录</h2></div><button type="button" @click="refresh" :disabled="loading">{{ loading ? "读取中…" : "刷新" }}</button></div>
    <p class="context">全市场 PE/PB/ROE 初筛尚无可归因收益；下面分别显示单股假设回测和模拟账户的状态。</p>
    <p v-if="error" class="error" role="alert">{{ error }}</p>
    <p v-if="!profit && loading" role="status">正在读取收益记录…</p>
    <div v-if="profit" class="cards">
      <section><span class="card-label">单股假设历史回测</span><template v-if="profit.value_backtest"><strong>{{ money(profit.value_backtest.hypothetical_profit) }} 元</strong><p>假设账面净盈亏 · 区间收益率 {{ percent(profit.value_backtest.total_return) }}</p><small>研究 #{{ profit.value_backtest.run_id }}，截至 {{ profit.value_backtest.end_date }}。含未平仓持仓估值，历史时点未认证，不代表真实获利。</small></template><template v-else><strong>暂无记录</strong><p>还没有运行单股假设回测，因此无法计算假设盈亏。</p></template></section>
      <section><span class="card-label">模拟盘账户</span><template v-if="profit.paper_accounts.length"><strong>累计利润不可计算</strong><p>缺少可核验的初始投入与资金流基线。</p><details><summary>查看账户最近净值</summary><p v-for="account in profit.paper_accounts" :key="account.account_id">{{ account.account_name }}：{{ money(account.latest_total_equity) }} 元（{{ account.latest_valuation_date ?? "未估值" }}）</p></details></template><template v-else><strong>暂无账户</strong><p>还没有模拟盘账户，因此无法报告模拟利润。</p></template></section>
    </div>
  </section>
</template>

<style scoped>
.profit-panel { padding: 20px 22px; border: 1px solid #d8e0dd; border-radius: 14px; background: #fff; }
.heading { display: flex; justify-content: space-between; align-items: start; gap: 12px; }
.eyebrow { margin: 0 0 4px; color: #326b58; font-size: 12px; font-weight: 700; }
h2 { margin: 0 0 8px; font-size: 19px; }
.context { margin: 0 0 14px; color: #5a6b62; font-size: 13px; line-height: 1.55; }
.cards { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }
.cards section { min-width: 0; padding: 16px; border-radius: 10px; background: #f4f8f6; }
.card-label { display: block; margin-bottom: 7px; color: #52695a; font-size: 12px; font-weight: 700; }
.cards strong { display: block; margin-bottom: 5px; color: #1b3f30; font-size: 19px; }
.cards p, .cards small { margin: 0; color: #52645b; line-height: 1.5; }
.cards p { font-size: 13px; } .cards small { font-size: 12px; }
button { border: 1px solid #b5cec0; border-radius: 8px; padding: 8px 12px; background: #fff; color: #24694f; font: inherit; cursor: pointer; }
button:disabled { opacity: .6; cursor: default; } .error { color: #a0282a; }
details { margin-top: 10px; } summary { cursor: pointer; font-size: 13px; font-weight: 700; }
@media (max-width: 760px) { .cards { grid-template-columns: 1fr; } }
</style>
