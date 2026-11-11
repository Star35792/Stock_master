export interface PriceValueResearchDiagnostics {
  version: string;
  trend: {
    status: "improving" | "weak" | "mixed" | "unknown";
    return_20: string | null;
    ma20_gap: string | null;
    reason_codes: string[];
    source_refs: string[];
  };
  quality: {
    status: "supported" | "risk" | "unknown" | "not_applicable";
    net_income_sum_3y: string | null;
    net_income_ttm?: string | null;
    net_income_min_3y?: string | null;
    net_income_positive_years_3y?: string | number | null;
    ocf_profit_3y: string | null;
    ocf_ttm: string | null;
    reason_codes: string[];
    source_refs: string[];
  };
  review_eligible: boolean;
}
export interface PriceValueScreenRow {
  shortlist?: {
    status: "selected" | "qualified_deferred" | "excluded" | "needs_review" | "blocked";
    qualified: boolean;
    selected: boolean;
    rank: number | null;
    reason_codes: string[];
    metrics: Record<string, unknown>;
  };
  symbol: string;
  name: string;
  industry: string | null;
  vendor_industry_hint: string | null;
  status: "ready_for_review" | "needs_review";
  reason_codes: string[];
  close: string | null;
  pe_vendor_unverified: string | null;
  pb_vendor: string | null;
  roe_single_period_proxy: string | null;
  roe_reported_vendor: string | null;
  roe_method: "vendor_weighted_average" | "single_period_proxy" | null;
  roe_report_date: string | null;
  roe_newer_report_date: string | null;
  roe_newer_reported_vendor: string | null;
  roe_newer_raw_page_hash: string | null;
  roe_notice_date: string | null;
  roe_raw_page_hash: string | null;
  valuation_source: string | null;
  valuation_updated_at: string | null;
  valuation_raw_page_hash: string | null;
  bar_source: string | null;
  bar_updated_at: string | null;
  roe_factor_name: string | null;
  roe_factor_version: string | null;
  roe_factor_run_id: number | null;
  research_diagnostics?: PriceValueResearchDiagnostics;
}
export interface PriceValueScreenReport {
  screen_version?: string;
  selection?: {
    version: string;
    parameters: Record<string, unknown>;
    qualified_count: number;
    shortlist_count: number;
    ordered_symbols: string[];
    reason_counts: Record<string, number>;
    ranking_description?: string;
    rule_impact?: {
      comparison: string;
      v1_equivalent_qualified_count: number;
      v1_equivalent_shortlist_count: number;
      removed_from_qualified_by_v2_count: number;
      removed_from_shortlist_by_v2: string[];
      reason_counts: Record<string, number>;
      mixed_with_other_reasons_count: number;
    };
  };
  observation_mode?: "same_evening" | "current_research_catchup";
  price_as_of?: string | null;
  financial_as_of?: string | null;
  analysis_at?: string | null;
  observed_at?: string | null;
  freshness?: {
    status: "current" | "stale" | "unknown" | "future";
    expected_trade_date: string | null;
    checked_at: string;
    reason_codes: string[];
  };
  candidate_eligible?: boolean;
  research_candidate_count?: number;
  diagnostic_version?: string;
  run_id: number;
  generated_at: string;
  target_trade_date: string | null;
  status: "blocked" | "partial" | "ready";
  error_message: string | null;
  global_reason_codes: string[];
  universe_count: number;
  ready_count: number;
  needs_review_count: number;
  reason_counts: Record<string, number>;
  snapshot_hash: string;
  valuation_source: string;
  input_batch_id: number | null;
  input_job_id: number | null;
  supplier_total: number | null;
  valuation_accepted_count: number | null;
  exchange_coverage: Record<string, { active_stocks: number; trusted_bars: number; vendor_mapped: number; close_matched: number; reported_roe: number }> | null;
  current_coverage_by_exchange: Record<string, { securities: number; trusted_bars: number; valuation: number; valid_pe_pb: number; reported_roe: number; proxy_roe: number; industry: number; listing_date: number; treatment_known: number }>;
  latest_daily_sync: null | { job_id: number; status: string; started_at: string; finished_at: string | null; error_summary: string | null };
  historical_raw_archive: null | { job_id: number; observed_at_utc: string; supplier_total: number; pit_verified: false; publication_scope: "raw_only" };
  daily_sync_job_id: number | null;
  prepared_factor_run_id: number | null;
  roe_reported_count: number | null;
  roe_report_dates: string[] | null;
  roe_raw_page_hashes: string[] | null;
  limitations: string[];
  rows: PriceValueScreenRow[];
}
export type PriceValueRuleImpact = NonNullable<
  NonNullable<PriceValueScreenReport["selection"]>["rule_impact"]
