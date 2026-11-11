<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import {
  fetchPriceValueScreen,
  fetchPriceValueTracking,
  type PriceValueForwardTracking,
  type PriceValueScreenReport,
} from "../../priceValueScreenApi";

const screen = ref<PriceValueScreenReport | null>(null);
const tracking = ref<PriceValueForwardTracking | null>(null);
const trackingError = ref("");
const loading = ref(false);
const error = ref("");
const maxPe = ref<string | number>("");
const maxPb = ref<string | number>("");
const minRoe = ref<string | number>("");
const topRowReasons = computed(() => Object.entries(screen.value?.reason_counts ?? {})
  .filter(([code]) => code !== "no_reviewable_candidates")
  .sort((left, right) => right[1] - left[1]).slice(0, 3));
const showAll = ref(false);
const viewMode = ref<"shortlist" | "qualified" | "research" | "base">("shortlist");
const page = ref(1);
const pageSize = 30;
let controller: AbortController | null = null;
const reasonNames: Record<string, string> = {
  shortlist_base_inputs_not_ready: "未通过基础数据准入",
  shortlist_research_conditions_not_met: "未通过原走势与现金流条件",
  shortlist_industry_missing: "行业缺失，不能核验行业数量限制",
  shortlist_pe_outside_range: "供应商 PE 未满足 0 < PE ≤ 20",
  shortlist_pb_outside_range: "PB 未满足 0 < PB ≤ 2",
  shortlist_roe_abnormal_review: "主报告期 ROE 超过 30%，需核查异常因素",
  shortlist_newer_roe_period_missing: "更新 ROE 缺少报告期",
  shortlist_trend_overheated_or_inconsistent: "高于均线超过 8% 或走势条件不一致",
  shortlist_net_income_ttm_nonpositive: "净利润 TTM 不为正，未通过精选",
  shortlist_net_income_ttm_missing_or_invalid: "净利润 TTM 证据缺失或无效，待核查",
  shortlist_cash_flow_insufficient: "三年现金流／利润不足 1",
  shortlist_annual_profit_not_continuously_positive: "最近三年并非逐年盈利",
  shortlist_report_blocked: "报告数据或鲜度门槛未通过",
  shortlist_industry_capacity_deferred: "同一行业本轮已优先安排两只，留在达标池",
  shortlist_total_capacity_deferred: "本轮研究数量已达上限，留在达标池",
  shortlist_profit_years_missing_or_invalid: "逐年盈利证据缺失或无效",
  shortlist_listing_age_missing_or_invalid: "上市年限证据缺失或无效",
  shortlist_newer_roe_missing_or_invalid: "更新 ROE 证据缺失或无效",
  shortlist_newer_roe_abnormal_review: "更新报告期 ROE 超过 30%，需核查异常因素",
  shortlist_listing_age_insufficient: "上市未满两年",
  shortlist_liquidity_insufficient: "20 日平均成交额不足 5000 万元",
  shortlist_roe_nonpositive: "主报告期 ROE 不为正",
  shortlist_newer_roe_nonpositive: "更新报告期 ROE 不为正",
  shortlist_cyclical_model_required: "周期行业需专用模型复核",
  report_stale: "报告未更新至应有交易日",
  report_future: "报告交易日晚于应有交易日",
  report_freshness_unknown: "报告新鲜度无法核验",
  report_date_missing: "报告缺少有效行情日期",
  report_date_in_future: "报告行情日晚于应有日期",
  calendar_coverage_incomplete: "交易日历覆盖不足，无法核验报告日期",
  diagnostic_version_missing: "旧报告尚无新版研究诊断，请等待重跑",
  trend_calendar_incomplete: "缺少连续 21 个交易日的日历",
  trend_history_incomplete: "缺少完整的 21 日行情窗口",
  trend_symbol_mismatch: "走势行情与证券代码不一致",
  trend_qfq_required: "缺少可信前复权行情，无法判断走势",
  trend_quote_untrusted: "窗口内存在停牌、可疑或未核验行情",
  trend_weak: "20 日收益为负且低于 20 日均线",
  trend_mixed: "20 日涨跌与均线条件尚未同时通过",
  quality_financial_model_required: "金融行业需专用质量模型",
  quality_symbol_mismatch: "财务记录与证券代码不一致",
  quality_net_income_sum_3y_missing: "缺少可用的三年累计利润证据",
  quality_ocf_profit_3y_missing: "缺少可用的三年现金流／利润证据",
  quality_ocf_ttm_missing: "缺少可用的 TTM 经营现金流证据",
  quality_net_income_sum_3y_insufficient: "近三年累计利润不为正",
  quality_ocf_profit_3y_insufficient: "近三年经营现金流／利润低于 1",
  quality_ocf_ttm_insufficient: "TTM 经营现金流不为正",
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
for (const [name, label] of Object.entries({ pe: "PE", pb: "PB", roe: "ROE", amount_mean_20: "20 日平均成交额", ma20_gap: "均线偏离", ocf_profit_3y: "三年现金流／利润", net_income_min_3y: "三年逐年利润" })) {
  reasonNames[`shortlist_${name}_missing_or_invalid`] = `${label}证据缺失或无效`;
}
const shortlistNames = { selected: "优先研究", qualified_deferred: "达标，暂缓安排", excluded: "未通过精选条件", needs_review: "需核查证据或行业模型", blocked: "报告不可用" };
const trendNames = { improving: "走势条件通过", weak: "持续走弱", mixed: "走势尚未确认", unknown: "历史数据不足" };
const qualityNames = { supported: "质量条件通过", risk: "现金流或盈利风险", unknown: "质量证据不足", not_applicable: "需专用行业模型" };
const freshnessNames = { current: "已核对交易日", stale: "报告已过期", unknown: "报告新鲜度未知", future: "报告日期异常" };
const currentReportEligible = computed(() => screen.value?.candidate_eligible === true
  && screen.value.freshness?.status === "current"
  && screen.value.status !== "blocked"
  && screen.value.global_reason_codes.length === 0
  && screen.value.screen_version === "relative_value_research_v7"
  && screen.value.selection?.version === "value_research_shortlist_v2");
const unavailableReason = computed(() => {
  const report = screen.value;
  if (!report) return "还没有初筛报告。";
  if (!report.freshness) return "旧报告尚未核验交易日，不能作为当前研究名单。";
  if (report.freshness.status === "stale") return "报告尚未更新到应有交易日；历史名单不计入当前候选。";
  if (report.freshness.status === "future") return "报告交易日晚于应有交易日，需要核对报告日期和交易日历。";
  if (report.freshness.status === "unknown") return "交易日历或报告日期尚未核验，不能确认名单有效。";
  if (!report.diagnostic_version) return "旧报告没有新版走势与质量诊断，需等待重新生成；刷新页面不会重跑筛选。";
  if (report.screen_version !== "relative_value_research_v7" || report.selection?.version !== "value_research_shortlist_v2") return "旧报告没有新版精选规则与排序，不能作为当前优先研究名单；需重新生成报告。";
  if (report.status === "blocked" || report.global_reason_codes.length) return `报告数据门槛未通过：${reasonText(report.global_reason_codes)}。`;
  return "报告版本未满足当前研究规则，需要重新生成；刷新页面不会重跑筛选。";
});
const baseRows = computed(() => currentReportEligible.value
  ? (screen.value?.rows ?? []).filter(row => row.status === "ready_for_review") : []);
const researchRows = computed(() => baseRows.value.filter(row => row.research_diagnostics?.review_eligible === true));
const qualifiedRows = computed(() => researchRows.value.filter(row => row.shortlist?.qualified === true));
const shortlistRows = computed(() => {
  const rows = new Map(qualifiedRows.value.filter(row => row.shortlist?.selected === true
    && row.shortlist.status === "selected")
    .map(row => [row.symbol, row]));
  return (currentReportEligible.value ? screen.value?.selection?.ordered_symbols ?? [] : [])
    .map(symbol => rows.get(symbol)).filter((row): row is NonNullable<typeof row> => row !== undefined);
});
const viewNames = { shortlist: "优先研究名单", qualified: "全部达标池", research: "原走势与现金流条件池", base: "仅通过基础数据准入的记录" };
const poolNames = { shortlist: "优先研究名单", qualified: "全部达标池", research: "原条件池" };
const trackingPoolOrder = ["shortlist", "qualified", "research"] as const;
const trackingStatusNames = { blocked: "不可评价", pending: "等待观察", observing: "观察中", incomplete: "数据不完整", complete: "观察窗完成" };
function optionalNumber(value: string | number): number | null {
  const input = String(value).trim();
  if (!input) return null;
  const parsed = Number(input);
  return Number.isFinite(parsed) ? parsed : null;
}
const visible = computed(() => {
  const rows = showAll.value ? screen.value?.rows ?? []
    : viewMode.value === "shortlist" ? shortlistRows.value
    : viewMode.value === "qualified" ? qualifiedRows.value
    : viewMode.value === "research" ? researchRows.value : baseRows.value;
  const upperPe = optionalNumber(maxPe.value);
  const upperPb = optionalNumber(maxPb.value);
  const roePercent = optionalNumber(minRoe.value);
  const lowerRoe = roePercent === null ? null : roePercent / 100;
  return rows.filter((row) => {
    const roeValue = row.roe_reported_vendor;
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
  if (codes.includes("no_reviewable_candidates")) return "每只股票都有待核查的数据或适用性限制；本次没有合格研究名单。可以主动打开原始记录核查缺口，这不代表市场上没有投资机会。";
  if (codes.includes("screen_job_failed")) return "自动任务未完成，当前名单不可用。请查看下方任务信息，并在问题解决后刷新报告。";
  return "目标交易日的估值或盈利能力数据尚未备齐，当前名单不可用。请查看下方缺口和下一步；这不是股票未达标的结论。";
});
function canBrowseRows(report: PriceValueScreenReport | null): boolean {
  return !!report && report.rows.length > 0;
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
function metricText(value: unknown): string | null {
  return typeof value === "string" || typeof value === "number" ? String(value) : null;
}
function percentage(value: string | null) {
  return value === null ? "—" : Number(value).toLocaleString("zh-CN", { style: "percent", maximumFractionDigits: 2 });
}
function reasonText(codes: string[]) {
  return codes.map(code => reasonNames[code] ?? code).join("、");
}
function displayedRoe(row: PriceValueScreenReport["rows"][number]) {
  return row.roe_reported_vendor;
}
async function refresh() {
  controller?.abort();
  const current = new AbortController();
  controller = current;
  loading.value = true; error.value = ""; trackingError.value = "";
  try {
    const report = await fetchPriceValueScreen(current.signal);
    if (current.signal.aborted || controller !== current) return;
    screen.value = report;
    try {
      tracking.value = await fetchPriceValueTracking(current.signal);
    } catch (cause) {
      if (current.signal.aborted || controller !== current) return;
      tracking.value = null;
      trackingError.value = cause instanceof Error ? cause.message : "名单效果跟踪暂时不可用";
    }
    showAll.value = false;
    viewMode.value = "shortlist";
    page.value = 1;
  } catch (cause) {
    if (!current.signal.aborted) {
      screen.value = null;
      tracking.value = null;
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
watch([showAll, viewMode, maxPe, maxPb, minRoe], () => { page.value = 1; }, { flush: "sync" });
</script>

<template>
  <section class="screen-panel">
    <div class="heading"><div><p class="eyebrow">第一步 · 自动进行</p><h2>找待研究的股票</h2><p>系统每天北京时间 00:00 尝试扫描上一已完成交易日，核对数据后提供估值、盈利现金流与走势研究条件，供人工复核。</p></div><button type="button" @click="refresh" :disabled="loading">{{ loading ? "读取中…" : "刷新报告" }}</button></div>
    <p v-if="error" class="error" role="alert">{{ error }}</p>
    <div v-if="!screen && !loading && !error" class="empty-state"><strong>还没有初筛报告</strong><p>服务启动后会补跑一次。之后交易日 21:00 准备输入、每日 00:00 尝试扫描；请稍后刷新查看。</p></div>
    <p v-if="!screen && loading" role="status">正在读取最新报告…</p>
    <template v-if="screen">
      <div v-if="screen.observation_mode === 'current_research_catchup'" class="research-policy" role="status">
        <strong>当前补采研究观察，非当晚历史名单</strong>
        <p>行情截至：{{ screen.price_as_of ?? '未知' }} · 财务研究日期：{{ screen.financial_as_of ?? '未知' }}</p>
        <p>实际分析时间：{{ screen.analysis_at ? displayTime(screen.analysis_at) : '未知' }}（北京时间）<template v-if="screen.observed_at"> · 实际采集时间：{{ displayTime(screen.observed_at) }}（北京时间）</template></p>
        <p>使用当前已取得的财务版本研究最新完整交易日价格；财务可得性以实际分析时刻为截止，不代表这些资料在行情日当晚已可得，不能用作历史名单或历史回测认证。</p>
      </div>
      <div class="freshness" :class="{ warning: !currentReportEligible }" role="status">
        <strong>{{ screen.freshness ? freshnessNames[screen.freshness.status] : '旧报告尚未核验新鲜度' }}</strong>
        <p>报告行情日：{{ screen.target_trade_date ?? '未知' }} · 应有报告行情日：{{ screen.freshness?.expected_trade_date ?? '无法核验' }}</p>
        <p v-if="!currentReportEligible">{{ unavailableReason }} 可主动查看原始记录和缺口。</p>
        <p v-if="screen.freshness?.reason_codes.length">{{ reasonText(screen.freshness.reason_codes) }}</p>
        <small>研究规则版本：{{ screen.diagnostic_version ?? '旧报告，等待重新生成' }}<template v-if="screen.freshness"> · 核验于 {{ displayTime(screen.freshness.checked_at) }}（北京时间）</template></small>
      </div>
      <div class="status" :class="currentReportEligible ? screen.status : 'blocked'" role="status"><strong>{{ screen.status === "blocked" ? blockedTitle : !currentReportEligible ? "原报告数据状态，仅供历史核查" : screen.status === "partial" ? "部分股票通过基础数据准入" : "本次筛选数据已覆盖" }}</strong><p v-if="screen.status === 'blocked'">{{ blockedDescription }}</p><p v-else>数据准入不等于投资质量达标；请继续查看走势和盈利现金流诊断。</p><small>目标交易日：{{ screen.target_trade_date ?? "未确定" }} · 报告时间：{{ displayTime(screen.generated_at) }}（北京时间）</small></div>
      <dl class="summary"><div><dt>本次证券池</dt><dd>{{ screen.universe_count.toLocaleString('zh-CN') }}</dd><small>数据准入 {{ baseRows.length.toLocaleString('zh-CN') }} 只</small></div><div><dt>本轮优先研究</dt><dd>{{ shortlistRows.length.toLocaleString('zh-CN') }}</dd><small>达标池 {{ qualifiedRows.length.toLocaleString('zh-CN') }} 只</small></div><div><dt>原走势与现金流条件池</dt><dd>{{ researchRows.length.toLocaleString('zh-CN') }}</dd><small>原报告数据待核查 {{ screen.needs_review_count.toLocaleString('zh-CN') }} 只</small></div></dl>
      <div v-if="screen.status === 'blocked'" class="action-panel"><strong>目前缺什么</strong><p>{{ primaryReasons.join('、') || '部分证券资料仍需核对' }}</p><p v-if="topRowReasons.length">主要逐股缺口：{{ topRowReasons.map(([code, count]) => `${reasonNames[code] ?? code} ${count.toLocaleString('zh-CN')} 只`).join('；') }}</p><strong>接下来</strong><p>{{ nextStep }}</p><p v-if="screen.latest_daily_sync">最近日线同步 #{{ screen.latest_daily_sync.job_id }}：{{ screen.latest_daily_sync.status === 'success' ? '成功' : screen.latest_daily_sync.status === 'failed' ? '失败' : '进行中' }}<span v-if="screen.latest_daily_sync.error_summary"> · {{ screen.latest_daily_sync.error_summary }}</span></p><p v-if="screen.historical_raw_archive">目标日历史估值已归档 {{ screen.historical_raw_archive.supplier_total.toLocaleString('zh-CN') }} 条，实际抓取于 {{ displayTime(screen.historical_raw_archive.observed_at_utc) }}；尚未验证当时可得，不能补成当日筛选或回测输入。</p></div>
      <div class="coverage-panel"><strong>数据覆盖（按交易所）</strong><div class="table-wrap"><table><thead><tr><th>交易所</th><th>证券</th><th>可信日线</th><th>同价估值</th><th>有效 PE/PB</th><th>披露 ROE</th><th>上市日期</th><th>已核行业</th><th>ST 状态已知</th></tr></thead><tbody><tr v-for="(coverage, exchange) in screen.current_coverage_by_exchange" :key="exchange"><td>{{ exchange }}</td><td>{{ coverage.securities }}</td><td>{{ coverage.trusted_bars }}</td><td>{{ coverage.valuation }}</td><td>{{ coverage.valid_pe_pb ?? 0 }}</td><td>{{ coverage.reported_roe }}</td><td>{{ coverage.listing_date }}</td><td>{{ coverage.industry }}</td><td>{{ coverage.treatment_known }}</td></tr></tbody></table></div></div>
      <label v-if="hasResearchRows" class="check"><input v-model="showAll" type="checkbox" /> 主动查看原始记录与缺口（包含未通过、过期或不适用的数据）</label>
      <div v-if="!showAll" class="research-policy"><strong>本轮精选规则</strong><p>供应商 PE 不高于 20、PB 不高于 2；上市至少 730 天、20 日平均成交额至少 5000 万元、最近三年逐年盈利；20 日涨幅为正、收盘不低于 20 日均线且高出不超过 8%；三年经营现金流／利润不低于 1、TTM 经营现金流为正；净利润 TTM 严格为正（报表净利润，非扣非／归母），缺证据须待核查。主期与已核验更新期 ROE 均须为正，不将季度或半年值年化。周期行业、ROE 超过 30% 需另行复核。</p><p>只在全部达标后排序，优先研究最多 10 只、每个行业最多 2 只；不足时不补位。排序先比较 PE/20 + PB/2（较低优先），再比较三年现金流／利润（最高按 2）、主期 ROE（最高按 30%），最后按代码确定并列顺序。</p><p>这是安排人工研究的优先队列，达标池中未排入者并非不合格；不代表已完成估值或收益验证。</p><p v-if="viewMode === 'research'">当前查看原走势与现金流条件池，其中部分股票未通过新增估值、逐年及滚动盈利或行业条件，不计入本轮优先名单。</p><p v-if="viewMode === 'base'">当前只查看通过基础数据准入的记录，尚未要求走势、现金流及精选条件合格。</p><small>精选规则版本：{{ screen.selection?.version ?? '旧报告，等待重新生成' }}</small></div>
      <div class="explain"><strong>这三个指标怎么看？</strong><p>PE 是价格与盈利之比，PB 是价格与净资产之比；ROE 显示财报披露的加权平均值。ROE 下限只比较主报告期；若有更新财报，该行同时显示更新值，请人工核对；PE 周期待核，指标均需人工复核，不构成买入建议。</p></div>
      <details class="diagnostics"><summary>查看完整原因、原始记录与研究限制</summary><p v-if="screen.error_message" class="error">任务信息：{{ screen.error_message }}</p><ul v-if="Object.keys(screen.reason_counts).length"><li v-for="(count, code) in screen.reason_counts" :key="code">{{ reasonNames[code] ?? code }}：{{ count }}</li></ul><p v-for="item in screen.limitations" :key="item">{{ item }}</p><p>供应商：{{ screen.valuation_source }} · 返回 {{ screen.supplier_total ?? "—" }} 条 · 同日收盘匹配 {{ screen.valuation_accepted_count ?? "—" }} 条 · 披露 ROE {{ screen.roe_reported_count ?? "—" }} 条</p><p v-for="(coverage, exchange) in screen.exchange_coverage ?? {}" :key="exchange">{{ exchange }}：证券 {{ coverage.active_stocks }} · 可信日线 {{ coverage.trusted_bars }} · 供应商映射 {{ coverage.vendor_mapped }} · 同价 {{ coverage.close_matched }} · 披露 ROE {{ coverage.reported_roe ?? 0 }}</p><p>原始批次：{{ screen.input_batch_id ?? "未完成" }} · 输入指纹：{{ screen.snapshot_hash }}</p><button type="button" class="secondary" @click="download">导出完整报告 JSON</button></details>
      <details v-if="screen.selection" class="diagnostics"><summary>查看精选淘汰与容量递延原因</summary><p>达标池 {{ screen.selection.qualified_count }} 只；优先研究 {{ screen.selection.shortlist_count }} 只。以下是报告生成时的统计，过期报告不代表当前候选。</p><ul><li v-for="(count, code) in screen.selection.reason_counts" :key="code">{{ reasonNames[code] ?? code }}：{{ count }}</li></ul><p>{{ screen.selection.ranking_description }}</p></details>
      <section v-if="tracking" class="tracking-panel" aria-label="冻结名单前向观察">
        <div class="list-heading"><div><p class="eyebrow">冻结名单 · 前向观察</p><h3>名单效果跟踪</h3></div><strong>{{ trackingStatusNames[tracking.status] }}</strong></div>
        <p>名单快照 {{ tracking.snapshot_hash?.slice(0, 16) ?? '待生成' }} · 形成截止日 {{ tracking.formation_cutoff ?? '—' }} · 首个观察收盘日 {{ tracking.entry_date ?? '等待后续交易日' }}</p>
        <p>{{ tracking.observation_mode === 'current_research_catchup' ? '当前补采观察单独评价，不与当晚名单合并。' : '当晚冻结名单。' }} 基准为原条件池等权观察；池内成员形成后不补入、不再平衡。</p>
        <p v-if="tracking.cost_policy">含成本观察统一扣除 {{ percentage(tracking.cost_policy.round_trip_cost_rate) }}；{{ tracking.cost_policy.description }}</p>
        <div v-if="tracking.windows?.length" class="table-wrap"><table>
          <thead><tr><th>观察窗</th><th>池</th><th>状态</th><th>原始／可观察</th><th>覆盖率</th><th>毛收益</th><th>含成本观察</th><th>相对原池</th><th>最大成员跌幅</th><th>缺口</th></tr></thead>
          <tbody v-for="window in tracking.windows" :key="window.trading_days">
            <tr v-if="window.status === 'pending'"><td>{{ window.trading_days }} 个交易日</td><td colspan="9">等待观察窗完成，不以 0% 代替未知结果。</td></tr>
            <tr v-for="pool in trackingPoolOrder" v-else :key="`${window.trading_days}-${pool}`">
              <td>{{ window.trading_days }} 个交易日<br><small>截至 {{ window.end_date }}</small></td><td>{{ poolNames[pool] }}</td>
              <td>{{ window.pools[pool].status === 'complete' ? '完整' : '不完整' }}</td>
              <td>{{ window.pools[pool].member_count }}／{{ window.pools[pool].observed_count }}</td>
              <td>{{ percentage(window.pools[pool].coverage_rate) }}</td><td>{{ percentage(window.pools[pool].gross_return_observed) }}</td>
              <td>{{ percentage(window.pools[pool].net_return_observed) }}</td><td>{{ percentage(window.pools[pool].excess_return_vs_research_pool ?? null) }}</td>
              <td>{{ percentage(window.pools[pool].maximum_member_loss) }}</td>
              <td><span v-if="window.pools[pool].missing_count">缺 {{ window.pools[pool].missing_count }}：{{ Object.entries(window.pools[pool].missing_reason_counts).map(([key, value]) => `${key} ${value}`).join('、') }}</span><span v-else>—</span></td>
            </tr>
          </tbody>
        </table></div>
        <p v-else>{{ tracking.reason_codes.includes('entry_session_pending') ? '尚未到首个可观察交易日。' : '冻结名单尚不可评价，请查看原因：' + tracking.reason_codes.join('、') }}</p>
        <small>这是冻结名单的前向研究观察，不是买入建议、真实组合收益、历史回测或策略认证。缺行情成员保留在原始样本数中。</small>
      </section>
      <p v-else-if="trackingError" class="tracking-warning" role="status">主筛选报告已加载；{{ trackingError }}，不影响当前名单核查。</p>
      <div class="list-section">
        <div class="list-heading"><div><h3>{{ showAll ? '原始记录与缺口（不计入候选）' : viewNames[viewMode] }}</h3><p>{{ !showAll && viewMode === 'shortlist' ? '按后台研究优先顺序展示；个人过滤只缩小当前名单，不会重新排序或补入其他股票。' : '按证券代码查看原始条件与缺口；个人过滤不会改变后台精选结果。' }}</p></div><span>{{ visible.length.toLocaleString('zh-CN') }} 只</span></div>
        <label v-if="!showAll && currentReportEligible" class="view-selector">查看范围<select v-model="viewMode"><option value="shortlist">优先研究名单（最多 10 只）</option><option value="qualified">全部达标池</option><option value="research">原走势与现金流条件池</option><option value="base">仅通过基础数据准入的记录</option></select></label>
        <p v-if="showAll" class="raw-warning" role="status">原始记录包含未通过门槛的股票；过期报告只反映历史数据。筛选条件不会把这些记录变成合格候选。</p>
        <div class="filters">
          <label>PE 上限（留空不限）<input v-model="maxPe" type="number" min="0" step="any" /></label>
          <label>PB 上限（留空不限）<input v-model="maxPb" type="number" min="0" step="any" /></label>
          <label>ROE 下限（%，仅 {{ screen.roe_report_dates?.[0] ?? '待确定' }} 报告期）<input v-model="minRoe" type="number" step="any" /></label>
          <button v-if="hasFilters" type="button" class="secondary" @click="clearFilters">清除估值条件</button>
        </div>
        <div v-if="visible.length" class="table-wrap"><table>
          <thead><tr><th>证券／研究顺序</th><th>行业</th><th>收盘价（元）</th><th>PE（周期待核）</th><th>PB</th><th>披露 ROE／报告期</th><th>走势研究诊断</th><th>盈利现金流诊断</th><th>精选状态／原因</th><th>数据状态／原因</th></tr></thead>
          <tbody><tr v-for="row in pageRows" :key="row.symbol">
            <td>{{ row.symbol }}<br />{{ row.name }}<small v-if="row.shortlist?.selected">达标池排序 #{{ row.shortlist.rank }}</small></td>
            <td>{{ row.industry ?? row.vendor_industry_hint ?? '—' }}<small v-if="!row.industry && row.vendor_industry_hint">供应商分类，待核</small></td>
            <td>{{ decimal(row.close) }}</td><td>{{ decimal(row.pe_vendor_unverified) }}</td><td>{{ decimal(row.pb_vendor) }}</td>
            <td>{{ percentage(displayedRoe(row)) }}<small>{{ row.roe_report_date ?? '—' }}</small><small v-if="row.roe_newer_report_date">更新报告 {{ row.roe_newer_report_date }}：{{ percentage(row.roe_newer_reported_vendor) }}，请同时复核</small></td>
            <td><template v-if="row.research_diagnostics">
              <strong>{{ trendNames[row.research_diagnostics.trend.status] }}</strong>
              <small>20 日涨跌：{{ percentage(row.research_diagnostics.trend.return_20) }}</small>
              <small>较 MA20：{{ percentage(row.research_diagnostics.trend.ma20_gap) }}</small>
              <details><summary>原因与来源</summary><p>{{ reasonText(row.research_diagnostics.trend.reason_codes) || '本轮走势条件通过' }}</p><p v-for="source in row.research_diagnostics.trend.source_refs" :key="source">{{ source }}</p><small>{{ row.research_diagnostics.version }}</small></details>
            </template><span v-else>旧报告，无走势诊断</span></td>
            <td><template v-if="row.research_diagnostics">
              <strong>{{ qualityNames[row.research_diagnostics.quality.status] }}</strong>
              <small>三年现金流／利润：{{ decimal(row.research_diagnostics.quality.ocf_profit_3y) }}</small>
              <details><summary>证据与缺口</summary><p>三年累计利润：{{ decimal(row.research_diagnostics.quality.net_income_sum_3y) }} 元</p><p>TTM 经营现金流：{{ decimal(row.research_diagnostics.quality.ocf_ttm) }} 元</p><p>{{ reasonText(row.research_diagnostics.quality.reason_codes) || '本轮质量条件通过' }}</p><p v-for="source in row.research_diagnostics.quality.source_refs" :key="source">{{ source }}</p></details>
            </template><span v-else>旧报告，无质量诊断</span></td>
            <td><template v-if="row.shortlist"><strong>{{ shortlistNames[row.shortlist.status] }}</strong><small v-if="row.shortlist.selected">估值、逐年及滚动盈利、流动性等精选条件通过；不代表已完成投资估值。</small><small>20 日均成交额：{{ decimal(metricText(row.shortlist.metrics.amount_mean_20)) }} 元</small><small>净利润 TTM（报表净利润，非扣非／归母）：{{ decimal(metricText(row.shortlist.metrics.net_income_ttm)) }} 元</small><small>原质量诊断仅表示三年利润与现金流条件通过；TTM 净利润另作精选检查。</small><small>三年最低年度利润：{{ decimal(metricText(row.shortlist.metrics.net_income_min_3y)) }} 元</small><small>{{ reasonText(row.shortlist.reason_codes) }}</small></template><small v-else>旧报告，无新版精选结果</small></td>
            <td>{{ row.status === 'ready_for_review' ? '基础数据准入通过' : reasonText(row.reason_codes) }}<small v-if="row.research_diagnostics">{{ row.research_diagnostics.review_eligible ? '研究条件通过' : '研究条件未通过或证据不足' }}</small></td>
          </tr></tbody>
        </table></div>
        <p v-else class="empty-state">{{ !showAll && !currentReportEligible ? unavailableReason : hasFilters ? '当前过滤条件没有匹配股票，可以清除个人过滤再看；后台精选门槛和名单保持不变。' : '本次所选范围没有股票。可以主动切换达标池或原条件池核查原因；缺数据或条件未通过的股票不会自动补入。' }}</p>
        <div v-if="visible.length" class="pagination"><span>第 {{ page }} 页 / {{ Math.ceil(visible.length / pageSize) }} 页</span><button type="button" :disabled="page <= 1" @click="page--">上一页</button><button type="button" :disabled="page * pageSize >= visible.length" @click="page++">下一页</button></div>
      </div>
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
.filters { display: flex; gap: 14px; flex-wrap: wrap; align-items: end; margin: 16px 0; } label { display: grid; gap: 6px; font-size: 13px; } input,select { border: 1px solid #becbc5; border-radius: 7px; padding: 9px; font: inherit; max-width: 260px; } .view-selector { margin: 14px 0; }
.table-wrap { overflow-x: auto; } table { border-collapse: collapse; width: 100%; font-size: 13px; } th,td { text-align: left; padding: 10px; border-bottom: 1px solid #e1e8e3; vertical-align: top; } th { white-space: nowrap; }
.table-wrap td small { display: block; margin-top: 3px; }
.freshness,.research-policy,.raw-warning { margin: 14px 0; padding: 14px 16px; border-radius: 9px; background: #edf5f0; font-size: 13px; line-height: 1.6; }
.freshness.warning,.raw-warning { background: #fff1e6; border-left: 4px solid #bd7446; }
.research-policy { background: #f3f6fa; }
.tracking-panel { margin: 18px 0; padding: 16px; border: 1px solid #c9d9d1; border-radius: 10px; background: #f8fbf9; font-size: 13px; line-height: 1.55; }
.tracking-warning { margin: 18px 0; padding: 14px 16px; border-left: 4px solid #bd7446; border-radius: 9px; background: #fff1e6; font-size: 13px; }
.tracking-panel .table-wrap { margin-top: 12px; }
.table-wrap details { margin-top: 8px; min-width: 150px; max-width: 280px; font-size: 12px; overflow-wrap: anywhere; }
.table-wrap details summary { cursor: pointer; }
.empty-state { padding: 20px; border-radius: 9px; background: #f4f8f6; } .empty-state p { margin: 6px 0 0; } .pagination { justify-content: flex-end; margin-top: 13px; }
@media(max-width: 900px) { .summary { grid-template-columns: 1fr; } }
</style>
