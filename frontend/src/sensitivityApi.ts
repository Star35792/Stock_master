export const sensitivityAxes = {
  top_n: "选股数量", rebalance_interval: "调仓间隔", coverage_ratio: "覆盖比例",
  minimum_score: "最低分数", invested_weight: "投入权重", commission_rate: "佣金率", slippage_bps: "滑点（基点）",
} as const;
export type SensitivityAxis = keyof typeof sensitivityAxes;
export interface SensitivityInput {
  run_ids: number[]; baseline_run_id: number; axes: SensitivityAxis[];
  hypothesis: string; acceptance_note: string; stop_note: string;
}
export interface SensitivityCell {
  candidate: string; fold: string; run_ids: number[]; axis_values: (string | null)[];
  status: "controlled" | "exploratory" | "missing" | "duplicate" | "unavailable";
  reasons: string[]; total_return: string | null; maximum_drawdown: string | null;
  sharpe_ratio: string | null; delta_return: string | null; contract_differences: string[];
}
export interface SensitivityReport {
  version: string; generated_at: string; research_only: true; ranked: false;
  recording_stage: "retrospective"; request: SensitivityInput; contract_sha256: string;
  baseline_candidate: string | null; folds: Record<string, [string, string]>;
  candidates: Record<string, (string | null)[]>; cells: SensitivityCell[];
  summaries: { candidate: string; axis_values: (string | null)[]; complete: boolean;
    controlled_folds: number; expected_folds: number; median_return: string | null;
    worst_return: string | null; return_range: string | null }[];
  unclassified_run_ids: number[]; unclassified_reasons: Record<string, string>; declarations: string[];
}
export async function evaluateSensitivity(input: SensitivityInput, signal: AbortSignal): Promise<SensitivityReport> {
  const response = await fetch("/api/v1/backtests/runs/sensitivity", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(input), signal,
  });
  if (!response.ok) {
    const data = await response.json().catch(() => null);
    const detail = data?.detail;
    throw new Error(typeof detail === "string" ? detail : Array.isArray(detail)
      ? detail.map((item: { msg?: string }) => item.msg).join("；")
      : `敏感性比较失败（HTTP ${response.status}）`);
  }
  return response.json() as Promise<SensitivityReport>;
}
