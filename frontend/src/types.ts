export interface DataQualityIssueSnapshot {
  id: number;
  dataset: string;
  symbol: string | null;
  trade_date: string | null;
  issue_type: string;
  severity: string;
  status: string;
  created_at: string;
}

export interface JobLogSnapshot {
  id: number;
  job_name: string;
  job_type: string;
  status: string;
  started_at: string;
  finished_at: string | null;
  rows_failed: number;
  error_message: string | null;
}

export interface FactorRunSnapshot {
  id: number;
  factor_name: string;
  factor_version: string;
  status: string;
  input_start_date: string | null;
  input_end_date: string | null;
  started_at: string;
  finished_at: string | null;
  rows_written: number;
  error_message: string | null;
}

export interface BacktestRunSnapshot {
  id: number;
  name: string;
  strategy_name: string;
  status: string;
  start_date: string;
  end_date: string;
  final_equity: string | number | null;
  total_return: string | number | null;
  started_at: string;
  finished_at: string | null;
  error_message: string | null;
}

export interface PaperOrderSnapshot {
  id: number;
  account_id: number;
  plan_id: number;
  check_id: number;
  symbol: string;
  side: string;
  order_quantity: number;
  trade_date: string;
  status: string;
  rejection_reason: string | null;
  created_at: string;
}

export interface DataQualityFailure extends DataQualityIssueSnapshot {
  raw_id: number | null;
  adjust_type: string | null;
  source_name: string | null;
  description: string;
  expected_value: Record<string, unknown> | null;
  actual_value: Record<string, unknown> | null;
}

export interface JobFailure extends JobLogSnapshot {
  batch_id: number | null;
  rows_read: number;
  rows_inserted: number;
  rows_updated: number;
  metadata: Record<string, unknown> | null;
}

export interface FactorRunFailure extends FactorRunSnapshot {
  parameters: Record<string, unknown>;
  code_version: string;
  metadata: Record<string, unknown> | null;
}

export interface BacktestRunFailure extends BacktestRunSnapshot {
  parameters: Record<string, unknown>;
  code_version: string;
  data_version: string;
  initial_cash: string | number;
}

export interface PaperOrderFailure extends PaperOrderSnapshot {
  reference_price: string | number;
  risk_current_drawdown: string | number | null;
  risk_limits: Record<string, unknown> | null;
  risk_code_version: string | null;
  risk_order_rejection_reasons: string[];
  risk_order_rule_results: Array<Record<string, unknown>>;
}

export interface FailureSection<T> {
  total: number;
  limit: number;
  has_more: boolean;
  items: T[];
}

export interface OperationsOverview {
  generated_at: string;
  data_quality: {
    issue_status_counts: Record<string, number>;
    open_issue_severity_counts: Record<string, number>;
    latest_issues: DataQualityIssueSnapshot[];
  };
  jobs: {
    status_counts: Record<string, number>;
    latest_jobs: JobLogSnapshot[];
  };
  factors: {
    status_counts: Record<string, number>;
    latest_runs: FactorRunSnapshot[];
  };
  backtests: {
    status_counts: Record<string, number>;
    latest_runs: BacktestRunSnapshot[];
  };
  paper_trading: {
    account_count: number;
    fill_count: number;
    position_count: number;
    order_status_counts: Record<string, number>;
    latest_orders: PaperOrderSnapshot[];
  };
}

export interface OperationsAlertItem {
  key: string;
  severity: "info" | "warning" | "critical";
  count: number;
  title: string;
  detail: string;
}

export interface OperationsAlertSummary {
  generated_at: string;
  items: OperationsAlertItem[];
}

export interface OperationsFailureReport {
  generated_at: string;
  data_quality_issues: FailureSection<DataQualityFailure>;
  jobs: FailureSection<JobFailure>;
  factor_runs: FailureSection<FactorRunFailure>;
  backtest_runs: FailureSection<BacktestRunFailure>;
  paper_orders: FailureSection<PaperOrderFailure>;
}

export interface OperationsFailureDetail {
  failure_type:
    | "data_quality_issue"
    | "job"
    | "factor_run"
    | "backtest_run"
    | "paper_order";
  data_quality_issue?: DataQualityFailure | null;
  job?: JobFailure | null;
  factor_run?: FactorRunFailure | null;
  backtest_run?: BacktestRunFailure | null;
  paper_order?: PaperOrderFailure | null;
}

export interface OperationsRerunPlan {
  failure_type:
    | "data_quality_issue"
    | "job"
    | "factor_run"
    | "backtest_run"
    | "paper_order";
  failure_id: number;
  supported: boolean;
  action_type: string;
  reason: string;
  parameters: Record<string, unknown>;
}
