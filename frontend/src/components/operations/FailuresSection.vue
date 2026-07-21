<script setup lang="ts">
import { ServerCog, Loader2 } from "@lucide/vue";

import type {
  BacktestRunFailure,
  DataQualityFailure,
  FactorRunFailure,
  JobFailure,
  OperationsFailureDetail,
  OperationsRerunPlan,
  PaperOrderFailure,
} from "../../types";
import { formatDate, formatUnknown, sideLabel } from "./formatters";
import type { FailureItem, FailureSectionView, FailureType, SelectedFailure } from "./viewTypes";

defineProps<{
  sections: FailureSectionView[];
  selectedFailure: SelectedFailure | null;
  failureDetail: OperationsFailureDetail | null;
  rerunPlan: OperationsRerunPlan | null;
  isDetailLoading: boolean;
  detailErrorMessage: string | null;
}>();

const emit = defineEmits<{
  select: [type: FailureType, item: FailureItem];
}>();

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
    return `${issue.dataset} / ${issue.status} / ${formatDate(issue.trade_date)}`;
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
    return `${run.strategy_name} / ${formatDate(run.start_date)}-${formatDate(run.end_date)}`;
  }

  const order = item as PaperOrderFailure;
  return `${order.status} / ${order.rejection_reason ?? "未给出拒单原因"}`;
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
      <span class="section-note">点击失败项查看详情和只读重跑建议</span>
    </div>

    <div class="failure-layout">
      <div class="failure-list">
        <article v-for="section in sections" :key="section.type" class="failure-section-card">
          <header>
            <h3>{{ section.title }}</h3>
            <span>{{ section.total }} total<span v-if="section.hasMore"> · has more</span></span>
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
            <p class="eyebrow">Failure Detail</p>
            <h3>{{ selectedFailure?.label ?? "选择一个失败项" }}</h3>
          </div>
          <Loader2 v-if="isDetailLoading" class="spin" :size="18" aria-hidden="true" />
        </header>

        <p v-if="detailErrorMessage" class="inline-error">{{ detailErrorMessage }}</p>

        <div v-if="rerunPlan" class="rerun-card">
          <p class="rerun-label">只读重跑建议，不会触发执行</p>
          <div>
            <span :class="['status', rerunPlan.supported ? 'status-success' : 'status-rejected']">
              {{ rerunPlan.supported ? "可生成建议" : "需人工复核" }}
            </span>
            <strong>{{ rerunPlan.action_type }}</strong>
          </div>
          <p>{{ rerunPlan.reason }}</p>
          <pre>{{ formatUnknown(rerunPlan.parameters) }}</pre>

          <div v-if="rerunPlan.supported" class="rerun-preflight">
            <strong>执行前准备</strong>
            <ul>
              <li>核对参数与失败详情是否匹配。</li>
              <li>确认该动作只会用于后续人工触发，不会在本页面直接执行。</li>
              <li>等待后端执行入口补齐审计、幂等和权限边界。</li>
            </ul>
            <div v-if="getRerunParameterRows(rerunPlan).length > 0" class="rerun-parameters">
              <div v-for="row in getRerunParameterRows(rerunPlan)" :key="row.key">
                <span>{{ row.key }}</span>
                <code>{{ row.value }}</code>
              </div>
            </div>
            <button type="button" class="disabled-action" disabled>执行入口未启用</button>
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
