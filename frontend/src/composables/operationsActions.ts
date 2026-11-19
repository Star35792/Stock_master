import { computed, ref } from "vue";

import {
  fetchOperationsAction,
  previewOperationsAction,
  startOperationsAction,
} from "../api";
import type {
  OperationsAction,
  OperationsActionPreview,
  OperationsActionType,
  OperationsRerunPlan,
} from "../types";

interface ActionStorage {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
  removeItem(key: string): void;
}

interface OperationsActionsDeps {
  preview: typeof previewOperationsAction;
  start: typeof startOperationsAction;
  fetch: typeof fetchOperationsAction;
  storage?: ActionStorage | null;
}

interface PendingStart {
  action_type: OperationsActionType;
  parameters: Record<string, unknown>;
  idempotency_key: string;
}

const STORAGE_KEY = "stock-master.operations-action.v1";
const ACTION_TYPES: OperationsActionType[] = [
  "daily_data_sync", "daily_bar_gap_backfill", "factor_profile_rebuild",
  "factor_values_rebuild", "alternative_data_sync", "price_value_input_prepare",
];
const defaultDeps: OperationsActionsDeps = {
  preview: previewOperationsAction,
  start: startOperationsAction,
  fetch: fetchOperationsAction,
};

function idempotencyKey(actionType: OperationsActionType) {
  const suffix = globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random()}`;
  return `frontend-${actionType}-${suffix}`;
}

function signature(actionType: OperationsActionType, parameters: Record<string, unknown>): string {
  const canonical = (value: unknown): unknown => {
    if (Array.isArray(value)) return value.map(canonical);
    if (value && typeof value === "object") {
      return Object.fromEntries(Object.entries(value).sort(([a], [b]) => a.localeCompare(b)).map(([key, item]) => [key, canonical(item)]));
    }
    return value;
  };
  return JSON.stringify([actionType, canonical(parameters)]);
}

function browserStorage(): ActionStorage | null {
  try { return globalThis.window?.sessionStorage ?? null; } catch { return null; }
}

export function createOperationsActions(deps: OperationsActionsDeps = defaultDeps) {
  const preview = ref<OperationsActionPreview | null>(null);
  const currentAction = ref<OperationsAction | null>(null);
  const isPreviewing = ref(false);
  const isStarting = ref(false);
  const isRestoring = ref(false);
  const errorMessage = ref<string | null>(null);
  const pendingStart = ref<PendingStart | null>(null);
  const recoveryActionId = ref<number | null>(null);
  const storage = deps.storage === undefined ? browserStorage() : deps.storage;
  let pollTimer: number | null = null;
  let pollingEnabled = true;
  let pollGeneration = 0;
  let previewGeneration = 0;
  let restoreGeneration = 0;
  let previewSignature: string | null = null;

  const isRunning = computed(() =>
    currentAction.value?.status === "queued" || currentAction.value?.status === "running",
  );

  function persist() {
    try {
      if (!recoveryActionId.value && !pendingStart.value) storage?.removeItem(STORAGE_KEY);
      else storage?.setItem(STORAGE_KEY, JSON.stringify({ action_id: recoveryActionId.value, pending: pendingStart.value }));
    } catch { /* The live task remains usable when browser storage is unavailable. */ }
  }

  try {
    const raw = storage?.getItem(STORAGE_KEY);
    if (raw && raw.length <= 16000) {
      const saved = JSON.parse(raw);
      if (Number.isSafeInteger(saved.action_id) && saved.action_id > 0) recoveryActionId.value = saved.action_id;
      const pending = saved.pending;
      if (pending && ACTION_TYPES.includes(pending.action_type) && typeof pending.idempotency_key === "string"
        && pending.idempotency_key.length <= 200 && pending.parameters && typeof pending.parameters === "object"
        && !Array.isArray(pending.parameters)) pendingStart.value = pending;
    }
  } catch { /* Invalid saved state cannot authorize a request. */ }

  function invalidatePreview() {
    previewGeneration += 1;
    preview.value = null;
    previewSignature = null;
    isPreviewing.value = false;
  }

  function previewMatches(actionType: OperationsActionType, parameters: Record<string, unknown>): boolean {
    return !!preview.value && previewSignature === signature(actionType, parameters);
  }

  async function previewAction(actionType: OperationsActionType, parameters: Record<string, unknown>) {
    if (isStarting.value || isRunning.value) return null;
    const request = JSON.parse(JSON.stringify(parameters));
    const generation = ++previewGeneration;
    preview.value = null;
    previewSignature = null;
    currentAction.value = null;
    isPreviewing.value = true;
    errorMessage.value = null;
    try {
      const checked = await deps.preview(actionType, request);
      if (generation !== previewGeneration) return null;
      preview.value = checked;
      previewSignature = signature(actionType, request);
      return checked;
    } catch (error) {
      if (generation !== previewGeneration) return null;
      errorMessage.value = error instanceof Error ? error.message : "操作预检失败";
      return null;
    } finally {
      if (generation === previewGeneration) isPreviewing.value = false;
    }
  }

  async function startAction(actionType: OperationsActionType, parameters: Record<string, unknown>) {
    if (isStarting.value || isRunning.value || isRestoring.value) return null;
    if (pendingStart.value && signature(pendingStart.value.action_type, pendingStart.value.parameters) !== signature(actionType, parameters)) {
      errorMessage.value = "上次启动结果尚未确认，请先重试原请求，避免重复执行。";
      return null;
    }
    const request = pendingStart.value ?? {
      action_type: actionType,
      parameters: JSON.parse(JSON.stringify(parameters)),
      idempotency_key: idempotencyKey(actionType),
    };
    pendingStart.value = request;
    persist();
    isStarting.value = true;
    errorMessage.value = null;
    try {
      const result = await deps.start(request.action_type, request.parameters, request.idempotency_key);
      currentAction.value = result;
      recoveryActionId.value = result.action_id;
      pendingStart.value = null;
      persist();
      schedulePoll();
      return result;
    } catch (error) {
      // A server rejection confirms that this submission was not accepted.
      const status = (error as { status?: number } | null)?.status;
      if (status && status >= 400 && status < 500) {
        pendingStart.value = null;
        persist();
      }
      errorMessage.value = error instanceof Error ? error.message : "操作启动失败";
      return null;
    } finally {
      isStarting.value = false;
    }
  }

  async function retryPendingStart() {
    const pending = pendingStart.value;
    return pending ? startAction(pending.action_type, pending.parameters) : null;
  }

  async function executePlan(
    plan: OperationsRerunPlan,
    confirm: (checked: OperationsActionPreview) => boolean | Promise<boolean> = () => false,
  ) {
    if (!plan.supported || !ACTION_TYPES.includes(plan.action_type as OperationsActionType)) return null;
    const actionType = plan.action_type as OperationsActionType;
    const checked = await previewAction(actionType, plan.parameters);
    if (!checked?.executable || !await confirm(checked)) return null;
    if (!previewMatches(actionType, plan.parameters)) return null;
    return startAction(actionType, checked.parameters);
  }

  async function restoreAction(actionId = recoveryActionId.value) {
    if (!actionId || !Number.isSafeInteger(actionId) || actionId <= 0) {
      errorMessage.value = "任务编号必须是正整数。";
      return null;
    }
    if (isStarting.value) return null;
    if (isRunning.value && currentAction.value?.action_id !== actionId) {
      errorMessage.value = `任务 #${currentAction.value?.action_id} 仍在后台运行，请先完成当前任务的状态核对。`;
      return null;
    }
    stopPolling();
    const generation = ++restoreGeneration;
    isRestoring.value = true;
    errorMessage.value = null;
    try {
      const result = await deps.fetch(actionId);
      if (generation !== restoreGeneration) return null;
      currentAction.value = result;
      recoveryActionId.value = result.action_id;
      persist();
      pollingEnabled = true;
      schedulePoll();
      return result;
    } catch (error) {
      if (generation !== restoreGeneration) return null;
      errorMessage.value = error instanceof Error ? error.message : "操作状态读取失败";
      return null;
    } finally {
      if (generation === restoreGeneration) isRestoring.value = false;
    }
  }

  function schedulePoll() {
    clearPollTimer();
    if (!pollingEnabled || !currentAction.value || !isRunning.value || typeof window === "undefined") return;
    const actionId = currentAction.value.action_id;
    const generation = pollGeneration;
    pollTimer = window.setTimeout(async () => {
      pollTimer = null;
      try {
        const result = await deps.fetch(actionId);
        if (!pollingEnabled || generation !== pollGeneration || currentAction.value?.action_id !== actionId) return;
        currentAction.value = result;
        errorMessage.value = null;
      } catch (error) {
        if (!pollingEnabled || generation !== pollGeneration) return;
        errorMessage.value = error instanceof Error ? error.message : "操作状态读取失败";
      }
      schedulePoll();
    }, 1500);
  }

  function clearPollTimer() {
    if (pollTimer !== null) {
      window.clearTimeout(pollTimer);
      pollTimer = null;
    }
  }

  function stopPolling() {
    pollingEnabled = false;
    pollGeneration += 1;
    restoreGeneration += 1;
    isRestoring.value = false;
    clearPollTimer();
  }

  async function resumePolling() {
    pollingEnabled = true;
    if (currentAction.value) schedulePoll();
    else if (recoveryActionId.value) await restoreAction();
  }

  return {
    preview, currentAction, isPreviewing, isStarting, isRestoring, isRunning, errorMessage,
    pendingStart, recoveryActionId, previewAction, previewMatches, invalidatePreview,
    startAction, retryPendingStart, executePlan, restoreAction, resumePolling, stopPolling,
  };
}

export type OperationsActions = ReturnType<typeof createOperationsActions>;

let shared: OperationsActions | null = null;

export function useOperationsActions() {
  shared ??= createOperationsActions();
  return shared;
}
