import { defineStore } from "pinia";

/**
 * 全局研究上下文：让「因子 → 回测 → 组合草案 → 持仓」这条研究主线的关键
 * 上下文能跨页流转，而不是靠用户在页面间手动复制 run_id / 因子方向。
 *
 * 只承载 UI 侧的「当前正在研究什么」，不替代服务端的不可变事实源
 * （因子方向、回测参数、组合草案的权威记录仍在后端）。
 */

export interface ResearchFactorDirection {
  factor_name: string;
  factor_version: string;
  display_name: string;
  higher_is_better: boolean;
}

interface ResearchState {
  /** 当前研究主线选中的因子方向快照（来自因子页的显式确认）。 */
  factorDirections: ResearchFactorDirection[];
  /** 最近一次发起的回测 run_id，供组合草案页一键预填。 */
  lastRunId: number | null;
  /** 最近一次回测名称。 */
  lastRunName: string | null;
  /** 当前研究的基准（可选）。 */
  benchmark: string | null;
}

export const useResearchStore = defineStore("research", {
  state: (): ResearchState => ({
    factorDirections: [],
    lastRunId: null,
    lastRunName: null,
    benchmark: null,
  }),

  getters: {
    hasActiveResearch(state): boolean {
      return state.factorDirections.length > 0 || state.lastRunId !== null;
    },
  },

  actions: {
    setFactorDirections(directions: ResearchFactorDirection[]) {
      this.factorDirections = directions;
    },
    setLastRun(runId: number, name: string | null) {
      this.lastRunId = runId;
      this.lastRunName = name;
    },
    setBenchmark(benchmark: string | null) {
      this.benchmark = benchmark;
    },
    reset() {
      this.factorDirections = [];
      this.lastRunId = null;
      this.lastRunName = null;
      this.benchmark = null;
    },
  },
});
