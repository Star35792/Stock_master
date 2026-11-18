<script setup lang="ts">
import {
  AlertTriangle,
  ArrowRight,
  BookOpen,
  BriefcaseBusiness,
  FlaskConical,
  Layers3,
  RefreshCw,
  ShieldAlert,
  Wallet,
} from "@lucide/vue";
import { computed, onBeforeUnmount, onMounted, watch } from "vue";
import { useRoute, useRouter } from "vue-router";

import { createResearchWorkbench } from "../composables/researchWorkbench";
import type { ResearchRunSummary } from "../researchWorkflowApi";
import { useResearchStore } from "../stores/research";
import EquityChart from "./components/EquityChart.vue";
import DividendScenarioPanel from "./components/DividendScenarioPanel.vue";

const route = useRoute();
const router = useRouter();
const researchStore = useResearchStore();
const state = createResearchWorkbench();

const {
  workbench,
  pipeline,
  selectedRunId,
  workbenchLoading,
  pipelineLoading,
  workbenchError,
  pipelineError,
  sectionErrors,
  source,
  loadWorkbench,
  loadPipeline,
  clearPipeline,
  dispose,
} = state;

const sourceLabel = computed(() => ({
  loading: "正在读取研究上下文",
  api: "聚合 API 已连接",
  partial: "部分研究区块不可用",
  error: "研究聚合不可用",
})[source.value]);

const generatedAt = computed(() => formatDateTime(workbench.value?.alerts?.generated_at ?? null));
const criticalAlerts = computed(() => workbench.value?.alerts?.items.filter((item) => item.severity === "critical").length ?? 0);
const pipelineRun = computed(() => pipeline.value?.run ?? null);

function readQueryRunId(value: unknown): number | null {
  const raw = Array.isArray(value) ? value[0] : value;
  if (raw === undefined || raw === null || raw === "") return null;
  const parsed = Number(raw);
  return Number.isSafeInteger(parsed) && parsed > 0 ? parsed : Number.NaN;
}

function formatDateTime(value: string | null): string {
  if (!value) return "尚未生成";
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString("zh-CN", { hour12: false });
}

function numberText(value: string | number | null | undefined, digits = 2): string {
  if (value === null || value === undefined) return "—";
  const parsed = Number(value);
  return Number.isFinite(parsed)
    ? parsed.toLocaleString("zh-CN", { maximumFractionDigits: digits })
    : "—";
}

function percent(value: string | number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  const parsed = Number(value);
  return Number.isFinite(parsed)
    ? parsed.toLocaleString("zh-CN", { style: "percent", maximumFractionDigits: 2 })
    : "—";
}

function runStatusLabel(status: string): string {
  return ({ success: "成功", failed: "失败", running: "运行中", pending: "等待中" } as Record<string, string>)[status] ?? status;
}

async function selectRun(run: ResearchRunSummary) {
  researchStore.setLastRun(run.id, run.name);
  const current = readQueryRunId(route.query.run_id);
  if (current === run.id) {
    await loadPipeline(run.id);
    return;
  }
  await router.replace({ name: "research", query: { ...route.query, run_id: String(run.id) } });
}

async function closePipeline() {
  const query = { ...route.query };
  delete query.run_id;
  clearPipeline();
  await router.replace({ name: "research", query });
}

async function refresh() {
  const runId = selectedRunId.value;
  await Promise.all([loadWorkbench(), runId ? loadPipeline(runId) : Promise.resolve()]);
}

async function continueToPortfolio() {
  const run = pipelineRun.value;
  if (!run) return;
  researchStore.setLastRun(run.id, run.name);
  researchStore.setBenchmark(pipeline.value?.benchmark?.benchmark ?? null);
  await router.push({ name: "portfolio" });
}

watch(
  () => route.query.run_id,
  (value) => {
    const runId = readQueryRunId(value);
    if (runId === null) {
      clearPipeline();
      return;
    }
    if (Number.isNaN(runId)) {
      void loadPipeline(Number.NaN);
      return;
    }
    void loadPipeline(runId);
  },
  { immediate: true },
);

