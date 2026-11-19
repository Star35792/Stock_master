export interface RecoveryEvidence {
  status: "verified" | "running" | "partial" | "failed" | "unknown" | "conflict";
  reason: string;
  confirmation_id: string;
  task_id: number | null;
  artifact_type: "backtest" | "portfolio" | null;
  artifact_id: number | null;
  artifact_status: string | null;
  artifact_verified: boolean;
}

export interface RecoveryStep {
  step_id: string;
  status: string;
  action_id?: string | null;
  attempts: number;
  read_checkpoint?: Record<string, unknown> | null;
  summary: Record<string, unknown>;
}

export function canInspectOriginalTask(step: RecoveryStep): boolean {
  return !!step.action_id && ["starting", "reading", "waiting_task", "unknown", "failed", "partial", "success", "cancelled", "blocked"].includes(step.status);
}

export function retryDescription(step: RecoveryStep): string | null {
  const checkpoint = step.read_checkpoint;
  if (!checkpoint || typeof checkpoint.attempts !== "number") return null;
  const attempts = Math.min(3, Math.max(0, checkpoint.attempts));
  const labels: Record<string, string> = {
    ready: "等待查询", calling: "查询中；中断后需核验", retryable: "瞬时失败，等待有限重试",
    success: "查询已返回", failed: "参数、数据或服务条件未满足", exhausted: "次数或时间已达上限",
    unknown: "上次查询结果未知，请重新规划", cancelled: "后续查询已取消",
  };
  const kind: Record<string, string> = {
    network: "网络连接", rate_limit: "请求限流", upstream: "上游暂不可用",
    temporary_lock: "临时锁占用", permanent: "不可自动重试",
  };
  const status = labels[String(checkpoint.status)] ?? "等待核验";
  const error = kind[String(checkpoint.error_kind)];
  return `只读查询 ${attempts}/3 次 · ${status}${error ? ` · ${error}` : ""}`;
}

export function recoveryEvidence(step: RecoveryStep): RecoveryEvidence | null {
  const value = step.summary.recovery;
  if (!value || typeof value !== "object" || !("status" in value)) return null;
  return value as RecoveryEvidence;
}
