<script setup lang="ts">
import { DatabaseZap, Loader2, RefreshCw, Wrench } from "@lucide/vue";
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from "vue";

import { useOperationsActions } from "../../composables/operationsActions";
import type { OperationsActionType } from "../../types";
import { formatUnknown, shanghaiDate, statusLabel } from "./formatters";
import { operationDiagnostics } from "./operationsDiagnostics";

const props = defineProps<{ enabled: boolean }>();

const {
  preview,
  currentAction,
  isPreviewing,
  isStarting,
  isRestoring,
  isRunning,
  errorMessage,
  pendingStart,
  recoveryActionId,
  previewAction,
  previewMatches,
  invalidatePreview,
  startAction,
  retryPendingStart,
  restoreAction,
  resumePolling,
  stopPolling,
} = useOperationsActions();

const emit = defineEmits<{ completed: [] }>();

const today = shanghaiDate();
const startDate = shanghaiDate(new Date(Date.now() - 30 * 86_400_000));
const restoreId = ref(recoveryActionId.value ? String(recoveryActionId.value) : "");

const form = reactive({
  actionType: "daily_data_sync" as OperationsActionType,
  symbols: "",
  startDate,
  endDate: today,
  originalRunId: "",
  sources: ["hithink", "eastmoney"],
});

