<script setup lang="ts">
import { Filter, RefreshCw } from "@lucide/vue";
import { formatPctChange, qualityStatusLabel } from "../../components/operations/formatters";

export interface DailyBarItem {
  symbol: string;
  trade_date: string;
  adjust_type: string;
  open: string;
  high: string;
  low: string;
  close: string;
  pre_close: string | null;
  pct_change: string | null;
  volume: string | null;
  amount: string | null;
  quality_status: string;
  is_suspect: boolean;
  source_name: string;
}

defineProps<{
  symbol: string;
  bars: DailyBarItem[];
  qualityStatus: string;
  startDate: string;
  endDate: string;
  loading: boolean;
}>();

const emit = defineEmits<{
  (event: "update:quality-status", value: string): void;
  (event: "update:start-date", value: string): void;
  (event: "update:end-date", value: string): void;
  (event: "query"): void;
}>();
</script>

<template>
  <section class="bars-panel market-panel">
    <header class="bars-panel__header">
      <div>
        <span class="market-kicker">日线质量视图</span>
        <h2>{{ symbol || "选择证券" }} 的行情记录</h2>
        <p>仅展示日线收盘数据，不代表实时行情或可成交报价。</p>
      </div>
      <Filter :size="19" aria-hidden="true" />
    </header>
    <form class="bars-filter" @submit.prevent="emit('query')">
      <label>质量状态
        <select :value="qualityStatus" @change="emit('update:quality-status', ($event.target as HTMLSelectElement).value)">
          <option value="">全部状态</option>
          <option value="trusted">可信</option>
          <option value="manual_fixed">人工修复</option>
          <option value="suspect">可疑</option>
          <option value="rejected">拒绝</option>
        </select>
      </label>
      <label>起始日期<input type="date" :value="startDate" @input="emit('update:start-date', ($event.target as HTMLInputElement).value)" /></label>
      <label>结束日期<input type="date" :value="endDate" @input="emit('update:end-date', ($event.target as HTMLInputElement).value)" /></label>
      <button type="submit" class="market-secondary-button" :disabled="loading || !symbol"><RefreshCw :size="15" :class="{ spin: loading }" />查询</button>
    </form>
    <div v-if="!symbol" class="market-empty">从左侧搜索并选择证券后，这里会加载日线数据。</div>
    <div v-else-if="!loading && !bars.length" class="market-empty">该筛选条件下没有日线记录。可以放宽日期或质量状态筛选。</div>
    <div v-else class="bars-table-wrap">
      <table class="bars-table">
        <thead><tr><th>交易日</th><th>收盘</th><th>涨跌幅</th><th>成交额</th><th>质量</th><th>来源</th></tr></thead>
        <tbody>
          <tr v-for="bar in bars" :key="`${bar.trade_date}-${bar.adjust_type}`">
            <td>{{ bar.trade_date }}</td><td>{{ bar.close }}</td><td>{{ formatPctChange(bar.pct_change) }}</td><td>{{ bar.amount ?? "-" }}</td>
            <td><span :class="['quality-badge', `quality-badge--${bar.quality_status}`]">{{ qualityStatusLabel(bar.quality_status) }}</span></td><td>{{ bar.source_name }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </section>
</template>

<style scoped>
.bars-panel { display: grid; gap: 16px; }
.bars-panel__header { display: flex; justify-content: space-between; gap: 12px; color: #2563eb; }
.bars-panel__header > div { display: grid; gap: 5px; }
.bars-panel h2 { color: #101828; font-size: 18px; }
.bars-panel p { color: #667085; font-size: 12px; }
.market-kicker { color: #2563eb; font-size: 11px; font-weight: 800; letter-spacing: .08em; }
.bars-filter { display: flex; flex-wrap: wrap; align-items: end; gap: 10px; padding: 12px; border: 1px solid #eef1f5; border-radius: 7px; background: #fbfcfe; }
.bars-filter label { display: grid; gap: 5px; min-width: 130px; color: #667085; font-size: 11px; font-weight: 700; }
.bars-filter select, .bars-filter input { min-height: 34px; border: 1px solid #d7dce4; border-radius: 6px; padding: 0 8px; color: #344054; background: #fff; font: inherit; }
.market-secondary-button { display: inline-flex; align-items: center; gap: 7px; min-height: 34px; border: 1px solid #d7dce4; border-radius: 6px; padding: 0 11px; color: #344054; background: #fff; cursor: pointer; font-weight: 700; }
.market-secondary-button:disabled { opacity: .5; cursor: wait; }
.market-empty { padding: 22px; color: #667085; background: #f8fafc; font-size: 12px; line-height: 1.5; }
.bars-table-wrap { overflow-x: auto; }
.bars-table { min-width: 650px; width: 100%; border-collapse: collapse; }
.bars-table th, .bars-table td { height: 40px; padding: 0 9px; border-bottom: 1px solid #eef1f5; text-align: left; white-space: nowrap; }
.bars-table th { color: #667085; font-size: 11px; }
.bars-table td { color: #344054; font-size: 12px; }
.quality-badge { display: inline-flex; min-height: 22px; align-items: center; border-radius: 999px; padding: 0 7px; font-size: 10px; font-weight: 800; }
.quality-badge--trusted, .quality-badge--manual_fixed { color: #047857; background: #dff6ea; }
.quality-badge--suspect { color: #9a3412; background: #ffedd5; }
.quality-badge--rejected { color: #b42318; background: #fee4e2; }
.spin { animation: spin .9s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }
</style>
