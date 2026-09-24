import type { PriceValueRequest } from "./priceValueApi";

export interface PriceValueAssumption {
  version: string; valuation_at: string; available_at: string; valid_until: string;
  source_reference: string; diluted_shares: string; scenarios: PriceValueRequest["scenarios"]; thesis_broken: boolean;
}
export interface PriceValueBacktestRequest {
  name: string; symbol: string; start_date: string; end_date: string; initial_cash: string;
  position_weight: string; required_margin: string; max_participation_rate: string;
  fees: PriceValueRequest["fees"]; sell_stamp_duty_rate: string; assumptions: PriceValueAssumption[];
  company_profile: "stable_non_financial"; currency: "CNY"; lot_size: 100;
}
export interface PriceValueBacktestResponse {
  run_id: number; strategy_name: string; research_only: true; pit_verified: false; execution_eligible: false;
  evaluated_at: string; contract_hash: string; initial_cash: string; final_equity: string; net_profit: string; total_return: string;
  maximum_drawdown: string; final_cash: string; trade_count: number; assumptions: PriceValueBacktestRequest;
  limitations: string[];
  trades: Array<{ signal_date: string; trade_date: string; symbol: string; side: string; quantity: number; reference_price: string; fill_price: string; commission: string; cash_after: string }>;
  equity_curve: Array<{ trade_date: string; cash: string; market_value: string; total_equity: string }>;
  positions: Array<{ trade_date: string; symbol: string; quantity: number; close: string; market_value: string }>;
  decisions: Array<{ trade_date: string; phase: "open" | "close"; action: string; reason: string; assumption_version: string | null; signal_date: string | null }>;
}
const backtestErrors: Record<string, string> = {
  "security is missing from the trusted market layer": "可信数据层中没有该证券，请先完成证券数据导入与校验。",
  "first release supports only CNY SH/SZ main-board ordinary shares": "首版仅支持人民币计价的沪深主板普通股。",
  "non-financial industry classification is required": "需要明确的非金融行业分类，当前证券的行业资料缺失或不适用。",
  "security lifecycle does not cover the requested interval": "证券的上市、退市或活跃状态不符合当前回测区间。",
  "interval contains corporate actions; event accounting is not implemented": "区间内存在分红、送转等公司行为，首版尚不支持对应会计处理，请调整区间。",
  "at least two unique ordered trusted trading dates are required": "可信交易日历至少需要覆盖两个开市日。",
  "missing or duplicate trusted unadjusted bar; complete calendar coverage required": "回测区间缺少完整的可信未复权日线，请先检查行情质量与交易日历。",
  "backtest requires trusted non-suspect stock bars with provenance": "回测需要带来源、可信且非可疑的股票日线。",
  "invalid OHLC in trusted market slice": "区间内存在无效的开高低收价格，请先检查行情质量。",
  "complete non-negative volume and amount are required": "成交量或成交额缺失、无效，请先补齐可信数据。",
  "non-suspended zero volume or amount is an unexplained trading state": "存在未标记停牌却零成交量或零成交额的日期，请先核对交易状态。",
  "ST or unknown historical treatment status is outside this simulation": "区间内有 ST 或未知特殊处理状态，首版拒绝运行。",
  "valid daily price limits are required; never assume unbounded tradability": "区间内缺少有效的涨跌停价格，无法模拟成交。",
  "end_date must have completed its trading session (16:00 Asia/Shanghai)": "结束日期须已过北京时间 16:00，请选择已结束的交易区间。",
  "assumption available_at cannot be in the future": "假设首次可得时间不能晚于当前时间。",
  "trusted market inputs changed during price-value simulation": "运行期间可信数据发生变化，本次结果未保存，请重新运行。",
};
export async function runPriceValueBacktest(payload: PriceValueBacktestRequest, signal: AbortSignal): Promise<PriceValueBacktestResponse> {
  const response = await fetch("/api/v1/strategy-research/price-value/backtests", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload), signal,
  });
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    const detail = body?.detail;
    throw new Error(typeof detail === "string" ? backtestErrors[detail] ?? detail : Array.isArray(detail)
      ? detail.map((item: { loc?: string[]; msg?: string }) => `${item.loc?.join(" / ")}: ${item.msg}`).join("；")
      : `回测失败（${response.status}），请检查服务及输入。`);
  }
  return response.json() as Promise<PriceValueBacktestResponse>;
}