const actionParameters = computed<Record<string, unknown>>(() => {
  if (form.actionType === "daily_data_sync") return {};
  if (form.actionType === "price_value_input_prepare") return {};
  if (form.actionType === "alternative_data_sync") return { sources: [...form.sources].sort() };
  if (form.actionType === "factor_values_rebuild") return { original_run_id: Number(form.originalRunId) };
  if (form.actionType === "daily_bar_gap_backfill") {
    return {
      symbols: [...new Set(form.symbols.split(/[,，\s]+/).filter(Boolean).map((symbol) => symbol.toUpperCase()))].sort(),
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

const validationError = computed(() => {
  if (form.actionType === "daily_data_sync" || form.actionType === "price_value_input_prepare") return "";
  if (form.actionType === "alternative_data_sync") return form.sources.length ? "" : "请至少选择一个数据来源。";
  if (form.actionType === "factor_values_rebuild") return Number.isSafeInteger(Number(form.originalRunId)) && Number(form.originalRunId) > 0 ? "" : "请填写需要重算的原因子运行编号。";
  const start = Date.parse(`${form.startDate}T00:00:00Z`);
  const end = Date.parse(`${form.endDate}T00:00:00Z`);
  if (!Number.isFinite(start) || !Number.isFinite(end) || start > end) return "请选择有效的开始和结束日期。";
  const maxDays = form.actionType === "daily_bar_gap_backfill" ? 31 : 366;
  if ((end - start) / 86_400_000 > maxDays) return form.actionType === "daily_bar_gap_backfill" ? "缺口回填最多选择 32 个自然日。" : "因子画像最多选择 367 个自然日。";
  if (form.actionType === "daily_bar_gap_backfill" && !(actionParameters.value.symbols as string[]).length) return "请填写需要回填的证券代码。";
  return "";
});
const controlsDisabled = computed(() => !props.enabled || isRunning.value || isStarting.value || isRestoring.value);
const canConfirm = computed(() => !controlsDisabled.value && !isPreviewing.value && !pendingStart.value
  && !validationError.value && !!preview.value?.executable && previewMatches(form.actionType, actionParameters.value));
const diagnostics = computed(() => preview.value ? operationDiagnostics(preview.value.checks, preview.value.action_type) : null);

watch(() => JSON.stringify([form.actionType, actionParameters.value]), invalidatePreview, { flush: "sync" });
watch(recoveryActionId, (id) => { if (id) restoreId.value = String(id); });

async function runPreview() {
  if (controlsDisabled.value || validationError.value) return;
  await previewAction(form.actionType, actionParameters.value);
}

async function confirmStart() {
  if (!canConfirm.value || !preview.value) return;
  if (!window.confirm(`确认执行：${preview.value.summary}`)) return;
  if (!canConfirm.value) return;
  await startAction(preview.value.action_type, preview.value.parameters);
}

async function retryUnknownStart() {
  if (!props.enabled || !pendingStart.value || isStarting.value) return;
  if (!window.confirm(`上次启动结果未知，将以相同幂等键重试原请求：\n${formatUnknown(pendingStart.value.parameters)}`)) return;
  await retryPendingStart();
}

async function readAction() {
  await restoreAction(Number(restoreId.value));
}

function switchSuggestedAction() {
  const next = diagnostics.value?.nextAction;
  if (!next || controlsDisabled.value || pendingStart.value) return;
  const originalRunId = preview.value?.parameters.original_run_id;
  if (Number.isSafeInteger(originalRunId) && Number(originalRunId) > 0) form.originalRunId = String(originalRunId);
  if (next.sources) form.sources = [...next.sources];
  form.actionType = next.actionType;
}

function actionLabel(actionType: OperationsActionType) {
  return {
    daily_data_sync: "同步最新可信数据",
    daily_bar_gap_backfill: "回填日线缺口",
    factor_profile_rebuild: "重建因子画像",
    factor_values_rebuild: "按原运行重算因子值",
    alternative_data_sync: "补采情绪与资金数据",
    price_value_input_prepare: "补齐当前价格价值研究输入",
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
onMounted(() => { void resumePolling(); });
</script>

<template>
  <section class="dashboard-section action-workbench">
    <div class="section-title">
      <Wrench :size="20" aria-hidden="true" />
      <h2>问题处理</h2>
      <span class="section-note">先预检，再确认执行；任务全程留审计记录</span>
    </div>

    <p v-if="!enabled" class="inline-error" role="status">当前为演示数据或正在连接后端，处理动作暂不可用。</p>
    <div class="action-workbench__layout">
      <div class="action-form">
        <div class="action-kind-grid">
          <button
            v-for="item in ([
              ['daily_data_sync', '数据同步', DatabaseZap],
              ['daily_bar_gap_backfill', '缺口回填', RefreshCw],
              ['factor_profile_rebuild', '因子画像', Wrench],
              ['factor_values_rebuild', '因子重算', Wrench],
              ['alternative_data_sync', '情绪与资金补采', DatabaseZap],
              ['price_value_input_prepare', '价格价值输入', RefreshCw],
            ] as const)"
            :key="item[0]"
            :class="{ active: form.actionType === item[0] }"
            type="button"
            :disabled="controlsDisabled"
            @click="form.actionType = item[0]"
          >
            <component :is="item[2]" :size="17" />
            {{ item[1] }}
          </button>
        </div>

        <label v-if="form.actionType === 'daily_bar_gap_backfill'">
          证券代码（逗号或空格分隔）
          <input v-model="form.symbols" placeholder="例如 600000.SH" :disabled="controlsDisabled" />
        </label>
        <label v-if="form.actionType === 'factor_values_rebuild'">
          原因子运行编号
          <input v-model="form.originalRunId" type="number" min="1" step="1" placeholder="从失败或无观察记录读取" :disabled="controlsDisabled" />
        </label>
        <div v-if="form.actionType === 'alternative_data_sync'" class="action-date-grid">
          <label class="action-source-option"><input v-model="form.sources" type="checkbox" value="hithink" :disabled="controlsDisabled" />同花顺情绪数据</label>
          <label class="action-source-option"><input v-model="form.sources" type="checkbox" value="eastmoney" :disabled="controlsDisabled" />东方财富资金数据</label>
        </div>
        <p v-if="form.actionType === 'factor_values_rebuild'" class="section-note">沿用原运行的因子、区间和复权口径；预检会说明输入缺失及后续处理，重算不会自动提升研究资格。</p>
        <p v-if="form.actionType === 'alternative_data_sync'" class="section-note">补采来源当前提供的情绪与资金数据，并保留 raw 来源证据；不据此补造历史时点数据。</p>
        <p v-if="form.actionType === 'price_value_input_prepare'" class="section-note">补齐最新已完成交易日的当前研究观察，日期以服务端预检为准；这不是历史 PIT 证据。</p>
        <button v-if="form.actionType !== 'factor_values_rebuild' && Number(form.originalRunId) > 0" type="button" class="secondary-action" :disabled="controlsDisabled" @click="form.actionType = 'factor_values_rebuild'">返回原因子运行 #{{ form.originalRunId }} 预检</button>
        <div v-if="form.actionType === 'daily_bar_gap_backfill' || form.actionType === 'factor_profile_rebuild'" class="action-date-grid">
          <label>开始日期<input v-model="form.startDate" type="date" :disabled="controlsDisabled" /></label>
          <label>结束日期<input v-model="form.endDate" type="date" :disabled="controlsDisabled" /></label>
        </div>
        <p v-if="validationError" class="inline-error" role="status">{{ validationError }}</p>

        <div class="action-buttons">
          <button class="secondary-action" type="button" :disabled="controlsDisabled || isPreviewing || !!validationError || !!pendingStart" @click="runPreview">
            <Loader2 v-if="isPreviewing" class="spin" :size="16" />
            {{ isPreviewing ? "正在预检" : "生成执行计划" }}
          </button>
          <button
            class="primary-action"
            type="button"
            :disabled="!canConfirm"
            @click="confirmStart"
          >
            {{ isStarting ? "正在启动" : "确认执行" }}
          </button>
        </div>
        <div v-if="pendingStart" class="rerun-card">
          <p>上次启动结果未知。重试会复用原参数和幂等键，查询或恢复同一任务。</p>
          <pre>{{ formatUnknown(pendingStart.parameters) }}</pre>
          <button type="button" class="secondary-action" :disabled="controlsDisabled" @click="retryUnknownStart">重试原请求</button>
        </div>
        <div class="action-date-grid">
          <label>恢复任务编号<input v-model="restoreId" type="number" min="1" step="1" placeholder="例如 42" :disabled="isStarting || isRestoring" /></label>
          <button type="button" class="secondary-action" :disabled="isStarting || isRestoring || !restoreId" @click="readAction">{{ isRestoring ? "正在读取" : "读取任务" }}</button>
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
          <p v-if="currentAction.status === 'partial'" class="inline-error">任务部分完成，请核对下方未完成项；这不表示原问题已解除。</p>
          <p v-if="currentAction.result?.problem_closed === false" class="inline-error">复查仍有缺失或阻断项，原问题尚未解除。</p>
          <p v-if="currentAction.error_message" class="inline-error">{{ currentAction.error_message }}</p>
          <pre v-if="currentAction.result">{{ formatUnknown(currentAction.result) }}</pre>
          <p v-else-if="isRunning" class="action-running"><Loader2 class="spin" :size="16" />任务在后台执行，页面会自动刷新状态。</p>
        </template>
        <template v-else-if="preview">
          <header><div><p class="eyebrow">执行计划</p><h3>{{ actionLabel(preview.action_type) }}</h3></div></header>
          <p>{{ preview.summary }}</p>
          <ul><li v-for="warning in preview.warnings" :key="warning">{{ warning }}</li></ul>
          <div v-if="diagnostics" class="action-diagnostics">
            <ul v-if="diagnostics.reasons.length" class="inline-error" role="status"><li v-for="reason in diagnostics.reasons" :key="reason">{{ reason }}</li></ul>
            <dl v-if="diagnostics.rows.length" class="diagnostic-summary"><div v-for="row in diagnostics.rows" :key="row.label"><dt>{{ row.label }}</dt><dd>{{ row.value }}</dd></div></dl>
            <p v-if="diagnostics.detail">具体原因：{{ diagnostics.detail }}</p>
            <p v-if="diagnostics.nextStep" class="section-note">{{ diagnostics.nextStep }}</p>
            <template v-if="diagnostics.nextAction">
              <p class="section-note">{{ diagnostics.nextAction.explanation }}</p>
              <button type="button" class="secondary-action" :disabled="controlsDisabled || !!pendingStart" @click="switchSuggestedAction">{{ diagnostics.nextAction.label }}</button>
            </template>
          </div>
          <details><summary>原始检查证据</summary><pre>{{ formatUnknown(preview.checks) }}</pre></details>
        </template>
        <div v-else class="empty-panel">选择处理动作并生成计划。这里会显示影响范围、风险提示和执行结果。</div>
      </div>
    </div>
  </section>
</template>
