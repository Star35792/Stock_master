export interface ResultEvidence {
  source: { kind: string; id?: number; symbol?: string };
  observed_at: string;
  facts: Record<string, string | number | boolean | null>;
  limitations: string[];
  unavailable_sections: string[];
}

function object(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

export function resultEvidence(summary: Record<string, unknown>): ResultEvidence | null {
  const observation = object(summary.observation) ? summary.observation : summary;
  const value = observation.evidence;
  if (!object(value) || !object(value.source) || !object(value.facts) || typeof value.observed_at !== "string") return null;
  if (!Array.isArray(value.limitations) || !value.limitations.every(item => typeof item === "string")) return null;
  if (!Array.isArray(value.unavailable_sections) || !value.unavailable_sections.every(item => typeof item === "string")) return null;
  if (!Object.values(value.facts).every(item => item === null || ["string", "number", "boolean"].includes(typeof item))) return null;
  return value as unknown as ResultEvidence;
}

export function evidenceUnavailable(summary: Record<string, unknown>): boolean {
  return object(summary.observation) && summary.observation.unavailable === true;
}

export function sourceLink(evidence: ResultEvidence): { path: string; label: string } | null {
  const { kind, id } = evidence.source;
  if (kind === "market") return { path: "/market", label: `行情页 · ${evidence.source.symbol ?? ""}` };
  if (!Number.isSafeInteger(id) || (id ?? 0) <= 0) return null;
  if (kind === "backtest") return { path: `/backtests/runs/${id}`, label: `原回测 #${id}` };
  if (kind === "portfolio") return { path: "/portfolio", label: `组合草案页 · 按 #${id} 查询` };
  if (kind === "positions") return { path: "/positions", label: `持仓页 · 选择账本 #${id}` };
  if (kind === "operations") return { path: "/operations", label: `运维任务记录 #${id}` };
  return null;
}

export const factLabels: Record<string, string> = {
  status: "原记录状态", start_date: "开始日期", end_date: "结束日期", as_of_date: "产物日期",
  initial_cash: "初始资金（元）", final_equity: "期末净值（元）", total_equity: "总资产（元）",
  total_return: "总收益率（小数）", annualized_return: "年化收益率（小数）", maximum_drawdown: "最大回撤（小数）",
  sharpe_ratio: "夏普比率", total_commission: "总佣金（元）", turnover_rate: "换手率（小数）",
  code_version: "代码版本", data_version: "数据版本", validation_mode: "验证模式",
  training_artifact_verified: "训练产物已核验", fee_model_version: "费用模型", adjust_type: "复权口径",
  evidence_qualified: "原记录证据资格（本查询不认证）",
  holding_count: "持仓数", price_as_of: "汇总报价日期（各股可能不同）", price_stale: "存在缺可信报价",
  sequence_anomaly: "流水时序异常", cash: "现金（元）", positions_market_value: "持仓估值（元）",
  realized_pnl: "已实现盈亏（元）", missing_price_count: "缺可信报价持仓数",
  earliest_price_date: "最早逐股报价日期", latest_price_date: "最晚逐股报价日期",
  execution_allowed: "允许直接执行", input_source: "输入来源", cash_before: "原预算（元）",
  target_cash: "目标现金（元）", cash_after: "草案现金（元）", source_run_id: "来源回测 ID",
  target_count: "目标数", symbol: "证券代码", latest_trade_date: "最新可信交易日", data_status: "数据状态",
  close: "收盘价（元）", quality_status: "质量状态", source_name: "数据来源", action_type: "维护类型",
  phase: "任务阶段", started_at: "任务开始时点", finished_at: "任务结束时点", problem_closed: "原任务范围内问题已闭合",
  quality_issue_updated: "质量问题已更新", scope: "复查范围", profile_rows: "画像行数",
  failed_count: "失败数", empty_factor_count: "空因子数", unresolved_rows: "未解决行数", remaining_gap_count: "剩余缺口数",
  completed_through: "日线完成日期", target_trade_date: "本次目标交易日", factor_rows: "实际因子行数",
  daily_loop_job_id: "日级闭环原任务 ID", daily_loop_status: "日级闭环状态", gate_status: "原发布闸门",
  publish_eligible: "原服务允许发布", market_release_id: "可信行情发布 ID", curated_release_id: "前复权发布 ID",
  alternative_status: "补充来源采集状态", failed_services_count: "失败的因子族数", empty_services_count: "无产物的因子族数",
  reason_codes_count: "原任务原因码数",
  observed_at: "报价采集时点", quote_time_verified: "已核验报价时点", observation_status: "报价采集结果",
  price_cny: "腾讯报价（元）", pe_ttm: "腾讯滚动市盈率", pb: "腾讯市净率",
  batch_id: "本次采集批次 ID", raw_snapshot_id: "来源快照 ID", raw_batch_id: "来源快照归档批次 ID",
  trade_date: "交易日", total: "对账总记录数", compared_sample_count: "返回对账样本数", conflict_sample_count: "样本中的冲突数",
  source_a: "对账来源 A", source_b: "对账来源 B", raw_id_a: "原始记录 A", raw_id_b: "原始记录 B",
  close_diff: "收盘价差（元）", conflict_reason: "冲突原因",
};

export function factValue(value: string | number | boolean | null): string {
  if (value === null) return "未提供";
  if (typeof value === "boolean") return value ? "是" : "否";
  return String(value);
}