>;
export interface PriceValueProfitStatus {
  value_backtest: null | {
    run_id: number;
    end_date: string;
    initial_cash: string;
    final_equity: string;
    hypothetical_profit: string;
    total_return: string;
    research_only: true;
    pit_verified: false;
  };
  paper_accounts: Array<{
    account_id: number;
    account_name: string;
    latest_valuation_date: string | null;
    latest_total_equity: string | null;
    profit: null;
    profit_reason: string;
  }>;
  paper_profit_note: string;
}
export interface PriceValueTrackingPool {
  status: "complete" | "incomplete";
  member_count: number;
  observed_count: number;
  missing_count: number;
  coverage_rate: string | null;
  gross_return_observed: string | null;
  net_return_observed: string | null;
  excess_return_vs_research_pool?: string | null;
  positive_member_rate: string | null;
  maximum_member_loss: string | null;
  missing_reason_counts: Record<string, number>;
}
export interface PriceValueForwardTracking {
  version: string;
  status: "blocked" | "pending" | "observing" | "incomplete" | "complete";
  reason_codes: string[];
  snapshot_hash?: string;
  screen_version?: string;
  selection_version?: string;
  target_trade_date?: string;
  observation_mode?: "same_evening" | "current_research_catchup";
  analysis_at?: string | null;
  formation_cutoff?: string;
  entry_date?: string | null;
  benchmark?: string;
  price_dataset?: string;
  price_input_hash?: string | null;
  observation_hash?: string;
  cost_policy?: {
    version: string;
    round_trip_cost_rate: string;
    description: string;
  };
  pools: Record<string, string[]>;
  rule_impact?: PriceValueRuleImpact;
  windows: Array<{
    trading_days: number;
    status: "pending" | "incomplete" | "complete";
    end_date: string | null;
    reason_codes: string[];
    pools: Record<"shortlist" | "qualified" | "research", PriceValueTrackingPool> | Record<string, never>;
  }>;
  research_only: true;
  pit_verified: false;
  execution_eligible: false;
}
export async function fetchPriceValueScreen(signal?: AbortSignal): Promise<PriceValueScreenReport | null> {
  const response = await fetch("/api/v1/strategy-research/price-value/screen/latest", { signal });
  if (response.status === 404) return null;
  if (!response.ok) throw new Error(`初筛报告读取失败（${response.status}）`);
  return response.json() as Promise<PriceValueScreenReport>;
}
export async function fetchPriceValueProfitStatus(signal?: AbortSignal): Promise<PriceValueProfitStatus> {
  const response = await fetch("/api/v1/strategy-research/price-value/profit-status", { signal });
  if (!response.ok) throw new Error(`收益状态读取失败（${response.status}）`);
  return response.json() as Promise<PriceValueProfitStatus>;
}
export async function fetchPriceValueTracking(signal?: AbortSignal): Promise<PriceValueForwardTracking | null> {
  const response = await fetch("/api/v1/strategy-research/price-value/screen/tracking", { signal });
  if (response.status === 404) return null;
  if (!response.ok) throw new Error(`名单效果跟踪读取失败（${response.status}）`);
  return response.json() as Promise<PriceValueForwardTracking>;
}
