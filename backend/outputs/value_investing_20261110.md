# 价值投资安全边际选股报告

- 策略：`value_investing_margin@v3`（内核 `value_investing_margin_v3`）
- 锁定安全边际：`0.3000000000`
- 行情交易日：2026-11-10
- 估值分布窗口：2024-05-06 ~ 2026-11-10
- 年报期：2023-12-31、2024-12-31、2025-12-31
- 股票池：5586；达标：1；优先研究名单：1；递延：0

> 本报告为只读研究输出，不产生权重、订单或收益承诺；`research_only=true / pit_verified=false / execution_eligible=false`。

## 优先研究名单

| # | 代码 | 名称 | 行业 | 收盘价 | PE | PB | 历史倍数保守锚 V_low | 买入成本上限 | 安全边际 | 3 年年化 ROE | 3 年营收 CAGR | 3 年净利 CAGR | 准入路径 |
|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| 1 | 601799.SH | 星宇股份 | 汽车零部件 | 70.8400 | 14.3043 | 1.7624 | 110.6911 | 77.4838 | 36.0% | 13.46% | 22.75% | 19.93% | ROE + 成长双路径 |

## 递延（达标但未进入研究容量）

| # | 代码 | 名称 | 行业 | 收盘价 | 历史倍数保守锚 | 买入成本上限 |
|---|---|---|---|---:|---:|---:|
| - | - | - | - | - | - | - |

## 企业质地体检

- 质地硬闸门：3 年年化 ROE ≥ 8.00%
- 3 年年化 ROE 口径：3 年累计归母净利 / 3 个年报期末净资产均值 / 3（年化）

### 六个企业研究维度的覆盖状态

| 维度 | 覆盖 | 是否硬闸门 | 用到的字段 | 说明 |
|---|---|---|---|---|
| 企业靠什么赚钱 | 无数据 | 否 | - | 主营业务构成（分产品/分地区营收、毛利率）在本项目没有数据源：财报与估值历史都不含分项营收。只能由人工阅读年报。 |
| 产品或服务有没有竞争力 | 仅代理 | 否 | `gross_margin`、`industry_roe_median`、`roe_3y` | 无市场份额与护城河数据源。用毛利率水平、行业内 3 年 ROE 中位数作为相对竞争力的代理，不等同于护城河本身。 |
| 行业未来有没有成长空间 | 仅代理 | 否 | `industry_profit_yoy_median`、`revenue_cagr_3y`、`net_profit_cagr_3y` | 由行业内公司归母净利同比中位数自建，不是行业景气预测；行业标签只有当前值，没有时点历史。企业自身 3 年营收/净利复合增速作为行业空间的代理。 |
| 企业是否具有持续盈利能力 | 有数据 | 是 | `roe_3y`、`net_profit_3y`、`cash_conversion_3y`、`ttm_net_profit`、`net_profit_decline_streak` | 3 年年化 ROE（累计归母净利 / 平均净资产 / 3）+ 三年累计净利为正+ 三年经营现金流转化率 + 净利连跌年数（价值陷阱信号）。硬闸门：3 年年化 ROE >= 0.08（或走成长路径：3 年营收复合增速 >= 0.12 且净利复合增速 >= 0.15）。 |
| 是不是价值陷阱 | 有数据 | 是 | `revenue_decline_streak`、`net_profit_decline_streak`、`debt_ratio_change_3y`、`revenue_cagr_3y` | 低 PE 不等于便宜：营收连跌、净利连跌、资产负债率上行三者任一命中即判为价值陷阱（已知不合格，非证据缺失）。陷阱闸门优先于成长路径——营收涨而利润跌、杠杆上升的公司不是成长故事，是带好标题的陷阱。 |
| 管理层是否诚信、优秀 | 仅代理 | 否 | `dividend_years_5y` | 无董监高履历、薪酬、股权质押等数据源。用近 5 年分红连续性作为股东回报与治理的弱代理；财报修订次数不可用——库内 152,921 行中仅 173 行是真修订，其余是年报/季报两种口径对同一报告期的重复入库，拿它当治理信号是假的。这不等于对管理层的评价。 |
| 未来十年是否仍然有竞争优势 | 无数据 | 否 | - | 财报最早只到 2022 年报，不足以构建十年序列；护城河也没有量化接口。该维度只能由人工长期跟踪。 |

