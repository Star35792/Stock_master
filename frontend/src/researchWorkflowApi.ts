import type {
  BacktestFinalHoldings,
  OperationsAlertSummary,
  PortfolioPlanSummary,
  PositionBook,
} from "./types";

/**
 * 研究流水线聚合接口（BFF 层）。
 * 对应后端 GET /api/v1/research-workflow/*，一次请求串起原本多接口才能拼出的视图。
 */

export interface ResearchRunSummary {
  id: number;
  name: string;
  strategy_name: string;
  parameters: Record<string, unknown>;
  code_version: string;
  data_version: string;
  start_date: string;
  end_date: string;
  initial_cash: string;
  final_equity: string | null;
  total_return: string | null;
  status: string;
  started_at: string;
  finished_at: string | null;
  error_message: string | null;
  created_at: string;
  sharpe_ratio?: string | null;
  performance_status?: "analyzed" | "derived" | "missing_equity" | "invalid_equity" | "not_applicable";
  performance_code_version?: string | null;
}

export interface ResearchEquityPoint {
  trade_date: string;
  cash: string;
  market_value: string;
  total_equity: string;
}

export interface ResearchPerformance {
  total_return: string;
  annualized_return: string;
  maximum_drawdown: string;
  annualized_volatility: string;
  sharpe_ratio: string | null;
  calmar_ratio: string | null;
  daily_win_rate: string;
  profit_loss_ratio: string | null;
  turnover_rate: string;
  total_commission: string;
  code_version: string;
}

export interface ResearchBenchmark {
  benchmark: string;
  input_source: "run_snapshot" | "current_market_unversioned";
  strategy_total_return: string;
  benchmark_total_return: string;
  excess_return: string;
  strategy_annualized_return: string;
  benchmark_annualized_return: string;
  excess_annualized_return: string;
  start_date: string;
  end_date: string;
}

export interface WorkbenchView {
  recent_backtests: ResearchRunSummary[] | null;
  recent_backtests_error: string | null;
  portfolio_plans: PortfolioPlanSummary[] | null;
  portfolio_plans_error: string | null;
  position_books: PositionBook[] | null;
  position_books_error: string | null;
  alerts: OperationsAlertSummary | null;
  alerts_error: string | null;
}

export interface PipelineView {
  run: ResearchRunSummary;
  equity_curve: ResearchEquityPoint[] | null;
  equity_curve_error: string | null;
  performance: ResearchPerformance | null;
  performance_error: string | null;
  benchmark: ResearchBenchmark | null;
  benchmark_error: string | null;
  final_holdings: BacktestFinalHoldings | null;
  final_holdings_error: string | null;
}

async function jsonApi<T>(response: Response, message: string): Promise<T> {
  if (!response.ok) {
    const payload = (await response.json().catch(() => null)) as { detail?: string } | null;
    throw new Error(payload?.detail ?? `${message} (${response.status})`);
  }
  return response.json() as Promise<T>;
}

export async function fetchResearchWorkbench(
  signal?: AbortSignal,
): Promise<WorkbenchView> {
  const response = await fetch("/api/v1/research-workflow/workbench", {
    headers: { Accept: "application/json" },
    signal,
  });
  return jsonApi<WorkbenchView>(response, "研究首页聚合读取失败");
}

export async function fetchResearchPipeline(
  runId: number,
  signal?: AbortSignal,
): Promise<PipelineView> {
  const response = await fetch(`/api/v1/research-workflow/pipeline?run_id=${runId}`, {
    headers: { Accept: "application/json" },
    signal,
  });
  return jsonApi<PipelineView>(response, "研究流水线聚合读取失败");
}
