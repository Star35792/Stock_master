"""add category to factor_definition

Revision ID: 20260722_0024
Revises: 20260721_0023
Create Date: 2026-07-22
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260722_0024"
down_revision: str | None = "20260721_0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# 基础因子名 -> 决策域分类。派生因子（name 含 "__"）由基础因子继承。
# 分类口径见 aiDoc/research/decision-factor-framework.md。
_FACTOR_CATEGORY_BY_NAME: dict[str, list[str]] = {
    "price_volume_risk": [
        "ret_1d", "volatility_5", "momentum_5", "ma_gap_5", "volume_chg_1d",
        "turnover_rate", "turnover_percentile_120", "momentum_20", "momentum_60",
        "volatility_20", "downside_volatility_20", "max_drawdown_20",
        "liquidity_20", "volume_stability_20", "abnormal_volume_pressure",
        "reversal_5", "beta_60", "residual_volatility_60",
    ],
    "financial_health": [
        "debt_to_assets", "current_ratio", "interest_coverage",
        "operating_cash_debt", "accrual_ratio", "financial_anomaly_score",
    ],
    "fundamental_quality": [
        "roe_ttm", "roic_ttm", "roa_ttm", "gross_margin_ttm",
        "operating_margin_ttm", "ocf_to_net_income_ttm", "revenue_growth_yoy",
        "ocf_growth_yoy", "net_profit_growth_yoy", "roe_stability_3y",
        "revenue_cagr_3y", "net_profit_cagr_3y",
        "receivable_growth_revenue_gap", "inventory_growth_revenue_gap",
    ],
    "valuation": [
        "market_cap", "earnings_yield", "book_to_price", "sales_to_price",
        "fcf_yield", "ev_to_ebitda", "industry_valuation_gap",
    ],
    "industry_prosperity": [
        "industry_momentum_60", "industry_breadth_20",
        "industry_turnover_share_change_20",
    ],
    "sentiment": [
        "hot_rank_score", "hot_heat", "hot_rank_change", "limit_up_strength",
        "dragon_tiger_net_buy_ratio", "limit_up_breadth",
        "limit_up_failure_rate", "consecutive_limit_up_height",
        "market_sentiment_score", "sentiment_temperature_percentile",
        "overheat_reversal_signal",
    ],
    "macro": [
        "rate_trend_10y", "yield_curve_slope", "social_financing_impulse",
        "m1_m2_gap", "usd_cnh_trend", "commodity_inflation_proxy",
        "credit_spread_change",
    ],
    "policy": ["policy_event_score"],
    "earnings_revision": [
        "earnings_revision_1m", "earnings_revision_breadth",
        "forecast_dispersion", "guidance_surprise", "profit_warning_risk",
    ],
    "capital_flow": [
        "main_fund_net_inflow", "main_fund_net_inflow_ratio",
        "main_fund_rank_score", "main_fund_inflow_change_5",
        "main_fund_inflow_percentile_20", "northbound_flow_ratio",
        "margin_financing_change_20", "etf_flow_industry_20",
        "institutional_crowding_score", "crowding_score",
    ],
    "composite": ["decision_score"],
}


def _backfill_base_factors_sql() -> str:
    clauses = []
    for category, names in _FACTOR_CATEGORY_BY_NAME.items():
        quoted = ", ".join("'" + name + "'" for name in names)
        clauses.append("WHEN name IN (" + quoted + ") THEN '" + category + "'")
    return (
        "UPDATE factor.factor_definition SET category = CASE "
        + " ".join(clauses)
        + " ELSE category END WHERE category IS NULL"
    )


def upgrade() -> None:
    op.add_column(
        "factor_definition",
        sa.Column("category", sa.String(length=50), nullable=True),
        schema="factor",
    )
    op.execute(_backfill_base_factors_sql())
    # 派生因子（xxx__transform）继承基础因子分类
    op.execute(
        "UPDATE factor.factor_definition AS d SET category = base.category "
        "FROM factor.factor_definition AS base "
        "WHERE position('__' in d.name) > 0 "
        "AND split_part(d.name, '__', 1) = base.name "
        "AND base.category IS NOT NULL"
    )
    # 兜底：仍未分类的回退到价量与风险
    op.execute(
        "UPDATE factor.factor_definition SET category = 'price_volume_risk' "
        "WHERE category IS NULL"
    )
    op.alter_column(
        "factor_definition",
        "category",
        existing_type=sa.String(length=50),
        nullable=False,
        schema="factor",
    )
    op.create_index(
        "ix_factor_definition_category",
        "factor_definition",
        ["category"],
        schema="factor",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_factor_definition_category",
        table_name="factor_definition",
        schema="factor",
    )
    op.drop_column("factor_definition", "category", schema="factor")