> 质地层只覆盖六个维度中的部分：'unavailable' 的维度没有任何数据支撑，'proxy' 的维度是替代指标而非该问题本身。通过全部硬闸门只说明「价格已进入安全边际区且可量化的质地指标达标」，不构成对企业质地的结论。


## 价值陷阱闸门

> 低 PE 不等于便宜：营收连跌、净利连跌、资产负债率上行三者任一命中，即判为「便宜是因为生意本身在失去价值」。

- 连续下滑阈值：2 年
- 资产负债率上升上限：10.00%
- 命中陷阱闸门的标的：2765 只
- 其中因 ROE 不足、但成长达标而被成长路径放行的标的：155 只

| 陷阱信号 | 证券数 |
|---|---:|
| 归母净利连年下滑 | 1363 |
| 营收连年下滑 | 1305 |
| 资产负债率上行 | 1098 |
| 趋势窗口不完整（证据缺失，阻断） | 23 |

被剔除标的（部分）：000014.SZ、000026.SZ、000049.SZ、000155.SZ、000419.SZ、000680.SZ、000762.SZ、000779.SZ、000878.SZ、001259.SZ、001322.SZ、001696.SZ、002044.SZ、002159.SZ、002216.SZ、002331.SZ、002390.SZ、002561.SZ、002597.SZ、002599.SZ 等 2765 只

## 仅价格闸门对照（关闭企业质地闸门）

- 仅价格闸门达标：5 只
- 施加企业质地闸门后达标：1 只
- 被质地闸门剔除：4 只 —— 000100.SZ、002758.SZ、600686.SH、600839.SH

| 代码 | 名称 | 行业 | 3 年年化 ROE | 收盘价 | 历史倍数保守锚 | 买入成本上限 |
|---|---|---|---:|---:|---:|---:|
| 000100.SZ | TCL科技 | 光学光电子 | 2.02% | 4.6000 | 6.8259 | 4.7781 |
| 002758.SZ | 浙农股份 | 综合Ⅱ | 5.70% | 7.4600 | 12.2216 | 8.5551 |
| 600686.SH | 金龙汽车 | 商用车 | 5.51% | 8.8600 | 15.4165 | 10.7915 |
| 600839.SH | 四川长虹 | 黑色家电 | 3.17% | 5.1300 | 11.2234 | 7.8563 |

## 未通过原因统计

| 原因 | 证券数 |
|---|---:|
| `margin_of_safety_not_met` | 5381 |
| `roe_3y_below_threshold` | 3745 |
| `pb_above_ceiling` | 3297 |
| `pe_above_ceiling` | 2929 |
| `pe_history_insufficient` | 2400 |
| `ttm_net_profit_not_positive` | 1684 |
| `three_year_net_profit_not_positive` | 1475 |
| `liquidity_insufficient` | 1405 |
| `net_profit_declining` | 1363 |
| `revenue_declining` | 1305 |
| `leverage_rising` | 1098 |
| `cyclic_industry_separate_model` | 987 |
| `cash_conversion_weak` | 937 |
| `pe_fair_anchor_unavailable` | 598 |
| `pb_history_insufficient` | 361 |
| `listed_days_insufficient` | 268 |
| `special_treatment_restricted` | 201 |
| `financial_industry_separate_model` | 122 |
| `shareholders_equity_not_positive` | 44 |
| `roe_3y_missing` | 28 |
| `three_year_window_incomplete` | 23 |
| `trend_window_incomplete` | 23 |
| `suspension_state_unknown` | 14 |
| `trusted_quote_missing` | 14 |
| `current_pb_unavailable` | 12 |
| `current_pe_unavailable` | 10 |
| `liquidity_window_missing` | 9 |
| `ttm_net_profit_missing` | 8 |
| `three_year_net_profit_missing` | 7 |
| `three_year_operating_cash_flow_missing` | 7 |
| `industry_missing` | 4 |
| `intrinsic_value_unavailable` | 3 |
| `listing_date_unverified` | 3 |
| `pb_fair_anchor_unavailable` | 3 |
| `shareholders_equity_missing` | 3 |
| `total_shares_missing` | 1 |