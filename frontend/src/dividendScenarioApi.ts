export interface DividendScenarioInput {
  input_kind: "synthetic";
  price_basis: "unadjusted_synthetic";
  symbol: string;
  initial_cash: string;
  initial_quantity: number;
  initial_price: string;
  prices: { trade_date: string; close: string }[];
  events: { event_id: string; revision: string; action_type: "cash_dividend"; record_date: string;
    ex_date: string; payment_date: string; cash_per_share: string; assumed_tax_rate: string }[];
  fills: { trade_date: string; side: "buy" | "sell"; quantity: number; price: string; fee: string }[];
}
export interface DividendScenarioReport {
  version: string; research_only: true; execution_eligible: false; production_policy: "reject";
  symbol: string; contract_sha256: string; initial_equity: string; final_equity: string;
  total_return: string; duplicate_events_ignored: number; declarations: string[];
  days: { trade_date: string; cash: string; quantity: number; sellable_quantity: number; close: string;
    receivable: string; market_value: string; total_equity: string; cumulative_gross_dividend: string;
    cumulative_assumed_tax: string; cumulative_fees: string }[];
  events: { event_id: string; revision: string; eligible_quantity: number; gross: string;
    assumed_tax: string; net: string; status: string }[];
}
export async function evaluateDividendScenario(input: DividendScenarioInput, signal: AbortSignal): Promise<DividendScenarioReport> {
  const response = await fetch("/api/v1/strategy-research/cash-dividend-scenario", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(input), signal,
  });
  if (!response.ok) {
    const data = await response.json().catch(() => null);
    const detail = data?.detail;
    throw new Error(typeof detail === "string" ? detail : Array.isArray(detail)
      ? detail.map((item: { msg?: string }) => item.msg).join("；")
      : `情景计算失败（HTTP ${response.status}）`);
  }
  return response.json() as Promise<DividendScenarioReport>;
}
