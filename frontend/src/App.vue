<script setup lang="ts">
import {
  Activity,
  AlertTriangle,
  BarChart3,
  BriefcaseBusiness,
  CheckCircle2,
  DatabaseZap,
  LineChart,
  Loader2,
  PlayCircle,
  RefreshCw,
  ServerCog,
  ShieldCheck,
} from "@lucide/vue";
import { computed, onMounted, ref, watch } from "vue";

import { fetchOperationsOverview } from "./api";
import { demoOverview } from "./demoData";
import type { OperationsOverview } from "./types";

const overview = ref<OperationsOverview | null>(null);
const isLoading = ref(false);
const source = ref<"api" | "demo">("demo");
const errorMessage = ref<string | null>(null);
const limit = ref(5);

let activeController: AbortController | null = null;

const displayOverview = computed(() => overview.value ?? demoOverview);
const generatedAt = computed(() => formatDateTime(displayOverview.value.generated_at));

const navItems = [
  { id: "overview", label: "总览", icon: Activity },
  { id: "quality", label: "质量", icon: ShieldCheck },
  { id: "jobs", label: "任务", icon: ServerCog },
  { id: "factors", label: "因子", icon: BarChart3 },
  { id: "backtests", label: "回测", icon: LineChart },
  { id: "paper", label: "模拟盘", icon: BriefcaseBusiness },
];

const snapshotCards = computed(() => {
  const current = displayOverview.value;
  return [
    {
      label: "开放质量问题",
      value: sumCounts(current.data_quality.open_issue_severity_counts),
      detail: `${current.data_quality.latest_issues.length} 条最近记录`,
      tone: "danger",
      icon: AlertTriangle,
    },
    {
      label: "失败任务",
      value: current.jobs.status_counts.failed ?? 0,
      detail: `${current.jobs.status_counts.running ?? 0} 个运行中`,
      tone: "warning",
      icon: ServerCog,
    },
    {
      label: "因子成功运行",
      value: current.factors.status_counts.success ?? 0,
      detail: `${current.factors.latest_runs.length} 个最近批次`,
      tone: "success",
      icon: CheckCircle2,
    },
    {
      label: "模拟持仓",
      value: current.paper_trading.position_count,
      detail: `${current.paper_trading.fill_count} 笔成交`,
      tone: "info",
      icon: BriefcaseBusiness,
    },
  ];
});

async function loadOverview() {
  activeController?.abort();
  activeController = new AbortController();
  isLoading.value = true;
  errorMessage.value = null;

  try {
    overview.value = await fetchOperationsOverview(limit.value, activeController.signal);
    source.value = "api";
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") {
      return;
    }

    overview.value = demoOverview;
    source.value = "demo";
    errorMessage.value = error instanceof Error ? error.message : "无法连接后端 API";
  } finally {
    isLoading.value = false;
  }
}

watch(limit, loadOverview);
onMounted(loadOverview);

function countEntries(counts: Record<string, number>) {
  return Object.entries(counts).sort(([, left], [, right]) => right - left);
}

function maxCount(counts: Record<string, number>) {
  return Math.max(1, ...Object.values(counts));
}

function sumCounts(counts: Record<string, number>) {
  return Object.values(counts).reduce((total, count) => total + count, 0);
}

function barWidth(count: number, counts: Record<string, number>) {
  return `${Math.max(8, (count / maxCount(counts)) * 100)}%`;
}

function statusClass(status: string) {
  return `status status-${status.replace(/_/g, "-")}`;
}

function severityClass(severity: string) {
  return `severity severity-${severity}`;
}

function formatDate(value: string | null) {
  if (!value) {
    return "-";
  }

  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
  }).format(new Date(value));
}

