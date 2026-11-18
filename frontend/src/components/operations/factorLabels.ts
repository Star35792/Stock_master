import type { FactorDefinition } from "../../types";

/** 因子分类中文标签。 */
export const factorCategoryLabels: Record<string, string> = {
  price_volume_risk: "价量与风险",
  financial_health: "财务健康",
  fundamental_quality: "基本面质量与成长",
  valuation: "估值与安全边际",
  industry_prosperity: "行业景气",
  sentiment: "市场情绪",
  macro: "宏观与信用",
  policy: "政策与监管",
  earnings_revision: "盈利预期修正",
  capital_flow: "资金结构与拥挤度",
  composite: "复合决策",
};

export const factorCategoryOptions = [
  { value: "", label: "全部分类" },
  ...Object.entries(factorCategoryLabels).map(([value, label]) => ({ value, label })),
];

/** 参数名中文标签。 */
export const parameterLabels: Record<string, string> = {
  window: "计算窗口",
  window_days: "窗口天数",
  lag: "滞后期",
  quantile: "分位数",
  winsorize: "缩尾比例",
  sentiment_history_days: "情绪历史窗口（天）",
};

const factorCopy: Record<string, { name: string; description: string }> = {
  debt_to_assets: { name: "资产负债率", description: "最近一期总负债除以总资产，用于衡量企业杠杆水平与偿债压力。" },
  ma_gap_5: { name: "5 日均线偏离", description: "收盘价相对近 5 个交易日平均收盘价的偏离程度，用于观察短期趋势。" },
  momentum_5: { name: "5 日动量", description: "当前收盘价相对近 5 个交易日前收盘价的变化，用于衡量短期价格动量。" },
  ret_20d: { name: "20 日收益率", description: "衡量标的近 20 个交易日的价格变化，用于观察短中期动量。" },
  ret_1d: { name: "1 日收益率", description: "当前可信收盘价相对前一可信交易日收盘价的变化。" },
  market_valuation_pe_ttm: { name: "滚动市盈率", description: "比较当前价格与过去十二个月盈利，数值越低通常代表估值更低。" },
  net_profit_growth_yoy: { name: "净利润同比增长", description: "衡量公司净利润相对上年同期的增长幅度。" },
  ocf_to_net_income_ttm: { name: "经营现金流净利润比", description: "最近报告期经营活动现金流相对净利润的比例，用于观察盈利的现金含量。" },
  revenue_growth_yoy: { name: "营业收入同比增长", description: "衡量企业营业收入相对上年同期的变化，用于观察企业成长速度。" },
  roe_ttm: { name: "单期 ROE 代理", description: "最近报告单期净利润除以期末股东权益；字段名沿用 roe_ttm，不代表滚动十二个月。" },
  turnover_rate: { name: "换手率", description: "成交量相对可交易流通规模的比例，用于观察交易活跃度。" },
  volatility_5: { name: "5 日波动率", description: "最近 5 个日收益率的总体标准差，用于衡量短期价格波动。" },
  volume_chg_1d: { name: "1 日成交量变化", description: "当前成交量相对上一可信交易日成交量的变化。" },
};

