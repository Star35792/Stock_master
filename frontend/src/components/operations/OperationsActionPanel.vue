<script setup lang="ts">
import { DatabaseZap, Loader2, RefreshCw, Wrench } from "@lucide/vue";
import { computed, onBeforeUnmount, reactive, watch } from "vue";

import { useOperationsActions } from "../../composables/operationsActions";
import type { OperationsActionType } from "../../types";
import { formatUnknown, statusLabel } from "./formatters";

const {
  preview,
  currentAction,
  isPreviewing,
  isStarting,
  isRunning,
  errorMessage,
  previewAction,
  startAction,
  stopPolling,
} = useOperationsActions();

const emit = defineEmits<{ completed: [] }>();

const today = new Date().toISOString().slice(0, 10);
const start = new Date();
start.setDate(start.getDate() - 60);

const form = reactive({
  actionType: "daily_data_sync" as OperationsActionType,
  symbols: "",
  startDate: start.toISOString().slice(0, 10),
  endDate: today,
});

const actionParameters = computed<Record<string, unknown>>(() => {
  if (form.actionType === "daily_data_sync") return {};
  if (form.actionType === "daily_bar_gap_backfill") {
    return {
      symbols: form.symbols.split(/[,，\s]+/).filter(Boolean),
      start_date: form.startDate,
      end_date: form.endDate,
      adjust_type: "qfq",
    };
  }
  return {
    start_date: form.startDate,
    end_date: form.endDate,
    adjust_type: "qfq",
  };
});

async function runPreview() {
  await previewAction(form.actionType, actionParameters.value);
}

async function confirmStart() {
  if (!preview.value?.executable || preview.value.action_type !== form.actionType) return;
  if (!window.confirm(`确认执行：${preview.value.summary}`)) return;
  await startAction(preview.value.action_type, preview.value.parameters);
}

function actionLabel(actionType: OperationsActionType) {
  return {
    daily_data_sync: "同步最新可信数据",
    daily_bar_gap_backfill: "回填日线缺口",
    factor_profile_rebuild: "重建因子画像",
  }[actionType];
}

watch(
  () => currentAction.value?.status,
  (status, previous) => {
    if (previous && ["queued", "running"].includes(previous) && status && !["queued", "running"].includes(status)) {
      emit("completed");
    }
  },
);

onBeforeUnmount(stopPolling);
</script>

<template>
  <section class="dashboard-section action-workbench">
    <div class="section-title">
      <Wrench :size="20" aria-hidden="true" />
      <h2>问题处理</h2>
      <span class="section-note">先预检，再确认执行；任务全程留审计记录</span>
    </div>

    <div class="action-workbench__layout">
      <div class="action-form">
        <div class="action-kind-grid">
          <button
            v-for="item in ([
              ['daily_data_sync', '数据同步', DatabaseZap],
              ['daily_bar_gap_backfill', '缺口回填', RefreshCw],
              ['factor_profile_rebuild', '因子画像', Wrench],
            ] as const)"
            :key="item[0]"
            :class="{ active: form.actionType === item[0] }"
            type="button"
            :disabled="isRunning"
            @click="form.actionType = item[0]"
          >
            <component :is="item[2]" :size="17" />
            {{ item[1] }}
          </button>
        </div>

        <label v-if="form.actionType === 'daily_bar_gap_backfill'">
          证券代码（逗号或空格分隔）
          <input v-model="form.symbols" placeholder="例如 600000.SH" />
        </label>
        <div v-if="form.actionType !== 'daily_data_sync'" class="action-date-grid">
          <label>开始日期<input v-model="form.startDate" type="date" /></label>
          <label>结束日期<input v-model="form.endDate" type="date" /></label>
        </div>

        <div class="action-buttons">
          <button class="secondary-action" type="button" :disabled="isPreviewing || isRunning" @click="runPreview">
            <Loader2 v-if="isPreviewing" class="spin" :size="16" />
            {{ isPreviewing ? "正在预检" : "生成执行计划" }}
          </button>
          <button
            class="primary-action"
            type="button"
            :disabled="!preview?.executable || preview.action_type !== form.actionType || isStarting || isRunning"
            @click="confirmStart"
          >
            {{ isStarting ? "正在启动" : "确认执行" }}
          </button>
        </div>
        <p v-if="errorMessage" class="inline-error" role="alert">{{ errorMessage }}</p>
      </div>

      <div class="action-result-panel">
        <template v-if="currentAction">
          <header>
            <div>
              <p class="eyebrow">任务 #{{ currentAction.action_id }}</p>
              <h3>{{ actionLabel(currentAction.action_type) }}</h3>
            </div>
            <span :class="['status', `status-${currentAction.status}`]">{{ statusLabel(currentAction.status) }}</span>
          </header>
          <p>当前阶段：{{ currentAction.phase }}</p>
          <p v-if="currentAction.error_message" class="inline-error">{{ currentAction.error_message }}</p>
          <pre v-if="currentAction.result">{{ formatUnknown(currentAction.result) }}</pre>
          <p v-else-if="isRunning" class="action-running"><Loader2 class="spin" :size="16" />任务在后台执行，页面会自动刷新状态。</p>
        </template>
        <template v-else-if="preview">
          <header><div><p class="eyebrow">执行计划</p><h3>{{ actionLabel(preview.action_type) }}</h3></div></header>
          <p>{{ preview.summary }}</p>
          <ul><li v-for="warning in preview.warnings" :key="warning">{{ warning }}</li></ul>
          <pre>{{ formatUnknown(preview.checks) }}</pre>
        </template>
        <div v-else class="empty-panel">选择处理动作并生成计划。这里会显示影响范围、风险提示和执行结果。</div>
      </div>
    </div>
  </section>
</template>