function formatDateTime(value: string | null) {
  if (!value) {
    return "-";
  }

  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

function formatNumber(value: number | string | null) {
  if (value === null) {
    return "-";
  }

  return new Intl.NumberFormat("zh-CN", {
    maximumFractionDigits: 2,
  }).format(Number(value));
}

function formatPercent(value: number | string | null) {
  if (value === null) {
    return "-";
  }

  return new Intl.NumberFormat("zh-CN", {
    style: "percent",
    maximumFractionDigits: 2,
  }).format(Number(value));
}

function sideLabel(side: string) {
  return side === "buy" ? "买入" : "卖出";
}
</script>

<template>
  <div class="app-shell">
    <aside class="sidebar">
      <div class="brand">
        <DatabaseZap :size="28" aria-hidden="true" />
        <div>
          <strong>Stock Master</strong>
          <span>Quant Ops</span>
        </div>
      </div>

      <nav class="nav-list" aria-label="Dashboard sections">
        <a v-for="item in navItems" :key="item.id" :href="`#${item.id}`" class="nav-link">
          <component :is="item.icon" :size="18" aria-hidden="true" />
          <span>{{ item.label }}</span>
        </a>
      </nav>
    </aside>

    <main class="workspace">
      <header id="overview" class="topbar">
        <div>
          <p class="eyebrow">V6 Operations Console</p>
          <h1>运维工作台</h1>
        </div>

        <div class="toolbar">
          <div class="segmented" aria-label="记录数量">
            <button :class="{ active: limit === 5 }" type="button" @click="limit = 5">5</button>
            <button :class="{ active: limit === 10 }" type="button" @click="limit = 10">10</button>
            <button :class="{ active: limit === 20 }" type="button" @click="limit = 20">20</button>
          </div>
          <button class="icon-button" type="button" title="刷新" @click="loadOverview">
            <Loader2 v-if="isLoading" class="spin" :size="18" aria-hidden="true" />
            <RefreshCw v-else :size="18" aria-hidden="true" />
          </button>
        </div>
      </header>

      <section class="status-strip" aria-label="Connection status">
        <span :class="['source-pill', source]">{{ source === "api" ? "Live API" : "Demo Data" }}</span>
        <span>生成时间 {{ generatedAt }}</span>
        <span v-if="errorMessage" class="inline-error">{{ errorMessage }}</span>
      </section>

      <section class="metric-grid" aria-label="Key metrics">
        <article v-for="card in snapshotCards" :key="card.label" :class="['metric-card', card.tone]">
          <component :is="card.icon" :size="22" aria-hidden="true" />
          <div>
            <span>{{ card.label }}</span>
            <strong>{{ formatNumber(card.value) }}</strong>
            <small>{{ card.detail }}</small>
          </div>
        </article>
      </section>

      <section id="quality" class="dashboard-section">
        <div class="section-title">
          <ShieldCheck :size="20" aria-hidden="true" />
          <h2>数据质量</h2>
        </div>

        <div class="section-layout">
          <div class="count-panel">
            <h3>问题状态</h3>
            <div
              v-for="[status, count] in countEntries(displayOverview.data_quality.issue_status_counts)"
              :key="status"
              class="count-row"
            >
              <span>{{ status }}</span>
              <div class="bar-track">
                <i :style="{ width: barWidth(count, displayOverview.data_quality.issue_status_counts) }" />
              </div>
              <strong>{{ count }}</strong>
            </div>
          </div>

          <div class="table-panel">
            <h3>最近问题</h3>
            <table>
              <thead>
                <tr>
                  <th>标的</th>
                  <th>数据集</th>
                  <th>类型</th>
                  <th>级别</th>
                  <th>日期</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="issue in displayOverview.data_quality.latest_issues" :key="issue.id">
                  <td>{{ issue.symbol ?? "-" }}</td>
                  <td>{{ issue.dataset }}</td>
                  <td>{{ issue.issue_type }}</td>
                  <td><span :class="severityClass(issue.severity)">{{ issue.severity }}</span></td>
                  <td>{{ formatDate(issue.trade_date) }}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>
      </section>

      <section id="jobs" class="dashboard-section">
        <div class="section-title">
          <ServerCog :size="20" aria-hidden="true" />
          <h2>任务流水</h2>
        </div>

        <div class="section-layout">
          <div class="count-panel">
            <h3>任务状态</h3>
            <div
              v-for="[status, count] in countEntries(displayOverview.jobs.status_counts)"
              :key="status"
              class="count-row"
            >
              <span>{{ status }}</span>
              <div class="bar-track"><i :style="{ width: barWidth(count, displayOverview.jobs.status_counts) }" /></div>
              <strong>{{ count }}</strong>
            </div>
          </div>

          <div class="table-panel">
            <h3>最近任务</h3>
            <table>
              <thead>
                <tr>
                  <th>任务</th>
                  <th>类型</th>
                  <th>状态</th>
                  <th>失败行</th>
                  <th>开始</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="job in displayOverview.jobs.latest_jobs" :key="job.id">
                  <td>{{ job.job_name }}</td>
                  <td>{{ job.job_type }}</td>
                  <td><span :class="statusClass(job.status)">{{ job.status }}</span></td>
                  <td>{{ job.rows_failed }}</td>
                  <td>{{ formatDateTime(job.started_at) }}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>
      </section>

      <section id="factors" class="dashboard-section">
        <div class="section-title">
          <BarChart3 :size="20" aria-hidden="true" />
          <h2>因子运行</h2>
        </div>

        <div class="table-panel full">
          <table>
            <thead>
              <tr>
                <th>因子</th>
                <th>版本</th>
                <th>状态</th>
                <th>输入区间</th>
                <th>写入行</th>
                <th>结束</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="run in displayOverview.factors.latest_runs" :key="run.id">
                <td>{{ run.factor_name }}</td>
                <td>{{ run.factor_version }}</td>
                <td><span :class="statusClass(run.status)">{{ run.status }}</span></td>
                <td>{{ formatDate(run.input_start_date) }} - {{ formatDate(run.input_end_date) }}</td>
                <td>{{ formatNumber(run.rows_written) }}</td>
                <td>{{ formatDateTime(run.finished_at) }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>

      <section id="backtests" class="dashboard-section">
        <div class="section-title">
          <LineChart :size="20" aria-hidden="true" />
          <h2>回测结果</h2>
        </div>

        <div class="table-panel full">
          <table>
            <thead>
              <tr>
                <th>名称</th>
                <th>策略</th>
                <th>状态</th>
                <th>区间</th>
                <th>最终权益</th>
                <th>收益</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="run in displayOverview.backtests.latest_runs" :key="run.id">
                <td>{{ run.name }}</td>
                <td>{{ run.strategy_name }}</td>
                <td><span :class="statusClass(run.status)">{{ run.status }}</span></td>
                <td>{{ formatDate(run.start_date) }} - {{ formatDate(run.end_date) }}</td>
                <td>{{ formatNumber(run.final_equity) }}</td>
                <td>{{ formatPercent(run.total_return) }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>

      <section id="paper" class="dashboard-section">
        <div class="section-title">
          <PlayCircle :size="20" aria-hidden="true" />
          <h2>模拟盘</h2>
        </div>

        <div class="paper-grid">
          <article class="mini-stat">
            <span>账户</span>
            <strong>{{ displayOverview.paper_trading.account_count }}</strong>
          </article>
          <article class="mini-stat">
            <span>成交</span>
            <strong>{{ displayOverview.paper_trading.fill_count }}</strong>
          </article>
          <article class="mini-stat">
            <span>持仓</span>
            <strong>{{ displayOverview.paper_trading.position_count }}</strong>
          </article>
        </div>

        <div class="table-panel full">
          <table>
            <thead>
              <tr>
                <th>标的</th>
                <th>方向</th>
                <th>数量</th>
                <th>状态</th>
                <th>交易日</th>
                <th>原因</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="order in displayOverview.paper_trading.latest_orders" :key="order.id">
                <td>{{ order.symbol }}</td>
                <td>{{ sideLabel(order.side) }}</td>
                <td>{{ formatNumber(order.order_quantity) }}</td>
                <td><span :class="statusClass(order.status)">{{ order.status }}</span></td>
                <td>{{ formatDate(order.trade_date) }}</td>
                <td>{{ order.rejection_reason ?? "-" }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>
    </main>
  </div>
</template>