const factorNameLabels: Record<string, string> = {
  turnover_percentile_120: "120 日换手率分位数", momentum_20: "20 日动量", momentum_60: "60 日动量",
  volatility_20: "20 日波动率", downside_volatility_20: "20 日下行波动率", max_drawdown_20: "20 日最大回撤",
  liquidity_20: "20 日平均成交额", volume_stability_20: "20 日量能稳定性", abnormal_volume_pressure: "异常成交量压力",
  reversal_5: "5 日反转", beta_60: "60 日 Beta", residual_volatility_60: "60 日残差波动率",
  main_fund_net_inflow: "主力资金净流入", main_fund_net_inflow_ratio: "主力资金净流入率", main_fund_rank_score: "主力资金排名得分",
  main_fund_inflow_change_5: "主力资金 5 日流入变化", main_fund_inflow_percentile_20: "主力资金 20 日流入分位数",
  northbound_flow_ratio: "北向资金流入率", margin_financing_change_20: "20 日融资余额变化", etf_flow_industry_20: "20 日行业 ETF 资金流",
  institutional_crowding_score: "机构拥挤度得分", crowding_score: "市场拥挤度得分", decision_score: "综合决策得分",
  rate_trend_10y: "10 年期利率趋势", yield_curve_slope: "收益率曲线斜率", social_financing_impulse: "社融脉冲",
  m1_m2_gap: "M1-M2 剪刀差", usd_cnh_trend: "美元兑离岸人民币趋势", commodity_inflation_proxy: "商品通胀代理",
  credit_spread_change: "信用利差变化", earnings_revision_1m: "1 个月盈利预测修正", earnings_revision_breadth: "盈利预测修正广度",
  forecast_dispersion: "盈利预测分歧", guidance_surprise: "业绩指引超预期", profit_warning_risk: "业绩预警风险",
  current_ratio: "流动比率", interest_coverage: "利息保障倍数", operating_cash_debt: "经营现金流负债比",
  roic_ttm: "滚动投入资本回报率", roa_ttm: "滚动总资产回报率", gross_margin_ttm: "滚动毛利率",
  operating_margin_ttm: "滚动营业利润率", accrual_ratio: "应计利润比率", ocf_growth_yoy: "经营现金流同比增长",
  roe_stability_3y: "3 年净资产收益率稳定性", revenue_cagr_3y: "3 年营业收入复合增长率", net_profit_cagr_3y: "3 年净利润复合增长",
  receivable_growth_revenue_gap: "应收账款与收入增速差", inventory_growth_revenue_gap: "存货与收入增速差", financial_anomaly_score: "财务异常得分",
  industry_momentum_60: "60 日行业动量", industry_breadth_20: "20 日行业上涨广度", industry_turnover_share_change_20: "20 日行业成交占比变化",
  hot_rank_score: "热度排名得分", hot_heat: "市场热度", hot_rank_change: "热度排名变化", limit_up_strength: "涨停强度",
  dragon_tiger_net_buy_ratio: "龙虎榜净买入率", limit_up_breadth: "涨停广度", limit_up_failure_rate: "涨停开板率",
  consecutive_limit_up_height: "连板高度", market_sentiment_score: "市场情绪得分", sentiment_temperature_percentile: "情绪温度分位数",
  overheat_reversal_signal: "过热反转信号", policy_event_score: "政策事件得分", market_cap: "总市值",
  earnings_yield: "盈利收益率", book_to_price: "账面市值比", sales_to_price: "销售收入市值比", fcf_yield: "自由现金流收益率",
  ev_to_ebitda: "企业价值倍数", industry_valuation_gap: "行业估值偏离",
};

function localizedTransform(name: string) {
  const match = name.match(/^(.*)__(industry_neutral|rank|winsor|zscore)$/);
  if (!match) return null;
  const base = factorCopy[match[1]]?.name ?? factorNameLabels[match[1]] ?? match[1];
  const labels: Record<string, { suffix: string; description: string }> = {
    industry_neutral: { suffix: "行业中性化", description: "在行业分组内调整原始因子，降低行业暴露对横截面比较的影响。" },
    rank: { suffix: "排名转换", description: "把原始因子转换为横截面排名，降低极端数值对比较的影响。" },
    winsor: { suffix: "缩尾处理", description: "对原始因子的极端值进行缩尾处理，提高统计稳定性。" },
    zscore: { suffix: "标准化", description: "将原始因子转换为均值为零、标准差为一的标准分数。" },
  };
  const copy = labels[match[2]];
  return { name: `${base}（${copy.suffix}）`, description: copy.description };
}

export function localizedName(factor: FactorDefinition) {
  const transformed = localizedTransform(factor.name);
  if (transformed) return transformed.name;
  return factorCopy[factor.name]?.name ?? factorNameLabels[factor.name] ?? factor.display_name ?? factor.name;
}

export function localizedDescription(factor: FactorDefinition) {
  const transformed = localizedTransform(factor.name);
  if (transformed) return transformed.description;
  if (factorCopy[factor.name]?.description) return factorCopy[factor.name].description;
  if (factor.description && /[\u3400-\u9fff]/.test(factor.description)) return factor.description;
  return `${localizedName(factor)}反映相关财务、市场或资金变化，可用于横截面比较与组合筛选。请结合输入数据、计算窗口和最近画像理解该指标。`;
}

export function categoryLabel(factor: FactorDefinition) {
  return factorCategoryLabels[factor.category] ?? factor.category ?? "未分类";
}

/** 按因子代码查中文名（不含变换后缀）。 */
export function factorDisplayName(name: string) {
  return factorCopy[name]?.name ?? factorNameLabels[name] ?? name;
}
