<script setup lang="ts">
import { ServerCog, Loader2 } from "@lucide/vue";
import { computed, ref } from "vue";

import type {
  BacktestRunFailure,
  DataQualityFailure,
  FactorRunFailure,
  JobFailure,
  OperationsFailureDetail,
  OperationsRerunPlan,
  PaperOrderFailure,
} from "../../types";
import { formatDate, formatUnknown, sideLabel, statusLabel, strategyLabel } from "./formatters";
import type { FailureItem, FailureSectionView, FailureType, SelectedFailure } from "./viewTypes";

const props = defineProps<{
  sections: FailureSectionView[];
  selectedFailure: SelectedFailure | null;
  failureDetail: OperationsFailureDetail | null;
  rerunPlan: OperationsRerunPlan | null;
  isDetailLoading: boolean;
  detailErrorMessage: string | null;
}>();

const emit = defineEmits<{
  select: [type: FailureType, item: FailureItem];
  execute: [plan: OperationsRerunPlan];
}>();

const activeFailureType = ref<FailureType | "all">("all");
const filterOptions = computed(() => [
  {
    type: "all" as const,
    title: "全部",
    total: props.sections.reduce((sum, section) => sum + section.total, 0),
  },
  ...props.sections.map((section) => ({
    type: section.type,
    title: section.title,
    total: section.total,
  })),
]);
const filteredSections = computed(() => {
  if (activeFailureType.value === "all") {
    return props.sections;
  }

  return props.sections.filter((section) => section.type === activeFailureType.value);
});

function getFailureTitle(type: FailureType, item: FailureItem) {
  if (type === "data_quality_issue") {
    const issue = item as DataQualityFailure;
    return `${issue.symbol ?? issue.dataset} · ${issue.issue_type}`;
  }
  if (type === "job") {
    return (item as JobFailure).job_name;
  }
  if (type === "factor_run") {
    return (item as FactorRunFailure).factor_name;
  }
  if (type === "backtest_run") {
    return (item as BacktestRunFailure).name;
  }

  const order = item as PaperOrderFailure;
  return `${order.symbol} · ${sideLabel(order.side)} ${order.order_quantity}`;
}

function getFailureSubtitle(type: FailureType, item: FailureItem) {
  if (type === "data_quality_issue") {
    const issue = item as DataQualityFailure;
    return `${issue.dataset} / ${statusLabel(issue.status)} / ${formatDate(issue.trade_date)}`;
  }
  if (type === "job") {
    const job = item as JobFailure;
    return `${job.job_type} / failed rows ${job.rows_failed}`;
  }
  if (type === "factor_run") {
    const run = item as FactorRunFailure;
    return `${run.factor_version} / ${formatDate(run.input_start_date)}-${formatDate(run.input_end_date)}`;
  }
  if (type === "backtest_run") {
    const run = item as BacktestRunFailure;
    return `${strategyLabel(run.strategy_name)} / ${formatDate(run.start_date)}-${formatDate(run.end_date)}`;
  }

  const order = item as PaperOrderFailure;
  return `${statusLabel(order.status)} / ${order.rejection_reason ?? "未给出拒单原因"}`;
}

function getDetailRows(detail: OperationsFailureDetail | null) {
  if (!detail) {
    return [];
  }

  const item =
    detail.data_quality_issue ??
    detail.job ??
    detail.factor_run ??
    detail.backtest_run ??
    detail.paper_order ??
    null;

  if (!item) {
    return [];
  }

  return Object.entries(item).map(([key, value]) => ({
    key,
    value: formatUnknown(value),
  }));
}

function getRerunParameterRows(plan: OperationsRerunPlan) {
  return Object.entries(plan.parameters).map(([key, value]) => ({
    key,
    value: formatUnknown(value),
  }));
}
</script>

<template>
  <section id="failures" class="dashboard-section">
    <div class="section-title">
      <ServerCog :size="20" aria-hidden="true" />
      <h2>失败中心</h2>
      <span class="section-note">点击失败项查看详情、预检并执行安全修复</span>
    </div>

    <div class="failure-filters" aria-label="失败类型筛选">
      <button
        v-for="option in filterOptions"
        :key="option.type"
        :class="{ active: activeFailureType === option.type }"
        type="button"
        @click="activeFailureType = option.type"
      >
        <span>{{ option.title }}</span>
        <strong>{{ option.total }}</strong>
      </button>
    </div>

    <div class="failure-layout">
      <div class="failure-list">
        <article v-for="section in filteredSections" :key="section.type" class="failure-section-card">
          <header>
            <h3>{{ section.title }}</h3>
            <span>共 {{ section.total }} 条<span v-if="section.hasMore"> · 还有更多</span></span>
          </header>

          <button
            v-for="item in section.items"
            :key="`${section.type}-${item.id}`"
            :class="[
              'failure-item',
              {
                active: selectedFailure?.type === section.type && selectedFailure?.id === item.id,
              },
            ]"
            type="button"
            @click="emit('select', section.type, item)"
          >
            <strong>{{ getFailureTitle(section.type, item) }}</strong>
            <span>{{ getFailureSubtitle(section.type, item) }}</span>
          </button>
          <div v-if="section.items.length === 0" class="empty-row">暂无记录</div>
        </article>
      </div>

      <aside class="detail-panel">
        <header>
          <div>
            <p class="eyebrow">失败详情</p>
            <h3>{{ selectedFailure?.label ?? "选择一个失败项" }}</h3>
          </div>
          <Loader2 v-if="isDetailLoading" class="spin" :size="18" aria-hidden="true" />
        </header>

        <p v-if="detailErrorMessage" class="inline-error">{{ detailErrorMessage }}</p>

        <div v-if="rerunPlan" class="rerun-card">
          <p class="rerun-label">安全修复计划</p>
          <div>
            <span :class="['status', rerunPlan.supported ? 'status-success' : 'status-rejected']">
              {{ rerunPlan.supported ? "可执行" : "需人工复核" }}
            </span>
            <strong>{{ rerunPlan.action_type }}</strong>
          </div>
          <p>{{ rerunPlan.reason }}</p>
          <pre>{{ formatUnknown(rerunPlan.parameters) }}</pre>

          <div v-if="rerunPlan.supported" class="rerun-preflight">
            <strong>执行前准备</strong>
            <ul>
              <li>核对参数与失败详情是否匹配。</li>
              <li>系统会先重新预检，再以幂等后台任务执行。</li>
              <li>执行完成后会重新检查缺口；只有问题闭环时才自动标记已修复。</li>
            </ul>
            <div v-if="getRerunParameterRows(rerunPlan).length > 0" class="rerun-parameters">
              <div v-for="row in getRerunParameterRows(rerunPlan)" :key="row.key">
                <span>{{ row.key }}</span>
                <code>{{ row.value }}</code>
              </div>
            </div>
            <button type="button" class="primary-action" @click="emit('execute', rerunPlan)">预检并执行</button>
          </div>
        </div>

        <div v-if="failureDetail" class="detail-table">
          <div v-for="row in getDetailRows(failureDetail)" :key="row.key" class="detail-row">
            <span>{{ row.key }}</span>
            <pre>{{ row.value }}</pre>
          </div>
        </div>
        <div v-else class="empty-panel">请选择左侧失败项查看详情。</div>
      </aside>
    </div>
  </section>
</template>
