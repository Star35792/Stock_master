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

interface OperationsActionsDeps {
  preview: typeof previewOperationsAction;
  start: typeof startOperationsAction;
  fetch: typeof fetchOperationsAction;
}

const defaultDeps: OperationsActionsDeps = {
  preview: previewOperationsAction,
  start: startOperationsAction,
  fetch: fetchOperationsAction,
};

function idempotencyKey(actionType: OperationsActionType) {
  const suffix = globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random()}`;
  return `frontend-${actionType}-${suffix}`;
}

export function createOperationsActions(deps: OperationsActionsDeps = defaultDeps) {
  const preview = ref<OperationsActionPreview | null>(null);
  const currentAction = ref<OperationsAction | null>(null);
  const isPreviewing = ref(false);
  const isStarting = ref(false);
  const errorMessage = ref<string | null>(null);
  let pollTimer: number | null = null;

  const isRunning = computed(() =>
    currentAction.value?.status === "queued" || currentAction.value?.status === "running",
  );

  async function previewAction(
    actionType: OperationsActionType,
    parameters: Record<string, unknown>,
  ) {
    isPreviewing.value = true;
    errorMessage.value = null;
    try {
      preview.value = await deps.preview(actionType, parameters);
      return preview.value;
    } catch (error) {
      preview.value = null;
      errorMessage.value = error instanceof Error ? error.message : "操作预检失败";
      return null;
    } finally {
      isPreviewing.value = false;
    }
  }

  async function startAction(
    actionType: OperationsActionType,
    parameters: Record<string, unknown>,
  ) {
    if (isStarting.value || isRunning.value) return null;
    isStarting.value = true;
    errorMessage.value = null;
    try {
      currentAction.value = await deps.start(
        actionType,
        parameters,
        idempotencyKey(actionType),
      );
      schedulePoll();
      return currentAction.value;
    } catch (error) {
      errorMessage.value = error instanceof Error ? error.message : "操作启动失败";
      return null;
    } finally {
      isStarting.value = false;
    }
  }

  async function executePlan(plan: OperationsRerunPlan) {
    if (!plan.supported) return null;
    const actionType = plan.action_type as OperationsActionType;
    const checked = await previewAction(actionType, plan.parameters);
    if (!checked?.executable) return null;
    return startAction(actionType, checked.parameters);
  }

  function schedulePoll() {
    stopPolling();
    if (!currentAction.value || !isRunning.value) return;
    pollTimer = window.setTimeout(async () => {
      try {
        currentAction.value = await deps.fetch(currentAction.value!.action_id);
        errorMessage.value = null;
      } catch (error) {
        errorMessage.value = error instanceof Error ? error.message : "操作状态读取失败";
      }
      schedulePoll();
    }, 1500);
  }

  function stopPolling() {
    if (pollTimer !== null) {
      window.clearTimeout(pollTimer);
      pollTimer = null;
    }
  }

  return {
    preview,
    currentAction,
    isPreviewing,
    isStarting,
    isRunning,
    errorMessage,
    previewAction,
    startAction,
    executePlan,
    stopPolling,
  };
}

export type OperationsActions = ReturnType<typeof createOperationsActions>;

let shared: OperationsActions | null = null;

export function useOperationsActions() {
  shared ??= createOperationsActions();
  return shared;
}
