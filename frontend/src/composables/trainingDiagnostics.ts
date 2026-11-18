import { ref } from "vue";
import {
  fetchTrainingDiagnostics,
  type TrainingDiagnosticsReport,
} from "../trainingDiagnosticsApi";

export function createTrainingDiagnostics(
  deps = { fetch: fetchTrainingDiagnostics },
) {
  const report = ref<TrainingDiagnosticsReport | null>(null);
  const loading = ref(false);
  const error = ref("");
  let controller: AbortController | null = null;
  let generation = 0;
  let runId = 0;

  function reset(id: number) {
    generation += 1;
    controller?.abort();
    controller = null;
    runId = id;
    report.value = null;
    loading.value = false;
    error.value = "";
  }

  async function load() {
    if (loading.value || !Number.isSafeInteger(runId) || runId <= 0) return;
    const request = ++generation;
    controller = new AbortController();
    const signal = controller.signal;
    loading.value = true;
    error.value = "";
    try {
      const result = await deps.fetch(runId, signal);
      if (request === generation && !signal.aborted) report.value = result;
    } catch (cause) {
      if (request === generation && !signal.aborted) {
        error.value = cause instanceof Error ? cause.message : "训练诊断读取失败，请重试。";
      }
    } finally {
      if (request === generation) {
        loading.value = false;
        controller = null;
      }
    }
  }

  return { report, loading, error, reset, load };
}
