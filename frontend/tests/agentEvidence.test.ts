import { expect, test } from "bun:test";
import { evidenceUnavailable, factValue, resultEvidence, sourceLink } from "../src/agentEvidence";

const evidence = {
  source: { kind: "backtest", id: 7 }, observed_at: "2026-11-19T08:00:00Z",
  facts: { total_return: "0.0012300000", training_artifact_verified: false, sharpe_ratio: null },
  limitations: ["历史研究"], unavailable_sections: [],
};

test("read and post-write observations expose the same original evidence", () => {
  expect(resultEvidence({ evidence })).toEqual(evidence);
  expect(resultEvidence({ observation: { evidence } })).toEqual(evidence);
  expect(evidenceUnavailable({ observation: { unavailable: true } })).toBe(true);
  expect(resultEvidence({})).toBe(null);
});

test("display preserves exact financial strings, false, zero and missing values", () => {
  expect(factValue("0.0012300000")).toBe("0.0012300000");
  expect(factValue(false)).toBe("否");
  expect(factValue(0)).toBe("0");
  expect(factValue(null)).toBe("未提供");
});

test("source routes come from allowlisted paths and valid original ids", () => {
  expect(sourceLink(evidence)?.path).toBe("/backtests/runs/7");
  expect(sourceLink({ ...evidence, source: { kind: "portfolio", id: 8 } })?.label).toContain("按 #8 查询");
  expect(sourceLink({ ...evidence, source: { kind: "backtest", id: -1 } })).toBe(null);
  expect(sourceLink({ ...evidence, source: { kind: "https://provider.example", id: 7 } })).toBe(null);
});
