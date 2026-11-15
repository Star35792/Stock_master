export interface FactorScoreSpecInput {
  factor_name: string;
  factor_version: string;
  weight: string;
  higher_is_better: boolean;
}

export interface FactorSelectionBacktestRequest {
  name: string;
  strategy_name: string;
  factor_specs: FactorScoreSpecInput[];
  top_n: number;
  signal_lag: number;
  rebalance_rule: "daily" | "weekly" | "monthly" | "every_n_trading_days";
  rebalance_interval: number;
  holding_period: number;
  benchmark: string | null;
  allowed_security_types: string[];
  coverage_ratio: string;
  weighting_mode: string;
  minimum_score_policy: string;
  invested_weight: string;
  portfolio_constraints: {
    max_symbol_weight: string;
    max_industry_weight: string;
    cash_reserve_rate: string;
    security_type_budgets: null;
  } | null;
  start_date: string;
  end_date: string;
  adjust_type: string;
  initial_cash: string;
  commission_rate: string;
  slippage_bps: string;
  lot_size: number;
  fee_model_version: string;
  min_commission: string;
  sell_stamp_duty_rate: string | null;
  transfer_fee_rate: string;
  exchange_fee_rate: string;
  code_version: string;
  data_version: string;
  validation_mode: string;
}

export interface FactorSelectionBacktestResult {
  run_id: number;
  name: string;
  strategy_name: string;
  start_date: string;
  end_date: string;
  initial_cash: string;
  final_equity: string;
  total_return: string;
  trade_count: number;
  equity_points: number;
  position_snapshots: number;
}

export interface FactorSelectionPreflight {
  rule_version: string;
  checked_at: string;
  input_sha256: string;
  runnable_now: boolean;
  evidence_qualified: boolean;
  checks: Array<{ code: string; status: "passed" | "blocked" | "caveat"; detail: string; next_action: string }>;
}

export async function preflightFactorSelectionBacktest(
  payload: FactorSelectionBacktestRequest,
  signal: AbortSignal,
): Promise<FactorSelectionPreflight> {
  const response = await fetch("/api/v1/strategy-research/factor-selection/backtests/preflight", {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify(payload),
    signal,
  });
  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as { detail?: unknown } | null;
    throw new Error(translateDetail(body?.detail));
  }
  return response.json() as Promise<FactorSelectionPreflight>;
}

const requestErrors: Record<string, string> = {
  "only signal_lag=1 is supported": "当前仅支持 T+1 信号执行。",
  "only holding_period=1 is supported": "当前仅支持持有 1 期。",
  "rebalance_interval only applies to rebalance_rule=every_n_trading_days":
    "调仓间隔仅对「每 N 个交易日」规则生效。",
  "non-daily rebalance rules are only supported for exchange=SH":
    "非日频调仓目前仅支持上交所（SH）日历。",
  "backtest interval contains corporate actions; event accounting is not implemented":
    "回测区间内存在公司行为（分红/送转），平台尚未实现对应会计处理，请调整区间。",
  "backtest_data_changed: trusted market data changed while the backtest was running":
    "运行期间可信行情发生变化，本次结果未保存，请重试。",
  "rebalance rule leaves no decision date with factor observations in the requested window":
    "所选调仓规则在窗口内没有落在因子观察日上的调仓日，请调整区间或规则。",
};

function translateDetail(detail: unknown): string {
  if (typeof detail === "string") {
    return requestErrors[detail] ?? detail;
  }
  if (Array.isArray(detail)) {
    return detail
      .map((item: { loc?: string[]; msg?: string }) => `${item.loc?.join(" / ")}: ${item.msg}`)
      .join("；");
  }
  return `回测失败，请检查服务及输入。`;
}

export async function runFactorSelectionBacktest(
  payload: FactorSelectionBacktestRequest,
  signal: AbortSignal,
): Promise<FactorSelectionBacktestResult> {
  const response = await fetch("/api/v1/strategy-research/factor-selection/backtests", {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify(payload),
    signal,
  });
  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as { detail?: unknown } | null;
    throw new Error(translateDetail(body?.detail));
  }
  return response.json() as Promise<FactorSelectionBacktestResult>;
}

export async function fetchBacktestFinalHoldingsTyped(
  runId: number,
  signal: AbortSignal,
): Promise<{
  run_id: number;
  as_of_date: string;
  cash: string;
  total_equity: string;
  holdings: Array<{
    symbol: string;
    name: string | null;
    industry: string | null;
    quantity: number;
    close: string;
    market_value: string;
    weight: string;
  }>;
}> {
  const response = await fetch(`/api/v1/backtests/runs/${runId}/final-holdings`, {
    headers: { Accept: "application/json" },
    signal,
  });
  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as { detail?: unknown } | null;
    throw new Error(translateDetail(body?.detail));
  }
  return response.json();
}
