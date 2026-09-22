export interface ValueScenario {
  cash_flows: string[];
  terminal_cash_flow: string;
  cost_of_equity: string;
  terminal_growth: string;
}
export interface PriceValueRequest {
  symbol: string;
  trade_date: string;
  valuation_at: string;
  assumptions_available_at: string;
  valid_until: string;
  assumption_version: string;
  assumptions_note: string;
  currency: "CNY";
  company_profile: "stable_non_financial" | "financial" | "cyclical" | "distressed";
  diluted_shares: string;
  scenarios: Record<"low" | "base" | "high", ValueScenario>;
  required_margin: string;
  quantity: number;
  fees: Record<"commission_rate" | "min_commission" | "transfer_fee_rate" | "exchange_fee_rate" | "slippage_bps", string>;
  has_position: boolean;
  thesis_broken: boolean;
}
export interface ResearchReason { code: string; message: string }
export interface PriceValueResult {
  status: "blocked" | "price_condition_met" | "wait_for_price" | "review_required";
  valuation: Record<"low" | "base" | "high", string> | null;
  price_metrics: {
    gross_margin: string; cost_adjusted_margin: string; buy_cost_ceiling: string;
    max_buy_quote: string; all_in_price: string; fee_total: string; slippage_cost: string; price_condition_met: boolean;
  } | null;
  market_snapshot: { symbol: string; trade_date: string; close: string; source_name: string; quality_status: string; updated_at: string | null } | null;
  blocking_reasons: ResearchReason[];
  review_reasons: ResearchReason[];
  limitations: ResearchReason[];
  execution_eligible: false;
  pit_verified: false;
  research_only: true;
  model_version: string;
  code_version: string;
  contract_hash: string;
  evaluated_at: string;
  assumptions: PriceValueRequest;
  new_allocation: "none";
  existing_position_action: "unchanged" | "review";
}
export async function evaluatePriceValue(payload: PriceValueRequest, signal: AbortSignal): Promise<PriceValueResult> {
  const response = await fetch("/api/v1/strategy-research/price-value/evaluate", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload), signal,
  });
  if (!response.ok) {
    const data = await response.json().catch(() => null);
    const detail = data?.detail;
    throw new Error(typeof detail === "string" ? detail : Array.isArray(detail)
      ? detail.map((item: { loc?: string[]; msg?: string }) => `${item.loc?.join(" / ")}: ${item.msg}`).join("；")
      : `研究请求失败（${response.status}），请检查服务和输入。`);
  }
  return response.json() as Promise<PriceValueResult>;
}