watch(pipelineRun, (run) => {
  if (!run) return;
  researchStore.setLastRun(run.id, run.name);
  researchStore.setBenchmark(pipeline.value?.benchmark?.benchmark ?? null);
});

onMounted(() => { void loadWorkbench(); });
onBeforeUnmount(dispose);
</script>

<template>
  <main class="research-page">
    <header class="research-hero">
      <div>
        <span class="research-eyebrow"><Layers3 :size="15" />RESEARCH WORKBENCH</span>
        <h1>研究主线</h1>
        <p>把最近回测、组合草案、持仓账本和告警放回同一条上下文；所有结果只用于研究与持仓推演。</p>
      </div>
      <div class="hero-actions">
        <span class="readonly-pill">只读聚合 · 不产生订单</span>
        <button class="refresh-button" type="button" :disabled="workbenchLoading || pipelineLoading" @click="refresh">
          <RefreshCw :class="{ spin: workbenchLoading || pipelineLoading }" :size="16" />
          刷新
        </button>
      </div>
    </header>

    <section class="research-status" :class="`is-${source}`" aria-live="polite">
      <strong>{{ sourceLabel }}</strong>
      <span>告警生成时间：{{ generatedAt }}</span>
      <span v-if="sectionErrors.length">缺失区块：{{ sectionErrors.join("、") }}</span>
      <span v-if="workbenchError" class="status-error">{{ workbenchError }}</span>
    </section>

    <section class="workbench-grid" aria-label="研究工作台摘要">
      <article class="research-panel runs-panel">
        <header class="panel-heading">
          <div><FlaskConical :size="19" /><div><span>最近回测</span><h2>从研究结果继续</h2></div></div>
          <RouterLink to="/backtests">全部回测 <ArrowRight :size="14" /></RouterLink>
        </header>
        <p v-if="workbench?.recent_backtests_error" class="panel-error">{{ workbench.recent_backtests_error }}</p>
        <div v-else-if="workbenchLoading && !workbench" class="panel-empty">正在读取最近回测…</div>
        <div v-else-if="!workbench?.recent_backtests?.length" class="panel-empty">暂无回测运行。可先到因子页发起只读预检和研究回测。</div>
        <div v-else class="run-list">
          <button
            v-for="run in workbench.recent_backtests"
            :key="run.id"
            class="run-row"
            :class="{ selected: selectedRunId === run.id }"
            type="button"
            @click="selectRun(run)"
          >
            <span class="run-id">#{{ run.id }}</span>
            <span class="run-main"><strong>{{ run.name }}</strong><small>{{ run.strategy_name }} · {{ run.start_date }} 至 {{ run.end_date }}</small></span>
            <span :class="['run-status', `status-${run.status}`]">{{ runStatusLabel(run.status) }}</span>
            <span class="run-return">{{ percent(run.total_return) }}</span>
            <ArrowRight :size="15" />
          </button>
        </div>
      </article>

      <article class="research-panel compact-panel">
        <header class="panel-heading"><div><BriefcaseBusiness :size="19" /><div><span>组合草案</span><h2>待核对方案</h2></div></div><RouterLink to="/portfolio">进入</RouterLink></header>
        <p v-if="workbench?.portfolio_plans_error" class="panel-error">{{ workbench.portfolio_plans_error }}</p>
        <div v-else-if="!workbench?.portfolio_plans?.length" class="panel-empty compact">暂无组合草案。</div>
        <ul v-else class="compact-list">
          <li v-for="plan in workbench.portfolio_plans.slice(0, 4)" :key="plan.plan_id"><div><strong>{{ plan.name }}</strong><small>#{{ plan.plan_id }} · 截至 {{ plan.as_of_date }}</small></div><span>{{ numberText(plan.total_equity) }} 元</span></li>
        </ul>
      </article>

      <article class="research-panel compact-panel">
        <header class="panel-heading"><div><Wallet :size="19" /><div><span>持仓账本</span><h2>人工成交事实</h2></div></div><RouterLink to="/positions">进入</RouterLink></header>
        <p v-if="workbench?.position_books_error" class="panel-error">{{ workbench.position_books_error }}</p>
        <div v-else-if="!workbench?.position_books?.length" class="panel-empty compact">暂无持仓账本。</div>
        <ul v-else class="compact-list">
          <li v-for="book in workbench.position_books.slice(0, 4)" :key="book.id"><div><strong>{{ book.name }}</strong><small>#{{ book.id }} · {{ book.note || "未填写备注" }}</small></div><span>现金 {{ numberText(book.cash) }}</span></li>
        </ul>
      </article>

      <article class="research-panel compact-panel alerts-panel">
        <header class="panel-heading"><div><ShieldAlert :size="19" /><div><span>运维告警</span><h2>{{ criticalAlerts }} 个关键项</h2></div></div><RouterLink to="/overview">查看</RouterLink></header>
        <p v-if="workbench?.alerts_error" class="panel-error">{{ workbench.alerts_error }}</p>
        <div v-else-if="!workbench?.alerts?.items.length" class="panel-empty compact">当前没有告警候选。</div>
        <ul v-else class="alert-list">
          <li v-for="alert in workbench.alerts.items.slice(0, 3)" :key="alert.key" :class="`severity-${alert.severity}`"><strong>{{ alert.title }}</strong><span>{{ alert.detail }}</span></li>
        </ul>
      </article>
    </section>

    <section class="pipeline-shell" aria-label="单次研究流水线">
      <header class="pipeline-heading">
        <div><span class="research-eyebrow"><BookOpen :size="15" />RUN PIPELINE</span><h2>单次研究流水线</h2><p>选择最近回测，或使用带 `run_id` 的链接恢复净值、绩效、基准和期末持仓。</p></div>
        <button v-if="selectedRunId" class="text-button" type="button" @click="closePipeline">关闭 #{{ selectedRunId }}</button>
      </header>

      <div v-if="pipelineLoading" class="pipeline-empty">正在读取研究流水线…</div>
      <div v-else-if="pipelineError" class="pipeline-error" role="alert"><AlertTriangle :size="18" /><div><strong>流水线读取失败</strong><p>{{ pipelineError }}</p></div></div>
      <div v-else-if="!pipeline" class="pipeline-empty"><FlaskConical :size="28" /><strong>尚未选择回测</strong><span>从上方最近回测选择一项，页面会把 run ID 写入地址栏。</span></div>
      <template v-else>
        <article class="pipeline-summary">
          <div><span>当前研究</span><h3>{{ pipeline.run.name }} · #{{ pipeline.run.id }}</h3><p>{{ pipeline.run.strategy_name }} · {{ pipeline.run.start_date }} 至 {{ pipeline.run.end_date }}</p></div>
          <div class="summary-actions"><RouterLink :to="`/backtests/runs/${pipeline.run.id}`" class="secondary-link">完整详情</RouterLink><button class="primary-button" type="button" :disabled="pipeline.run.status !== 'success' || !pipeline.final_holdings" @click="continueToPortfolio">带入组合草案 <ArrowRight :size="15" /></button></div>
        </article>

        <div class="metric-row">
          <div><span>总收益</span><strong>{{ percent(pipeline.performance?.total_return ?? pipeline.run.total_return) }}</strong></div>
          <div><span>年化收益</span><strong>{{ percent(pipeline.performance?.annualized_return) }}</strong></div>
          <div><span>最大回撤</span><strong>{{ percent(pipeline.performance?.maximum_drawdown) }}</strong></div>
          <div><span>夏普</span><strong>{{ numberText(pipeline.performance?.sharpe_ratio, 3) }}</strong></div>
          <div><span>相对基准</span><strong>{{ percent(pipeline.benchmark?.excess_return) }}</strong></div>
        </div>

        <div class="pipeline-grid">
          <article class="pipeline-card chart-card">
            <header><h3>净值与回撤</h3><span>{{ pipeline.equity_curve?.length ?? 0 }} 个观测</span></header>
            <p v-if="pipeline.equity_curve_error" class="panel-error">{{ pipeline.equity_curve_error }}</p>
            <EquityChart v-else :points="pipeline.equity_curve ?? []" :initial-cash="pipeline.run.initial_cash" :loading="false" />
          </article>

          <article class="pipeline-card">
            <header><h3>基准核对</h3><span>{{ pipeline.benchmark?.benchmark ?? "未配置" }}</span></header>
            <p v-if="pipeline.benchmark_error" class="panel-error">{{ pipeline.benchmark_error }}</p>
            <dl v-else-if="pipeline.benchmark" class="detail-list"><div><dt>输入来源</dt><dd>{{ pipeline.benchmark.input_source }}</dd></div><div><dt>策略收益</dt><dd>{{ percent(pipeline.benchmark.strategy_total_return) }}</dd></div><div><dt>基准收益</dt><dd>{{ percent(pipeline.benchmark.benchmark_total_return) }}</dd></div><div><dt>超额收益</dt><dd>{{ percent(pipeline.benchmark.excess_return) }}</dd></div></dl>
            <p v-else class="panel-empty compact">当前运行没有可用基准比较。</p>
          </article>

          <article class="pipeline-card holdings-card">
            <header><h3>期末持仓</h3><span>{{ pipeline.final_holdings?.as_of_date ?? "—" }}</span></header>
            <p v-if="pipeline.final_holdings_error" class="panel-error">{{ pipeline.final_holdings_error }}</p>
            <div v-else-if="!pipeline.final_holdings?.holdings.length" class="panel-empty compact">期末无持仓。</div>
            <div v-else class="holding-table"><div class="holding-head"><span>证券</span><span>数量</span><span>权重</span></div><div v-for="holding in pipeline.final_holdings.holdings.slice(0, 8)" :key="holding.symbol"><span><strong>{{ holding.symbol }}</strong><small>{{ holding.name || holding.industry || "—" }}</small></span><span>{{ holding.quantity }}</span><span>{{ percent(holding.weight) }}</span></div></div>
          </article>
        </div>
        <p class="research-caveat">聚合页只复用后端既有研究事实，不认证 PIT、样本外稳定性或可交易性。进入组合草案后仍须经过风险预算、就绪核对、正式风险评估和人工确认。</p>
      </template>
    </section>
    <DividendScenarioPanel />
  </main>
