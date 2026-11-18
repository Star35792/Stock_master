import { ref } from "vue";
import { auditBacktestSignals, type SignalAuditReport } from "../signalAuditApi";

export function createSignalAudit(deps = { audit: auditBacktestSignals }) {
  const report = ref<SignalAuditReport | null>(null);
  const loading = ref(false);
  const error = ref("");
  const note = ref("");
  let controller: AbortController | null = null;
  let generation = 0;
  let runId = 0;

  function reset(id: number) {
    generation++;
    controller?.abort();
    controller = null;
    runId = id;
    report.value = null;
    loading.value = false;
    error.value = "";
    note.value = "";
  }

  function stopWaiting() {
    reset(runId);
    note.value = "已停止等待。服务端可能仍在计算，稍后可重试。";
  }

  async function start() {
    if (loading.value || !Number.isSafeInteger(runId) || runId <= 0) return;
    const request = ++generation;
    controller = new AbortController();
    const signal = controller.signal;
    loading.value = true;
    error.value = "";
    note.value = "";
    report.value = null;
    try {
      const result = await deps.audit(runId, signal);
      if (request === generation && !signal.aborted) report.value = result;
    } catch (cause) {
      if (request === generation && !signal.aborted) {
        error.value = cause instanceof Error ? cause.message : "信号审计失败，请重试。";
      }
    } finally {
      if (request === generation) { loading.value = false; controller = null; }
    }
  }
  return { report, loading, error, note, reset, start, stopWaiting };
}
