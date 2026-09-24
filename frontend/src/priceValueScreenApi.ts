export interface PriceValueScreenRow {
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
}
export interface PriceValueScreenReport {
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
