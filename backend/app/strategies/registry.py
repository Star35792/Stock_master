"""Catalog of the built-in strategy definitions.

This module is the single place where a built-in strategy is *declared*.
The declarations are plain module constants so they can be inspected and
unit-tested without a database; :func:`register_all_strategy_definitions`
writes them into ``strategy.strategy_definition`` idempotently, and the
service layer reads the registered row back before it runs a screen.  The
point of the round trip is that a run must be reproducible from the catalog
alone: the locked parameters travel with the definition instead of living in
the caller's head.

Versioning
----------
The strategy *version* and the screening *kernel* version move independently.
``value_investing_margin@v1`` screened for a margin of safety and asked only
that a company be profitable.  ``@v2`` keeps the same locked 30% margin and
adds a business-quality gate (a minimum annualised three-year return on
equity), because a price gate on its own finds cheap securities -- and a
business that cannot earn its cost of capital stays cheap forever.

``@v3`` keeps both and adds the two things a margin of safety is useless
without: a value-trap gate (a business whose revenue or profit keeps
falling, or whose balance sheet keeps levering up, is losing value rather
than being mispriced) and a growth path (a business that compounds fast has
not yet earned a high return on its accumulated equity, so demanding a
mature return on equity would reject exactly the businesses worth owning).

All definitions stay registered: the superseded ones are retired, not
deleted, so a past run can still be reproduced from the catalog.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.models.strategy import StrategyDefinition
from app.repositories.strategy import StrategyDefinitionRepository
from app.strategies.value_investing import ValueInvestingParams

#: Name of the built-in value-investing strategy.
VALUE_INVESTING_NAME = "value_investing_margin"
#: The current version of the built-in value-investing strategy.
VALUE_INVESTING_VERSION = "v3"
#: The superseded first version: price gate only, no business-quality gate.
VALUE_INVESTING_V1_VERSION = "v1"
#: The superseded second version: price gate plus a return-on-equity gate,
#: but no value-trap gate and no growth path.
VALUE_INVESTING_V2_VERSION = "v2"

#: The locked margin of safety.  Changing it means publishing a new version
#: of the definition, not editing this row in place: the strategy document
#: requires ``required_margin`` to be fixed in advance and never fitted to
#: realised returns.
VALUE_INVESTING_REQUIRED_MARGIN = Decimal("0.30")

#: Minimum annualised three-year return on equity required by ``@v2``.
#:
#: Why 8%: on the 2026-11-09 screen universe the annualised three-year ROE
#: distribution has a median of 4.6% and a 75th percentile of 8.9%.  A
#: threshold of 8% therefore asks for a business in the upper quartile of
#: the market -- one that earns roughly its cost of equity -- while still
#: leaving 1,651 securities in the universe, so the gate narrows the field
#: without emptying it.  It is a *locked* parameter: a different bar means a
#: new version, never a refit.
VALUE_INVESTING_MIN_ROE_3Y = Decimal("0.08")

#: Minimum compound annual growth rate of revenue required by ``@v3``'s
#: growth path.
#:
#: Why 12%: on the 2026-11-09 universe the three-year revenue CAGR has a
#: median of 3.2% and a 75th percentile of 12.3%.  A threshold of 12% asks
#: for a business in the top quartile of the market by growth, which is
#: what separates a *growth* business from one that merely has not yet
#: declined.  It is a *locked* parameter.
VALUE_INVESTING_MIN_REVENUE_CAGR_3Y = Decimal("0.12")

#: Minimum compound annual growth rate of attributable net profit required
#: by ``@v3``'s growth path.
#:
#: Why 15%: the three-year net profit CAGR has a median of -0.6% and a 75th
#: percentile of 16.4%.  Requiring 15% means the profit has to compound at
#: roughly the pace of the top quartile -- a revenue-only grower that is
#: merely holding its margin does not qualify.  Note that only 3,722 of
#: 5,560 securities have a positive profit base in both FY2022 and FY2025,
#: so this threshold is only observable for that population; the rest fall
#: back to the return-on-equity path.
VALUE_INVESTING_MIN_NET_PROFIT_CAGR_3Y = Decimal("0.15")

#: Number of consecutive annual declines that mark a value trap.
#:
#: Why 2: a single down year is noise; two in a row is a direction.  On the
#: 2026-11-09 universe 1,363 securities fell two years running on net
#: profit and 1,305 on revenue, and 838 fell on both -- those are the
#: securities whose "cheap" multiple reflects a business that is losing
#: value rather than one the market has mispriced.
VALUE_INVESTING_DECLINE_STREAK_YEARS = 2

#: Maximum tolerated rise in the liabilities-to-assets ratio across the
#: trend window.
#:
#: Why 10 percentage points: the median change across the market is +0.9
#: points and the 75th percentile +7.6 points, so 10 points flags the top
#: decile of leverage creep (1,098 securities).  A balance sheet that is
#: levering up while the business is not compounding is the other half of
#: the value trap.
VALUE_INVESTING_MAX_DEBT_RATIO_RISE_3Y = Decimal("0.10")

#: Parameter contract of the value-investing strategy.  The values live in
#: the dedicated columns / rows of the definition; this JSON describes the
#: shape so a reader can validate a caller-supplied override.
VALUE_INVESTING_PARAMETERS_SCHEMA: dict[str, Any] = {
    "required_margin": {
        "type": "decimal",
        "gt": 0,
        "lt": 1,
        "locked": True,
        "description": "所需安全边际；买入成本上限 = 保守内在价值 x (1 - required_margin)",
    },
    "max_pe": {"type": "decimal", "gt": 0, "locked": True},
    "max_pb": {"type": "decimal", "gt": 0, "locked": True},
    "min_listed_days": {"type": "integer", "min": 0, "locked": True},
    "min_amount_20d": {"type": "decimal", "min": 0, "locked": True},
    "min_history_observations": {"type": "integer", "min": 1, "locked": True},
    "min_cash_conversion_3y": {"type": "decimal", "gt": 0, "locked": True},
    "min_roe_3y": {
        "type": "decimal",
        "gt": 0,
        "locked": True,
        "nullable": True,
        "description": (
            "3 年年化 ROE 下限（累计归母净利 / 平均净资产 / 3）；"
            "为空表示不启用企业质地闸门"
        ),
    },
    "min_revenue_cagr_3y": {
        "type": "decimal",
        "gt": 0,
        "locked": True,
        "nullable": True,
        "description": "3 年营收复合增速下限；为空表示不启用成长路径",
    },
    "min_net_profit_cagr_3y": {
        "type": "decimal",
        "gt": 0,
        "locked": True,
        "nullable": True,
        "description": "3 年归母净利复合增速下限；为空表示不启用成长路径",
    },
    "decline_streak_years": {
        "type": "integer",
        "min": 1,
        "locked": True,
        "nullable": True,
        "description": "连续年度下滑年数阈值；达到即判价值陷阱；为空表示不启用陷阱闸门",
    },
    "max_debt_ratio_rise_3y": {
        "type": "decimal",
        "min": 0,
        "locked": True,
        "nullable": True,
        "description": "资产负债率在趋势窗口内的最大允许上升幅度；为空表示不启用",
    },
    "anchor_quantile": {"type": "decimal", "gt": 0, "le": 1, "locked": True},
    "top_n": {"type": "integer", "min": 1, "locked": True},
    "max_per_industry": {"type": "integer", "min": 1, "locked": True},
}

#: Locked thresholds shared by every version.  ``required_margin`` is
#: deliberately absent: it has its own column so that one number never lives
#: in two places.
VALUE_INVESTING_COMMON_PARAMETERS: dict[str, Any] = {
    "max_pe": "20",
    "max_pb": "2",
    "min_listed_days": 730,
    "min_amount_20d": "50000000",
    "min_history_observations": 500,
    "min_cash_conversion_3y": "0.8",
    "anchor_quantile": "0.25",
    "top_n": 10,
    "max_per_industry": 2,
}

VALUE_INVESTING_INPUTS: list[str] = [
    "market.daily_bar.close",
    "market.daily_bar.amount",
    "market.daily_bar.special_treatment_status",
    "market.security.industry",
    "market.security.list_date",
    "research.valuation_history.pe_ttm",
    "research.valuation_history.pb_mrq",
    "research.valuation_history.total_shares",
    "fundamental.financial_statement.income.parent_holder_net_profit",
    "fundamental.financial_statement.cash_flow.act_cash_flow_net",
    "fundamental.financial_statement.balance.holder_equity_total",
    "fundamental.financial_indicator.index_weighted_avg_roe",
    "fundamental.financial_indicator.sale_gross_margin",
    "fundamental.financial_indicator.assets_debt_ratio",
    "fundamental.financial_indicator.calculate_parent_holder_net_profit_yoy_growth_ratio",
    "market.corporate_action.cash_dividend",
]

#: Declarative catalog.  Each entry is one strategy definition.
BUILTIN_STRATEGY_DEFINITIONS: tuple[dict[str, Any], ...] = (
    {
        "name": VALUE_INVESTING_NAME,
        "version": VALUE_INVESTING_V1_VERSION,
        "display_name": "价值投资安全边际选股 v1（仅价格闸门）",
        "description": (
            "先准入再估值的价值投资筛选：上市与交易状态准入、三年盈利与现金流质量、"
            "绝对估值区间，最后以双锚（盈利锚 + 净资产锚）取保守值，"
            "按买入成本上限 = V_low x (1 - required_margin) 判定是否已具备安全边际。"
            "本版本只筛价格与最低盈利质量，不含企业质地闸门，已被 v2 取代。"
            "输出为研究候选名单，不产生权重、订单或收益承诺。"
        ),
        "category": "value_investing",
        "parameters_schema": VALUE_INVESTING_PARAMETERS_SCHEMA,
        "locked_parameters": [
            "required_margin",
            "max_pe",
            "max_pb",
            "min_listed_days",
            "min_amount_20d",
            "min_history_observations",
            "min_cash_conversion_3y",
            "anchor_quantile",
            "top_n",
            "max_per_industry",
        ],
        "required_margin": VALUE_INVESTING_REQUIRED_MARGIN,
        "parameters": dict(VALUE_INVESTING_COMMON_PARAMETERS),
        "inputs": VALUE_INVESTING_INPUTS,
        "is_active": False,
        "status": "retired",
    },
    {
        "name": VALUE_INVESTING_NAME,
        "version": VALUE_INVESTING_V2_VERSION,
        "display_name": "价值投资安全边际选股 v2（价格闸门 + 企业质地闸门）",
        "description": (
            "在 v1 的基础上补上「买股票就是买企业」的可量化一半："
            "新增 3 年年化 ROE 下限这一硬闸门（默认 8%），"
            "过滤掉「便宜但赚不回资本成本」的价值陷阱；"
            "同时披露毛利率、资产负债率、行业内 3 年 ROE 中位数、"
            "行业归母净利同比中位数、近 5 年分红年数与财报修订次数，"
            "并在报告中显式声明六个企业研究维度里哪些有数据、哪些只有代理、"
            "哪些完全不可得。安全边际仍锁定 30%，不随版本变化。"
            "本版本没有价值陷阱闸门与成长路径，已被 v3 取代。"
            "输出为研究候选名单，不产生权重、订单或收益承诺。"
        ),
        "category": "value_investing",
        "parameters_schema": VALUE_INVESTING_PARAMETERS_SCHEMA,
        "locked_parameters": [
            "required_margin",
            "max_pe",
            "max_pb",
            "min_listed_days",
            "min_amount_20d",
            "min_history_observations",
            "min_cash_conversion_3y",
            "min_roe_3y",
            "anchor_quantile",
            "top_n",
            "max_per_industry",
        ],
        "required_margin": VALUE_INVESTING_REQUIRED_MARGIN,
        "parameters": {
            **VALUE_INVESTING_COMMON_PARAMETERS,
            "min_roe_3y": str(VALUE_INVESTING_MIN_ROE_3Y),
        },
        "inputs": VALUE_INVESTING_INPUTS,
        "is_active": False,
        "status": "retired",
    },
    {
        "name": VALUE_INVESTING_NAME,
        "version": VALUE_INVESTING_VERSION,
        "display_name": "价值投资安全边际选股 v3（价格 + 质地 + 价值陷阱闸门 + 成长路径）",
        "description": (
            "在 v2 的基础上补上价值投资第三、第四、第五节的三件事："
            "（1）价值陷阱闸门——用 4 个年报的连续下滑年数（营收、净利各 2 年）"
            "与资产负债率上升幅度把「PE 低是因为生意本身在失去价值」的标的"
            "判为已知不合格（excluded），而不是让它冒充低估；"
            "（2）成长路径——价值投资与成长投资并非对立，"
            "连续高增长的企业尚未在累积的净资产上赚出高 ROE，"
            "因此对 3 年营收复合增速 >= 12% 且归母净利复合增速 >= 15% 的企业"
            "改走成长路径准入（仍必须通过价格闸门与陷阱闸门）；"
            "（3）DCF 定位——内在价值仍用盈利锚与净资产锚双锚取保守值，"
            "绝对估值折现（DCF/FCFE）由独立的只读研究模块承担，"
            "不进本闸门，因为折现率与现金流假设的敏感性不适合做硬闸门。"
            "安全边际仍锁定 30%，不随版本变化。"
            "输出为研究候选名单，不产生权重、订单或收益承诺。"
        ),
        "category": "value_investing",
        "parameters_schema": VALUE_INVESTING_PARAMETERS_SCHEMA,
        "locked_parameters": [
            "required_margin",
            "max_pe",
            "max_pb",
            "min_listed_days",
            "min_amount_20d",
            "min_history_observations",
            "min_cash_conversion_3y",
            "min_roe_3y",
            "min_revenue_cagr_3y",
            "min_net_profit_cagr_3y",
            "decline_streak_years",
            "max_debt_ratio_rise_3y",
            "anchor_quantile",
            "top_n",
            "max_per_industry",
        ],
        "required_margin": VALUE_INVESTING_REQUIRED_MARGIN,
        "parameters": {
            **VALUE_INVESTING_COMMON_PARAMETERS,
            "min_roe_3y": str(VALUE_INVESTING_MIN_ROE_3Y),
            "min_revenue_cagr_3y": str(VALUE_INVESTING_MIN_REVENUE_CAGR_3Y),
            "min_net_profit_cagr_3y": str(VALUE_INVESTING_MIN_NET_PROFIT_CAGR_3Y),
            "decline_streak_years": VALUE_INVESTING_DECLINE_STREAK_YEARS,
            "max_debt_ratio_rise_3y": str(
                VALUE_INVESTING_MAX_DEBT_RATIO_RISE_3Y
            ),
        },
        "inputs": VALUE_INVESTING_INPUTS,
        "is_active": True,
        "status": "active",
    },
)


def register_all_strategy_definitions(db: Session) -> dict[str, int]:
    """Register every built-in strategy definition.

    Idempotent: each declaration is upserted on ``(name, version)``, so
    calling this on every start-up refreshes the catalog without creating
    duplicates.
    """
    repository = StrategyDefinitionRepository()
    counts: dict[str, int] = {}
    for definition in BUILTIN_STRATEGY_DEFINITIONS:
        repository.ensure(
            db,
            name=definition["name"],
            version=definition["version"],
            display_name=definition["display_name"],
            description=definition["description"],
            category=definition["category"],
            parameters_schema=definition["parameters_schema"],
            locked_parameters=definition["locked_parameters"],
            required_margin=definition["required_margin"],
            parameters=definition["parameters"],
            inputs=definition["inputs"],
            is_active=definition["is_active"],
            status=definition["status"],
        )
        counts[definition["name"]] = counts.get(definition["name"], 0) + 1
    db.flush()
    return counts


def params_from_values(
    required_margin: Any,
    parameters: dict[str, Any] | None,
) -> ValueInvestingParams:
    """Rebuild the screening parameters from raw catalog values.

    ``required_margin`` comes from the dedicated column and the remaining
    thresholds from the ``parameters`` JSON, so a run is reproducible from
    the catalog alone rather than from the code that happened to be checked
    out.  A margin outside ``(0, 1)`` is rejected instead of silently
    clamped.
    """
    if required_margin is None:
        raise ValueError("strategy definition has no required_margin")
    values = parameters or {}
    kwargs: dict[str, Any] = {"required_margin": Decimal(str(required_margin))}
    for key in (
        "max_pe",
        "max_pb",
        "min_amount_20d",
        "min_cash_conversion_3y",
        "anchor_quantile",
    ):
        if key in values:
            kwargs[key] = values[key]
    for key in (
        "min_listed_days",
        "min_history_observations",
        "top_n",
        "max_per_industry",
    ):
        if key in values:
            kwargs[key] = values[key]
    if values.get("min_roe_3y") is not None:
        kwargs["min_roe_3y"] = values["min_roe_3y"]
    for key in (
        "min_revenue_cagr_3y",
        "min_net_profit_cagr_3y",
        "max_debt_ratio_rise_3y",
    ):
        if values.get(key) is not None:
            kwargs[key] = values[key]
    if values.get("decline_streak_years") is not None:
        kwargs["decline_streak_years"] = values["decline_streak_years"]
    return ValueInvestingParams(**kwargs)


def params_from_definition(
    definition: StrategyDefinition,
) -> ValueInvestingParams:
    """Rebuild the screening parameters from a registered definition row.

    The row is the fact source: the locked margin comes from the
    ``required_margin`` column and the remaining thresholds from the
    ``parameters`` JSON.  A definition that carries a margin outside
    ``(0, 1)`` is rejected instead of silently clamped.
    """
    if definition.required_margin is None:
        raise ValueError(
            f"strategy definition {definition.name}@{definition.version} has no "
            "required_margin"
        )
    return params_from_values(definition.required_margin, definition.parameters)