</template>

<style scoped>
.research-page { max-width: 1320px; margin: 0 auto; padding: 30px 28px 72px; color: #172033; }
.research-hero, .pipeline-heading, .pipeline-summary, .panel-heading, .panel-heading > div, .hero-actions, .summary-actions { display: flex; align-items: center; }
.research-hero { align-items: flex-start; justify-content: space-between; gap: 24px; margin-bottom: 18px; }
.research-hero > div:first-child, .pipeline-heading > div { display: grid; gap: 7px; }
.research-eyebrow { display: inline-flex; align-items: center; gap: 7px; color: #2563eb; font-size: 11px; font-weight: 800; letter-spacing: .09em; }
.research-page h1 { color: #101828; font-size: clamp(30px, 4vw, 42px); }
.research-hero p, .pipeline-heading p { max-width: 760px; color: #667085; font-size: 14px; line-height: 1.6; }
.hero-actions { align-items: flex-end; gap: 8px; }
.readonly-pill { border: 1px solid #c7d7fe; border-radius: 999px; padding: 8px 11px; color: #1d4ed8; background: #eff6ff; font-size: 12px; font-weight: 700; white-space: nowrap; }
.refresh-button, .primary-button, .text-button { border: 0; cursor: pointer; font: inherit; }
.refresh-button { display: inline-flex; min-height: 36px; align-items: center; gap: 7px; border: 1px solid #d0d5dd; border-radius: 7px; padding: 0 12px; color: #344054; background: #fff; font-size: 12px; font-weight: 700; }
.refresh-button:disabled, .primary-button:disabled { cursor: not-allowed; opacity: .55; }
.spin { animation: spin 1s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }
.research-status { display: flex; flex-wrap: wrap; gap: 8px 18px; margin-bottom: 18px; border: 1px solid #dbe4f0; border-radius: 8px; padding: 10px 13px; color: #475467; background: #f8fafc; font-size: 12px; }
.research-status strong { color: #175cd3; }.research-status.is-partial strong { color: #b54708; }.research-status.is-error strong, .status-error { color: #b42318; }
.workbench-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 14px; }
.research-panel, .pipeline-shell, .pipeline-card { min-width: 0; border: 1px solid #e3e7ee; border-radius: 10px; background: #fff; box-shadow: 0 8px 28px rgba(15, 23, 42, .04); }
.research-panel { padding: 17px; }.runs-panel { grid-column: span 2; grid-row: span 2; }
.panel-heading { justify-content: space-between; gap: 12px; margin-bottom: 13px; }.panel-heading > div { align-items: flex-start; gap: 9px; }.panel-heading > div > div { display: grid; gap: 2px; }
.panel-heading svg { color: #2563eb; }.panel-heading span { color: #667085; font-size: 11px; font-weight: 700; text-transform: uppercase; }.panel-heading h2 { color: #101828; font-size: 16px; }
.panel-heading a { display: inline-flex; align-items: center; gap: 4px; color: #2563eb; font-size: 12px; font-weight: 700; text-decoration: none; }
.panel-empty { display: grid; min-height: 170px; place-content: center; gap: 8px; color: #667085; font-size: 13px; text-align: center; }.panel-empty.compact { min-height: 88px; }
.panel-error { margin: 0; border: 1px solid #fecaca; border-radius: 7px; padding: 9px 10px; color: #b42318; background: #fff1f2; font-size: 12px; line-height: 1.5; }
.run-list { display: grid; gap: 7px; }.run-row { display: grid; grid-template-columns: 48px minmax(0, 1fr) 64px 80px 18px; align-items: center; gap: 9px; width: 100%; border: 1px solid #edf0f5; border-radius: 8px; padding: 11px; color: #344054; background: #fbfcfe; cursor: pointer; text-align: left; }.run-row:hover, .run-row.selected { border-color: #93b4ff; background: #f0f6ff; }.run-id { color: #667085; font: 700 12px ui-monospace, monospace; }.run-main { display: grid; gap: 3px; min-width: 0; }.run-main strong, .run-main small { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }.run-main strong { color: #101828; font-size: 13px; }.run-main small { color: #667085; font-size: 11px; }.run-status { border-radius: 999px; padding: 4px 7px; color: #475467; background: #eef2f6; font-size: 10px; font-weight: 700; text-align: center; }.status-success { color: #067647; background: #ecfdf3; }.status-failed { color: #b42318; background: #fff1f2; }.status-running { color: #175cd3; background: #eff8ff; }.run-return { color: #344054; font-size: 12px; font-variant-numeric: tabular-nums; text-align: right; }
.compact-list, .alert-list { display: grid; gap: 8px; margin: 0; padding: 0; list-style: none; }.compact-list li { display: flex; justify-content: space-between; gap: 10px; border-top: 1px solid #f0f2f5; padding-top: 8px; }.compact-list li:first-child { border-top: 0; padding-top: 0; }.compact-list li div { display: grid; gap: 2px; min-width: 0; }.compact-list strong { overflow: hidden; color: #344054; font-size: 12px; text-overflow: ellipsis; white-space: nowrap; }.compact-list small, .compact-list li > span { color: #667085; font-size: 10px; }.compact-list li > span { white-space: nowrap; }
.alert-list li { border-left: 3px solid #fdb022; padding-left: 9px; }.alert-list li.severity-critical { border-color: #f04438; }.alert-list li.severity-info { border-color: #2e90fa; }.alert-list strong, .alert-list span { display: block; }.alert-list strong { color: #344054; font-size: 12px; }.alert-list span { margin-top: 2px; color: #667085; font-size: 10px; line-height: 1.45; }
.pipeline-shell { margin-top: 18px; padding: 20px; }.pipeline-heading { align-items: flex-start; justify-content: space-between; gap: 20px; border-bottom: 1px solid #edf0f5; padding-bottom: 16px; }.pipeline-heading h2 { color: #101828; font-size: 22px; }.text-button { color: #667085; background: transparent; font-size: 12px; text-decoration: underline; }
.pipeline-empty { display: grid; min-height: 250px; place-content: center; gap: 8px; color: #667085; text-align: center; }.pipeline-error { display: flex; align-items: flex-start; gap: 10px; margin-top: 16px; border: 1px solid #fecaca; border-radius: 8px; padding: 13px; color: #b42318; background: #fff1f2; }.pipeline-error p { margin-top: 4px; font-size: 12px; }
.pipeline-summary { justify-content: space-between; gap: 18px; padding: 17px 0; }.pipeline-summary > div:first-child { display: grid; gap: 4px; }.pipeline-summary span { color: #2563eb; font-size: 11px; font-weight: 800; }.pipeline-summary h3 { color: #101828; font-size: 18px; }.pipeline-summary p { color: #667085; font-size: 12px; }.summary-actions { gap: 8px; }.secondary-link, .primary-button { display: inline-flex; min-height: 36px; align-items: center; gap: 6px; border-radius: 7px; padding: 0 12px; font-size: 12px; font-weight: 700; text-decoration: none; }.secondary-link { border: 1px solid #d0d5dd; color: #344054; background: #fff; }.primary-button { color: #fff; background: #2563eb; }
.metric-row { display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); gap: 9px; margin-bottom: 14px; }.metric-row div { border: 1px solid #e7ebf1; border-radius: 8px; padding: 12px; background: #fbfcfe; }.metric-row span, .metric-row strong { display: block; }.metric-row span { color: #667085; font-size: 10px; }.metric-row strong { margin-top: 5px; color: #101828; font-size: 16px; font-variant-numeric: tabular-nums; }
.pipeline-grid { display: grid; grid-template-columns: minmax(0, 1.55fr) minmax(250px, .7fr); gap: 12px; }.pipeline-card { padding: 15px; box-shadow: none; }.pipeline-card > header { display: flex; justify-content: space-between; gap: 12px; margin-bottom: 10px; }.pipeline-card h3 { color: #101828; font-size: 14px; }.pipeline-card header span { color: #667085; font-size: 11px; }.chart-card { grid-row: span 2; }.detail-list { display: grid; gap: 8px; }.detail-list div { display: flex; justify-content: space-between; gap: 12px; border-top: 1px solid #f0f2f5; padding-top: 7px; }.detail-list div:first-child { border-top: 0; }.detail-list dt, .detail-list dd { font-size: 11px; }.detail-list dt { color: #667085; }.detail-list dd { color: #344054; font-weight: 700; text-align: right; }
.holding-table { display: grid; gap: 6px; }.holding-table > div { display: grid; grid-template-columns: minmax(0, 1fr) 60px 64px; align-items: center; gap: 8px; font-size: 11px; }.holding-head { color: #667085; font-weight: 700; }.holding-table > div > span:nth-child(n+2) { text-align: right; }.holding-table strong, .holding-table small { display: block; }.holding-table strong { color: #344054; }.holding-table small { color: #98a2b3; }
.research-caveat { margin: 14px 0 0; border-left: 3px solid #fdb022; padding: 8px 10px; color: #7a5b14; background: #fffaeb; font-size: 11px; line-height: 1.55; }
@media (max-width: 1040px) { .workbench-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }.runs-panel { grid-column: span 2; }.pipeline-grid { grid-template-columns: 1fr; }.chart-card { grid-row: auto; } }
@media (max-width: 720px) { .research-page { padding: 24px 16px 48px; }.research-hero, .pipeline-heading, .pipeline-summary, .hero-actions { align-items: flex-start; flex-direction: column; }.workbench-grid { grid-template-columns: 1fr; }.runs-panel { grid-column: auto; }.run-row { grid-template-columns: 42px minmax(0, 1fr) 58px 18px; }.run-return { display: none; }.metric-row { grid-template-columns: repeat(2, minmax(0, 1fr)); }.summary-actions { flex-wrap: wrap; }.pipeline-shell { padding: 15px; } }
</style>
