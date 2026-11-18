export interface DailyRankICEvidence {
  trade_date: string;
  rank_ic: string | null;
  common_symbol_count: number;
  status: string;
}

export interface TrainingDiagnosticFactor {
  factor_name: string;
  factor_version: string;
  status: "recorded" | "insufficient";
  reason: string | null;
  observations: number;
  hac_pair_count: number;
  mean_rank_ic: string | null;
  hac_standard_error: string | null;
  z_score: string | null;
  p_value: string | null;
  q_value: string | null;
  reject_null: boolean;
  rank_ic_series: DailyRankICEvidence[];
}

export interface TrainingDiagnosticsReport {
  run_id: number;
  training_artifact_id: number | null;
  artifact_hash: string | null;
  contract_version: string | null;
  status: "recorded" | "insufficient" | "not_recorded" | "unsupported";
  reason: string | null;
  research_only: true;
  selection_unchanged: true;
  qualification_unchanged: true;
  policy: Record<string, unknown>;
  family: Record<string, unknown>;
  eligible_dates: string[];
  evidence_sha256: string | null;
  price_snapshot: Record<string, unknown>;
  factors: TrainingDiagnosticFactor[];
  declarations: string[];
}

export async function fetchTrainingDiagnostics(
  runId: number,
  signal: AbortSignal,
): Promise<TrainingDiagnosticsReport> {
  const response = await fetch(`/api/v1/backtests/runs/${runId}/training-diagnostics`, {
    headers: { Accept: "application/json" },
    signal,
  });
  if (!response.ok) {
    const body = await response.json().catch(() => null) as { detail?: unknown } | null;
    throw new Error(
      typeof body?.detail === "string"
        ? body.detail
        : `训练诊断读取失败（HTTP ${response.status}）`,
    );
  }
  return response.json() as Promise<TrainingDiagnosticsReport>;
}
