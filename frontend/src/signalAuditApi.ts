export interface SignalAuditCheck {
  kind: string;
  cutoff: string;
  compared_values: number;
  differences: number;
  changed_input_rows: number;
  examples: { signal_date: string; symbol: string; expected: string | null; actual: string | null }[];
}

export interface SignalAuditReport {
  run_id: number;
  generated_at: string;
  status: "passed" | "failed" | "insufficient" | "error";
  reason: string;
  version: string;
  scope: "factor_frame_to_signal";
  historical_replay_verified: false;
  input_verified: boolean;
  baseline_matches_run: boolean;
  recorded_code_version: string | null;
  current_code_version: string;
  input_sha256: string | null;
  input_rows: number;
  signal_count: number;
  evaluations: number;
  cutoffs: string[];
  checks: SignalAuditCheck[];
  declarations: string[];
  limits: Record<string, number>;
}

export async function auditBacktestSignals(runId: number, signal: AbortSignal): Promise<SignalAuditReport> {
  const response = await fetch(`/api/v1/backtests/runs/${runId}/signal-audit`, {
    method: "POST", headers: { Accept: "application/json" }, signal,
  });
  if (!response.ok) {
    const body = await response.json().catch(() => null) as { detail?: unknown } | null;
    throw new Error(typeof body?.detail === "string" ? body.detail : `审计请求失败（HTTP ${response.status}）`);
  }
  return response.json() as Promise<SignalAuditReport>;
}
