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
  rule: string;
  next_action: string;
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

export interface ApiPage<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

export interface PaperAccount {
  id: number;
  name: string;
  cash_balance: string;
}

export interface DailyBarItem {
  symbol: string;
  security_type: string;
  trade_date: string;
  adjust_type: "none" | "qfq" | "hfq";
  open: string;
  high: string;
  low: string;
  close: string;
  pre_close: string | null;
  change_amount: string | null;
  pct_change: string | null;
  volume: string | null;
  amount: string | null;
  turnover_rate: string | null;
  amplitude: string | null;
  source_name: string;
  source_count: number;
  quality_score: string;
  quality_status: string;
  is_suspect: boolean;
  updated_at: string;
}

export interface FactorDefinition {
  id: number;
  name: string;
  display_name: string | null;
  description: string | null;
  category: string;
  frequency: "daily" | "weekly" | "monthly";
  inputs: string[];
  applicable_security_types: string[];
  window_days: number | null;
  version: string;
  dependencies: string[];
  parameters_schema: Record<string, unknown>;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

export interface FactorProfile {
  id: number;
  factor_name: string;
  factor_version: string;
  trade_date: string;
  universe_size: number;
  observation_count: number;
  missing_count: number;
  coverage_rate: string;
  missing_rate: string;
  mean: string | null;
  standard_deviation: string | null;
  ic_1d: string | null;
  rank_ic_1d: string | null;
  top_quantile_turnover: string | null;
  code_version: string;
}

export interface PortfolioAssetInput {
  symbol: string;
  security_type: "stock" | "etf" | "index";
  industry: string;
  price: string;
  current_quantity: number;
  requested_weight: string;
  lot_size: number;
}

export interface PortfolioConstraintsInput {
  cash_reserve_rate: string;
  max_symbol_weight: string;
  max_industry_weight: string;
  security_type_budgets: Record<string, string>;
}

export interface PortfolioPlanCreateRequest {
  name: string;
  as_of_date: string;
  cash: string;
  assets: PortfolioAssetInput[];
  constraints: PortfolioConstraintsInput;
}

export interface PortfolioTarget {
  symbol: string;
  security_type: string;
  industry: string;
  requested_weight: string;
  constrained_weight: string;
  final_weight: string;
  current_quantity: number;
  target_quantity: number;
  order_quantity: number;
  price: string;
  lot_size: number;
  reasons: string[];
}

export interface PortfolioPlan {
  plan_id: number;
  name: string;
  as_of_date: string;
  status: "draft";
  execution_allowed: false;
  input_source: "operator_provided";
  total_equity: string;
  cash_before: string;
  target_cash: string;
  cash_after: string;
  constraints: PortfolioConstraintsInput;
  code_version: string;
  created_at: string;
  targets: PortfolioTarget[];
}

export interface PortfolioReadinessCheck {
  code: string;
  scope: "plan" | "account" | "position" | "security" | "market";
  passed: boolean;
  symbol: string | null;
  message: string;
}

export interface PortfolioRiskReadiness {
  plan_id: number;
  paper_account_id: number;
  readiness_stage: "pre_risk";
  ready_for_risk_check: boolean;
  execution_allowed: false;
  risk_evaluated: false;
  orders_created: false;
  read_only: true;
  access_scope: "local_single_user";
  consistency_level: "read_committed_diagnostic";
  checked_at: string;
  account_snapshot_hash: string;
  market_snapshot_hash: string;
  checks: PortfolioReadinessCheck[];
  blockers: PortfolioReadinessCheck[];
}

export interface PaperEquityComponent {
  symbol: string;
  quantity: number;
  average_cost: string;
  price: string;
  market_value: string;
  daily_bar_id: number;
  source_name: string;
  quality_status: string;
  bar_trade_date: string;
  adjust_type: "none";
  is_suspect: false;
  bar_updated_at: string;
}

export interface PaperEquitySnapshot {
  snapshot_id: number;
  account_id: number;
  valuation_date: string;
  cash_balance: string;
  positions_market_value: string;
  total_equity: string;
  observed_high_watermark: string;
  drawdown_since_monitoring_start: string;
  previous_snapshot_id: number | null;
  monitoring_start_date: string;
  observation_count: number;
  history_status: "bootstrap" | "continuous" | "gap";
  account_snapshot_hash: string;
  market_snapshot_hash: string;
  valuation_hash: string;
  valuation_policy: Record<string, string>;
  code_version: string;
  created_at: string;
  execution_allowed: false;
  risk_evaluated: false;
  orders_created: false;
  access_scope: "local_single_user";
  components: PaperEquityComponent[];
}

export interface RiskRuleEvaluation {
  rule_name: string;
  passed: boolean;
  reason_code: string;
  message: string;
}

export interface RiskOrderEvaluation {
  symbol: string;
  side: "buy" | "sell";
  order_quantity: number;
  passed: boolean;
  rejection_reasons: string[];
  rule_results: RiskRuleEvaluation[];
  reference_price: string;
  current_symbol_weight: string;
  projected_symbol_weight: string;
  current_industry_weight: string;
  projected_industry_weight: string;
  average_daily_amount: string | null;
  adv_window: number;
  adv_observation_count: number;
  adv_input_hash: string;
}

export interface RiskEvaluation {
  check_id: number;
  plan_id: number;
  paper_account_id: number;
  equity_snapshot_id: number;
  evaluation_status: "passed" | "blocked" | "failed";
  risk_passed: boolean;
  current_drawdown: string;
  limits: Record<string, string>;
  preconditions: Array<Record<string, unknown>>;
  plan_request_hash: string;
  readiness_account_hash: string;
  readiness_market_hash: string;
  readiness_hash: string;
  adv_snapshot_hash: string;
  risk_policy_hash: string;
  evaluation_hash: string;
  code_version: string;
  created_at: string;
  risk_evaluated: true;
  execution_allowed: false;
  orders_created: false;
  access_scope: "local_single_user";
  orders: RiskOrderEvaluation[];
}

export interface PaperExecutionApprovalOrder {
  execution_index: number;
  risk_check_order_id: number;
  symbol: string;
  security_type: string;
  industry: string;
  side: "buy" | "sell";
  signed_quantity: number;
  reference_price: string;
  estimated_fill_price: string;
  estimated_amount: string;
  estimated_commission: string;
  current_quantity: number;
  target_quantity: number;
  risk_classification: "risk_reducing" | "risk_increasing";
}

export interface PaperExecutionApproval {
  approval_id: number;
  risk_check_id: number;
  plan_id: number;
  account_id: number;
  equity_snapshot_id: number;
  stored_status: "pending" | "consumed" | "expired" | "revoked";
  effective_status: "pending" | "consumed" | "expired" | "revoked";
  approval_scope: "paper_execution";
  actor_type: "local_single_user";
  approved_by: string;
  code_version: string;
  evaluation_hash: string;
  order_batch_hash: string;
  approval_hash: string;
  order_summary: PaperExecutionApprovalOrder[];
  confirmation_policy: Record<string, unknown>;
  approved_at: string;
  expires_at: string;
  consumed_at: string | null;
  revoked_at: string | null;
  revocation_reason: string | null;
  created_at: string;
  execution_allowed: false;
  orders_created: false;
  consumable_later: boolean;
  access_scope: "local_single_user";
}

export interface PaperExecutionOrder {
  execution_index: number;
  order_id: number;
  fill_id: number;
  risk_check_order_id: number;
  symbol: string;
  side: "buy" | "sell";
  quantity: number;
  reference_price: string;
  fill_price: string;
  commission: string;
  cash_after: string;
}

export interface PaperExecution {
  execution_id: number;
  approval_id: number;
  risk_check_id: number;
  plan_id: number;
  account_id: number;
  equity_snapshot_id: number;
  status: "completed" | "rejected";
  execution_mode: "paper";
  actor_type: "local_single_user";
  executed_by: string;
  code_version: string;
  approval_hash: string;
  evaluation_hash: string;
  order_batch_hash: string;
  execution_policy_hash: string;
  account_state_hash_before: string;
  account_state_hash_after: string | null;
  execution_hash: string;
  cash_before: string | null;
  cash_after: string | null;
  order_count: number;
  fill_count: number;
  total_commission: string;
  failure_code: string | null;
  failure_message: string | null;
  started_at: string;
  completed_at: string;
  created_at: string;
  approval_status: "consumed";
  real_broker_submitted: false;
  next_step: "capture_new_equity_snapshot";
  access_scope: "local_single_user";
  orders: PaperExecutionOrder[];
}

export interface PositionBook {
  id: number;
  name: string;
  cash: string;
  note: string | null;
  created_at: string;
  updated_at: string;
}

export interface PositionTrade {
  id: number;
  symbol: string;
  side: "buy" | "sell";
  quantity: number;
  price: string;
  fee: string;
  trade_date: string;
  note: string | null;
  created_at: string;
}

export interface PositionConcentration {
  position_count: number;
  hhi: string;
  top5_weight: string;
  max_symbol_weight: string;
}

export interface PositionIndustryExposure {
  industry: string;
  market_value: string;
  weight: string;
  symbol_count: number;
}

export interface PositionHolding {
  symbol: string;
  name: string | null;
  industry: string | null;
  security_type: string | null;
  exchange: string | null;
  quantity: number;
  average_cost: string;
  cost_basis: string;
  price: string | null;
  price_as_of: string | null;
  price_quality_status: string | null;
  price_stale: boolean;
  sequence_anomaly: boolean;
  market_value: string;
  unrealized_pnl: string | null;
  unrealized_pnl_pct: string | null;
  realized_pnl: string;
  weight: string;
}

export interface PositionBookHoldings {
  book_id: number;
  book_name: string;
  price_as_of: string | null;
  price_stale: boolean;
  sequence_anomaly: boolean;
  cash: string;
  positions_market_value: string;
  total_equity: string;
  realized_pnl: string;
  concentration: PositionConcentration;
  industry_exposure: PositionIndustryExposure[];
  holdings: PositionHolding[];
}

export interface PaperPositionRow {
  symbol: string;
  name: string | null;
  industry: string | null;
  security_type: string | null;
  exchange: string | null;
  quantity: number;
  average_cost: string;
  price: string | null;
  price_as_of: string | null;
  price_quality_status: string | null;
  price_stale: boolean;
  market_value: string;
  unrealized_pnl: string | null;
  unrealized_pnl_pct: string | null;
  weight: string;
}

export interface PaperPositions {
  account_id: number;
  price_as_of: string | null;
  price_stale: boolean;
  cash_balance: string;
  positions_market_value: string;
  total_equity: string;
  concentration: PositionConcentration;
  industry_exposure: PositionIndustryExposure[];
  holdings: PaperPositionRow[];
}

export interface PaperPerformancePoint {
  valuation_date: string;
  cash_balance: string;
  positions_market_value: string;
  total_equity: string;
  drawdown: string;
  history_status: string;
}

export interface PaperPerformanceSummary {
  observation_count: number;
  monitoring_start_date: string | null;
  first_valuation_date: string | null;
  last_valuation_date: string | null;
  latest_total_equity: string;
  high_watermark: string;
  max_drawdown: string;
  cumulative_return: string | null;
  annualized_return: string | null;
  approximate: boolean;
  daily_volatility: string | null;
  volatility_approximate: boolean;
}

export interface PaperPerformance {
  account_id: number;
  summary: PaperPerformanceSummary;
  curve: PaperPerformancePoint[];
}

export interface PortfolioDriftRow {
  symbol: string;
  industry: string | null;
  security_type: string | null;
  target_weight: string;
  current_weight: string;
  drift: string;
  direction: "buy" | "sell" | "hold";
  current_quantity: number;
  target_quantity: number | null;
  reasons: string[];
}

export interface PortfolioDriftSummary {
  max_abs_drift: string;
  off_threshold_count: number;
  cash_weight: string;
  cash_target_weight: string;
}

export interface PortfolioPlanDrift {
  plan_id: number;
  source: "position_book" | "paper_account";
  source_id: number;
  source_name: string | null;
  threshold: string;
  price_as_of: string | null;
  total_equity: string;
  summary: PortfolioDriftSummary;
  rows: PortfolioDriftRow[];
}
