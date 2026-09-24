<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from "vue";
import {
  fetchPriceValueScreen,
  type PriceValueScreenReport,
} from "../../priceValueScreenApi";

const screen = ref<PriceValueScreenReport | null>(null);
const loading = ref(false);
const error = ref("");
const maxPe = ref<string | number>("");
const maxPb = ref<string | number>("");
const minRoe = ref<string | number>("");
const topRowReasons = computed(() => Object.entries(screen.value?.reason_counts ?? {})
  .filter(([code]) => code !== "no_reviewable_candidates")
  .sort((left, right) => right[1] - left[1]).slice(0, 3));
const showAll = ref(false);
const page = ref(1);
const pageSize = 30;
let controller: AbortController | null = null;
const reasonNames: Record<string, string> = {
  trading_calendar_missing: "缺少交易日历",
  trading_calendar_stale: "交易日历已过期",
  trading_calendar_incomplete: "交易所日历不完整",
  trusted_bars_missing_for_target: "目标日缺可信日线",
  valuation_missing_for_target: "目标日缺估值快照",
  roe_reported_missing_for_target: "目标日缺披露 ROE",
  input_preparation_missing_for_target: "目标日晚间估值采集未完成",
  input_batch_unverified: "原始估值批次无法核验",
  roe_input_batch_unverified: "ROE 原始批次无法核验",
  roe_observation_lineage_unverified: "ROE 披露来源无法核验",
  screen_job_failed: "午夜筛选任务执行失败",
  screen_job_running: "午夜筛选任务正在运行",
  no_reviewable_candidates: "没有可供人工复核的证券",
  inactive_or_delisted: "非活跃或已退市",
  listing_unverified: "上市日期无法核对",
  industry_missing: "行业缺失",
  industry_source_conflict: "行业来源不一致",
  financial_industry_separate_model: "金融行业需专用模型",
  trusted_quote_missing: "可信报价缺失",
  untrusted_quote: "报价未通过可信门槛",
  suspended: "停牌",
  treatment_unknown_or_restricted: "特殊处理状态未知或受限",
  liquidity_missing: "成交量额缺失",
  valuation_missing: "估值快照缺失",
  valuation_invalid_or_nonpositive: "PE/PB 缺失或非正",
  vendor_close_mismatch: "供应商报价与可信日线差异超 0.01 元",
  valuation_lineage_unverified: "估值原始批次未核验",
  roe_reported_missing: "披露 ROE 缺失",
  roe_report_period_not_comparable: "ROE 报告期不同，需单独复核",
};
function optionalNumber(value: string | number): number | null {
  const input = String(value).trim();
  if (!input) return null;
  const parsed = Number(input);
  return Number.isFinite(parsed) ? parsed : null;
}
const visible = computed(() => {
  const rows = screen.value?.rows ?? [];
  const upperPe = optionalNumber(maxPe.value);
  const upperPb = optionalNumber(maxPb.value);
  const roePercent = optionalNumber(minRoe.value);
  const lowerRoe = roePercent === null ? null : roePercent / 100;
  return rows.filter((row) => {
    const roeValue = row.roe_reported_vendor;
    if (!showAll.value && row.status !== "ready_for_review") return false;
    if (upperPe !== null && (!row.pe_vendor_unverified || Number(row.pe_vendor_unverified) <= 0 || Number(row.pe_vendor_unverified) > upperPe)) return false;
    if (upperPb !== null && (!row.pb_vendor || Number(row.pb_vendor) <= 0 || Number(row.pb_vendor) > upperPb)) return false;
    if (lowerRoe !== null && (
      roeValue === null
      || row.roe_report_date !== screen.value?.roe_report_dates?.[0]
      || Number(roeValue) < lowerRoe
    )) return false;
    return true;
  });
});
const primaryReasons = computed(() =>
  (screen.value?.global_reason_codes ?? []).map(code => reasonNames[code] ?? code)
);
const nextStep = computed(() => {
  const sync = screen.value?.latest_daily_sync;
  if (sync?.status === "failed") return "日线同步失败：先检查自动同步任务，再等当晚 21:00 重新采集估值与 ROE。";
  if (sync?.status === "running") return "日线同步正在运行。完成后还需当晚 21:00 的估值与 ROE 采集。";
  if (screen.value?.global_reason_codes.includes("input_preparation_missing_for_target")) return "目标日当晚没有完成采集，不能用事后数据补成当晚名单。下一交易日 21:00 会尝试采集新一轮，午夜后查看结果。";
  return "逐项检查下方覆盖情况与原始批次，再刷新报告。";
});
const pageRows = computed(() => visible.value.slice((page.value - 1) * pageSize, page.value * pageSize));
const blockedTitle = computed(() => {
  const codes = screen.value?.global_reason_codes ?? [];
  if (codes.includes("screen_job_running")) return "筛选还在运行";
  if (codes.includes("no_reviewable_candidates")) return "本次 0 只通过数据与适用性门槛";
  if (codes.includes("screen_job_failed")) return "自动筛选没有完成";
  return "数据还没准备齐，暂时不能筛选";
});
const blockedDescription = computed(() => {
  const codes = screen.value?.global_reason_codes ?? [];
  if (codes.includes("screen_job_running")) return "任务完成后刷新报告查看名单。";
  if (codes.includes("no_reviewable_candidates")) return "每只股票都有待核查的数据或适用性限制；下方仍展示带缺口标记的原始指标供人工核查，这不代表市场上没有投资机会。";
  if (codes.includes("screen_job_failed")) return "自动任务未完成，当前名单不可用。请查看下方任务信息，并在问题解决后刷新报告。";
  return "目标交易日的估值或盈利能力数据尚未备齐，当前名单不可用。请查看下方缺口和下一步；这不是股票未达标的结论。";
});
function canBrowseRows(report: PriceValueScreenReport | null): boolean {
  return !!report && report.rows.length > 0 && report.global_reason_codes.every(code => code === "no_reviewable_candidates");
}
const hasResearchRows = computed(() => canBrowseRows(screen.value));
const hasFilters = computed(() => [maxPe.value, maxPb.value, minRoe.value].some(value => String(value).trim() !== ""));
function clearFilters() { maxPe.value = ""; maxPb.value = ""; minRoe.value = ""; page.value = 1; }
function displayTime(value: string): string {
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString("zh-CN", { timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
}
function decimal(value: string | null, digits = 2) {
  return value === null ? "—" : Number(value).toLocaleString("zh-CN", { maximumFractionDigits: digits });
}
function percentage(value: string | null) {
  return value === null ? "—" : Number(value).toLocaleString("zh-CN", { style: "percent", maximumFractionDigits: 2 });
}
function displayedRoe(row: PriceValueScreenReport["rows"][number]) {
  return row.roe_reported_vendor;
}
async function refresh() {
  controller?.abort();
  const current = new AbortController();
  controller = current;
  loading.value = true; error.value = "";
  try {
    const report = await fetchPriceValueScreen(current.signal);
    screen.value = report;
    showAll.value = canBrowseRows(report) && report?.ready_count === 0;
    page.value = 1;
  } catch (cause) {
    if (!current.signal.aborted) {
      screen.value = null;
      error.value = cause instanceof Error ? cause.message : "读取失败";
    }
  } finally {
    if (!current.signal.aborted) loading.value = false;
  }
}
function download() {
  if (!screen.value) return;
  const url = URL.createObjectURL(new Blob([JSON.stringify(screen.value, null, 2)], { type: "application/json" }));
  const anchor = document.createElement("a"); anchor.href = url; anchor.download = `price-value-screen-${screen.value.target_trade_date ?? "unavailable"}.json`; anchor.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
onMounted(refresh);
onUnmounted(() => controller?.abort());
</script>

<template>
  <section class="screen-panel">
    <div class="heading"><div><p class="eyebrow">第一步 · 自动进行</p><h2>找待研究的股票</h2><p>系统每天北京时间 00:00 尝试扫描上一已完成交易日；只读 PE、PB 和财报披露的 ROE，供你挑选需要人工复核的股票。</p></div><button type="button" @click="refresh" :disabled="loading">{{ loading ? "读取中…" : "刷新报告" }}</button></div>
    <p v-if="error" class="error" role="alert">{{ error }}</p>
    <div v-if="!screen && !loading && !error" class="empty-state"><strong>还没有初筛报告</strong><p>服务启动后会补跑一次。之后交易日 21:00 准备输入、每日 00:00 尝试扫描；请稍后刷新查看。</p></div>
    <p v-if="!screen && loading" role="status">正在读取最新报告…</p>
    <template v-if="screen">
      <div class="status" :class="screen.status" role="status"><strong>{{ screen.status === "blocked" ? blockedTitle : screen.status === "partial" ? "部分股票可供人工复核" : "本次筛选数据已覆盖" }}</strong><p v-if="screen.status === 'blocked'">{{ blockedDescription }}</p><p v-else>名单仅是研究起点，仍需核查业务、财报与数据时点。</p><small>目标交易日：{{ screen.target_trade_date ?? "未确定" }} · 报告时间：{{ displayTime(screen.generated_at) }}（北京时间）</small></div>
      <dl class="summary"><div><dt>本次证券池</dt><dd>{{ screen.universe_count.toLocaleString('zh-CN') }}</dd></div><div><dt>可人工复核</dt><dd>{{ screen.ready_count.toLocaleString('zh-CN') }}</dd></div><div><dt>数据不足或需单独复核</dt><dd>{{ screen.needs_review_count.toLocaleString('zh-CN') }}</dd></div></dl>
      <div v-if="screen.status === 'blocked'" class="action-panel"><strong>目前缺什么</strong><p>{{ primaryReasons.join('、') || '部分证券资料仍需核对' }}</p><p v-if="topRowReasons.length">主要逐股缺口：{{ topRowReasons.map(([code, count]) => `${reasonNames[code] ?? code} ${count.toLocaleString('zh-CN')} 只`).join('；') }}</p><strong>接下来</strong><p>{{ nextStep }}</p><p v-if="screen.latest_daily_sync">最近日线同步 #{{ screen.latest_daily_sync.job_id }}：{{ screen.latest_daily_sync.status === 'success' ? '成功' : screen.latest_daily_sync.status === 'failed' ? '失败' : '进行中' }}<span v-if="screen.latest_daily_sync.error_summary"> · {{ screen.latest_daily_sync.error_summary }}</span></p><p v-if="screen.historical_raw_archive">目标日历史估值已归档 {{ screen.historical_raw_archive.supplier_total.toLocaleString('zh-CN') }} 条，实际抓取于 {{ displayTime(screen.historical_raw_archive.observed_at_utc) }}；尚未验证当时可得，不能补成当日筛选或回测输入。</p></div>
      <div class="coverage-panel"><strong>数据覆盖（按交易所）</strong><div class="table-wrap"><table><thead><tr><th>交易所</th><th>证券</th><th>可信日线</th><th>同价估值</th><th>有效 PE/PB</th><th>披露 ROE</th><th>上市日期</th><th>已核行业</th><th>ST 状态已知</th></tr></thead><tbody><tr v-for="(coverage, exchange) in screen.current_coverage_by_exchange" :key="exchange"><td>{{ exchange }}</td><td>{{ coverage.securities }}</td><td>{{ coverage.trusted_bars }}</td><td>{{ coverage.valuation }}</td><td>{{ coverage.valid_pe_pb ?? 0 }}</td><td>{{ coverage.reported_roe }}</td><td>{{ coverage.listing_date }}</td><td>{{ coverage.industry }}</td><td>{{ coverage.treatment_known }}</td></tr></tbody></table></div></div>
      <label v-if="hasResearchRows" class="check"><input v-model="showAll" type="checkbox" @change="page = 1" /> 查看每只股票的数据和缺口</label>
      <div class="explain"><strong>这三个指标怎么看？</strong><p>PE 是价格与盈利之比，PB 是价格与净资产之比；ROE 显示财报披露的加权平均值。ROE 下限只比较主报告期；若有更新财报，该行同时显示更新值，请人工核对；PE 周期待核，指标均需人工复核，不构成买入建议。</p></div>
      <details class="diagnostics"><summary>查看完整原因、原始记录与研究限制</summary><p v-if="screen.error_message" class="error">任务信息：{{ screen.error_message }}</p><ul v-if="Object.keys(screen.reason_counts).length"><li v-for="(count, code) in screen.reason_counts" :key="code">{{ reasonNames[code] ?? code }}：{{ count }}</li></ul><p v-for="item in screen.limitations" :key="item">{{ item }}</p><p>供应商：{{ screen.valuation_source }} · 返回 {{ screen.supplier_total ?? "—" }} 条 · 同日收盘匹配 {{ screen.valuation_accepted_count ?? "—" }} 条 · 披露 ROE {{ screen.roe_reported_count ?? "—" }} 条</p><p v-for="(coverage, exchange) in screen.exchange_coverage ?? {}" :key="exchange">{{ exchange }}：证券 {{ coverage.active_stocks }} · 可信日线 {{ coverage.trusted_bars }} · 供应商映射 {{ coverage.vendor_mapped }} · 同价 {{ coverage.close_matched }} · 披露 ROE {{ coverage.reported_roe ?? 0 }}</p><p>原始批次：{{ screen.input_batch_id ?? "未完成" }} · 输入指纹：{{ screen.snapshot_hash }}</p><button type="button" class="secondary" @click="download">导出完整报告 JSON</button></details>
      <div v-if="hasResearchRows && (screen.ready_count > 0 || showAll)" class="list-section"><div class="list-heading"><div><h3>{{ showAll ? "全市场原始记录" : "可人工复核名单" }}</h3><p>下面的条件只过滤当前页面，不产生选股结论或交易信号。</p></div><span>{{ visible.length.toLocaleString('zh-CN') }} 只</span></div><div class="filters"><label>PE 上限（留空不限）<input v-model="maxPe" type="number" min="0" step="any" @input="page = 1" /></label><label>PB 上限（留空不限）<input v-model="maxPb" type="number" min="0" step="any" @input="page = 1" /></label><label>ROE 下限（%，仅 {{ screen.roe_report_dates?.[0] ?? '待确定' }} 报告期）<input v-model="minRoe" type="number" step="any" @input="page = 1" /></label><button v-if="hasFilters" type="button" class="secondary" @click="clearFilters">清除条件</button></div><div v-if="visible.length" class="table-wrap"><table><thead><tr><th>证券</th><th>行业</th><th>收盘价（元）</th><th>PE（周期待核）</th><th>PB</th><th>主报告期 ROE（披露加权平均）</th><th>报告期</th><th>状态／原因</th></tr></thead><tbody><tr v-for="row in pageRows" :key="row.symbol"><td>{{ row.symbol }}<br />{{ row.name }}</td><td>{{ row.industry ?? row.vendor_industry_hint ?? "—" }}<small v-if="!row.industry && row.vendor_industry_hint">供应商分类，待核</small></td><td>{{ decimal(row.close) }}</td><td>{{ decimal(row.pe_vendor_unverified) }}</td><td>{{ decimal(row.pb_vendor) }}</td><td>{{ percentage(displayedRoe(row)) }}<small v-if="row.roe_newer_report_date">更新报告 {{ row.roe_newer_report_date }}：{{ percentage(row.roe_newer_reported_vendor) }}，请同时复核</small></td><td>{{ row.roe_report_date ?? "—" }}</td><td>{{ row.status === "ready_for_review" ? "待人工复核" : row.reason_codes.map(code => reasonNames[code] ?? code).join("、") }}</td></tr></tbody></table></div><p v-else class="empty-state">{{ hasFilters ? "当前条件没有匹配股票，可以清除条件再看。" : "本次没有可展示的股票记录。" }}</p><div v-if="visible.length" class="pagination"><span>第 {{ page }} 页 / {{ Math.ceil(visible.length / pageSize) }} 页</span><button type="button" :disabled="page <= 1" @click="page--">上一页</button><button type="button" :disabled="page * pageSize >= visible.length" @click="page++">下一页</button></div></div>
    </template>
  </section>
</template>

<style scoped>
.screen-panel { border: 1px solid #d8e0dd; border-radius: 14px; padding: 22px; background: white; min-width: 0; }
h2,h3,p { margin: 0 0 10px; } h2 { font-size: 23px; } h3 { font-size: 18px; }
.eyebrow { color: #326b58; font-size: 12px; font-weight: 700; }
.heading,.pagination,.list-heading { display: flex; justify-content: space-between; align-items: center; gap: 12px; flex-wrap: wrap; }
.heading p:not(.eyebrow) { max-width: 690px; color: #56695d; line-height: 1.55; }
button { border: 0; border-radius: 8px; padding: 10px 15px; background: #24694f; color: white; font: inherit; cursor: pointer; } button:disabled { opacity: .5; cursor: default; }
button.secondary { margin-top: 10px; border: 1px solid #b5cec0; background: white; color: #24694f; }
.status { display: block; max-width: none; margin: 15px 0; padding: 18px; border-radius: 10px; background: #ecf5ef; color: #33483c; font-size: 14px; font-weight: 400; line-height: 1.55; }
.status.blocked { border-left: 4px solid #bd7446; background: #fff1e6; } .status.partial { background: #fff8df; }
.status strong { display: block; margin-bottom: 6px; font-size: 18px; } .status p { line-height: 1.55; } small { color: #61736a; }
.error { color: #a0282a; } .summary { display: grid; grid-template-columns: repeat(3,minmax(0,1fr)); gap: 12px; margin: 0 0 15px; }
.summary div { background: #f4f8f6; border-radius: 9px; padding: 15px; } .summary dt { color: #52645b; font-size: 13px; } .summary dd { margin: 6px 0 0; font-size: 23px; font-weight: 700; }
.explain { padding: 14px 16px; border-radius: 9px; background: #f3f6fa; color: #3d5364; } .explain p { margin: 5px 0 0; font-size: 13px; line-height: 1.6; }
.action-panel { margin: 0 0 15px; padding: 14px 16px; border: 1px solid #edc6a8; border-radius: 9px; background: #fffaf6; font-size: 13px; line-height: 1.6; overflow-wrap: anywhere; }
.action-panel strong { display: block; margin-top: 7px; } .action-panel strong:first-child { margin-top: 0; }
.coverage-panel { margin: 0 0 15px; padding: 14px 16px; border: 1px solid #d8e0dd; border-radius: 9px; font-size: 13px; }
.coverage-panel strong { display: block; margin-bottom: 8px; }
.diagnostics { margin-top: 16px; color: #52645b; font-size: 13px; } .diagnostics p { overflow-wrap: anywhere; } .diagnostics summary { font-weight: 700; }
.diagnostics ul { padding-left: 20px; } .check { display: flex; align-items: center; gap: 8px; margin-top: 12px; } .check input { width: auto; }
.list-section { margin-top: 24px; border-top: 1px solid #e1e8e3; padding-top: 20px; } .list-heading p { color: #61736a; font-size: 13px; } .list-heading span { color: #24694f; font-weight: 700; }
.filters { display: flex; gap: 14px; flex-wrap: wrap; align-items: end; margin: 16px 0; } label { display: grid; gap: 6px; font-size: 13px; } input { border: 1px solid #becbc5; border-radius: 7px; padding: 9px; font: inherit; max-width: 140px; }
.table-wrap { overflow-x: auto; } table { border-collapse: collapse; width: 100%; font-size: 13px; } th,td { text-align: left; padding: 10px; border-bottom: 1px solid #e1e8e3; vertical-align: top; } th { white-space: nowrap; }
.table-wrap td small { display: block; margin-top: 3px; }
.empty-state { padding: 20px; border-radius: 9px; background: #f4f8f6; } .empty-state p { margin: 6px 0 0; } .pagination { justify-content: flex-end; margin-top: 13px; }
@media(max-width: 900px) { .summary { grid-template-columns: 1fr; } }
</style>
