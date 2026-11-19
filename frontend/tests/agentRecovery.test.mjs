import { expect, test } from "bun:test";
import { canInspectOriginalTask, recoveryEvidence, retryDescription } from "../src/agentRecovery.ts";

const step = changes => ({ step_id: "research", status: "unknown", action_id: "original-confirmation", attempts: 1, summary: {}, ...changes });

test("original task inspection is distinct from dispatch or read retry", () => {
  for (const status of ["unknown", "partial", "failed", "waiting_task", "cancelled"]) {
    expect(canInspectOriginalTask(step({ status }))).toBe(true);
  }
  for (const status of ["pending", "preflighting", "awaiting_confirmation"]) {
    expect(canInspectOriginalTask(step({ status }))).toBe(false);
  }
  expect(canInspectOriginalTask(step({ action_id: null }))).toBe(false);
});

test("read recovery exposes persisted attempts and recognized failure type", () => {
  expect(retryDescription(step({}))).toBeNull();
  expect(retryDescription(step({ read_checkpoint: { attempts: 2, status: "retryable", error_kind: "temporary_lock" } }))).toContain("2/3 次");
  expect(retryDescription(step({ read_checkpoint: { attempts: 2, status: "retryable", error_kind: "temporary_lock" } }))).toContain("临时锁占用");
  expect(retryDescription(step({ read_checkpoint: { attempts: 1, status: "unknown" } }))).toContain("重新规划");
});

test("half committed artifacts remain visible without changing recovery status", () => {
  const recovery = { status: "failed", artifact_id: 77, artifact_type: "backtest", artifact_verified: true };
  expect(recoveryEvidence(step({ summary: { recovery } }))).toEqual(recovery);
  expect(recoveryEvidence(step({ summary: {} }))).toBeNull();
});
