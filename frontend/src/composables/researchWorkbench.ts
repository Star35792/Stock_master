import { computed, ref } from "vue";

import {
  fetchResearchPipeline,
  fetchResearchWorkbench,
  type PipelineView,
  type WorkbenchView,
} from "../researchWorkflowApi";

export interface ResearchWorkbenchDeps {
  fetchWorkbench: typeof fetchResearchWorkbench;
  fetchPipeline: typeof fetchResearchPipeline;
}

const defaultDeps: ResearchWorkbenchDeps = {
  fetchWorkbench: fetchResearchWorkbench,
  fetchPipeline: fetchResearchPipeline,
};

export function createResearchWorkbench(deps: ResearchWorkbenchDeps = defaultDeps) {
  const workbench = ref<WorkbenchView | null>(null);
  const pipeline = ref<PipelineView | null>(null);
  const selectedRunId = ref<number | null>(null);
  const workbenchLoading = ref(false);
  const pipelineLoading = ref(false);
  const workbenchError = ref<string | null>(null);
  const pipelineError = ref<string | null>(null);

  let workbenchController: AbortController | null = null;
  let pipelineController: AbortController | null = null;
  let workbenchGeneration = 0;
  let pipelineGeneration = 0;

  const sectionErrors = computed(() => {
    const current = workbench.value;
    if (!current) return [];
    return [
      current.recent_backtests_error ? "最近回测" : null,
      current.portfolio_plans_error ? "组合草案" : null,
      current.position_books_error ? "持仓账本" : null,
      current.alerts_error ? "告警" : null,
    ].filter((label): label is string => label !== null);
  });

  const source = computed<"loading" | "api" | "partial" | "error">(() => {
    if (workbenchLoading.value && !workbench.value) return "loading";
    if (workbenchError.value && !workbench.value) return "error";
    return sectionErrors.value.length ? "partial" : "api";
  });

  async function loadWorkbench() {
    workbenchController?.abort();
    const controller = new AbortController();
    const generation = ++workbenchGeneration;
    workbenchController = controller;
    workbenchLoading.value = true;
    workbenchError.value = null;
    try {
      const result = await deps.fetchWorkbench(controller.signal);
      if (controller.signal.aborted || generation !== workbenchGeneration) return;
      workbench.value = result;
    } catch (error) {
      if (controller.signal.aborted || generation !== workbenchGeneration) return;
      workbenchError.value = error instanceof Error ? error.message : "研究首页读取失败";
    } finally {
      if (generation === workbenchGeneration) workbenchLoading.value = false;
    }
  }

  async function loadPipeline(runId: number) {
    if (!Number.isSafeInteger(runId) || runId <= 0) {
      clearPipeline();
      pipelineError.value = "回测编号必须是正整数。";
      return;
    }
    pipelineController?.abort();
    const controller = new AbortController();
    const generation = ++pipelineGeneration;
    pipelineController = controller;
    selectedRunId.value = runId;
    pipeline.value = null;
    pipelineError.value = null;
    pipelineLoading.value = true;
    try {
      const result = await deps.fetchPipeline(runId, controller.signal);
      if (controller.signal.aborted || generation !== pipelineGeneration || selectedRunId.value !== runId) return;
      pipeline.value = result;
    } catch (error) {
      if (controller.signal.aborted || generation !== pipelineGeneration) return;
      pipelineError.value = error instanceof Error ? error.message : "研究流水线读取失败";
    } finally {
      if (generation === pipelineGeneration) pipelineLoading.value = false;
    }
  }

  function clearPipeline() {
    pipelineController?.abort();
    pipelineController = null;
    pipelineGeneration += 1;
    selectedRunId.value = null;
    pipeline.value = null;
    pipelineError.value = null;
    pipelineLoading.value = false;
  }

  function dispose() {
    workbenchController?.abort();
    pipelineController?.abort();
    workbenchGeneration += 1;
    pipelineGeneration += 1;
  }

  return {
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
  };
}

export type ResearchWorkbench = ReturnType<typeof createResearchWorkbench>;
