import { reactive, ref, watch } from "vue";
import { evaluateSensitivity, type SensitivityAxis, type SensitivityReport } from "../sensitivityApi";

export function parseSensitivityIds(text: string): number[] {
  const parts = text.trim().split(/[,，\s]+/).filter(Boolean);
  if (parts.length < 2 || parts.length > 64 || parts.some(part => !/^[1-9]\d*$/.test(part)))
    throw new Error("请输入 2–64 个互不重复的正整数运行编号。");
  const ids = parts.map(Number);
  if (ids.some(id => !Number.isSafeInteger(id)) || new Set(ids).size !== ids.length)
    throw new Error("运行编号须为互不重复的安全正整数。");
  return ids;
}

export function createSensitivity(deps = { evaluate: evaluateSensitivity }) {
  const input = reactive({ ids: "", baseline: "", axis: "top_n" as SensitivityAxis,
    secondAxis: "" as SensitivityAxis | "", hypothesis: "", acceptance: "", stop: "" });
  const report = ref<SensitivityReport | null>(null);
  const loading = ref(false);
  const error = ref("");
  let revision = 0;
  let controller: AbortController | null = null;
  function invalidate() {
    revision++; controller?.abort(); controller = null;
    report.value = null; loading.value = false; error.value = "";
  }
  const stopWatch = watch(input, invalidate, { deep: true, flush: "sync" });
  function useSelection(ids: number[]) { input.ids = ids.join(","); input.baseline = String(ids[0] ?? ""); }
  async function calculate() {
    if (loading.value) return;
    invalidate();
    const current = revision;
    try {
      const ids = parseSensitivityIds(input.ids);
      const baseline = Number(input.baseline);
      if (!ids.includes(baseline)) throw new Error("基准运行必须在运行编号列表中。");
      if (input.axis === input.secondAxis) throw new Error("两个扫描轴不能相同。");
      const axes = input.secondAxis ? [input.axis, input.secondAxis] : [input.axis];
      controller = new AbortController();
      const signal = controller.signal;
      loading.value = true;
      const result = await deps.evaluate({ run_ids: ids, baseline_run_id: baseline, axes,
        hypothesis: input.hypothesis, acceptance_note: input.acceptance, stop_note: input.stop }, signal);
      if (current === revision && !signal.aborted) report.value = result;
    } catch (cause) {
      if (current === revision) error.value = cause instanceof Error ? cause.message : "比较失败";
    } finally { if (current === revision) { loading.value = false; controller = null; } }
  }
  function dispose() { invalidate(); stopWatch(); }
  return { input, report, loading, error, useSelection, calculate, dispose };
}
