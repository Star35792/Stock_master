"""因子分类常量与显示名。

因子按决策域分类，用于分组展示、过滤和后续按域设置权重。分类口径与
``aiDoc/research/decision-factor-framework.md`` 的因子域保持一致；为避免
单因子域过碎，合并宏观流动性 + 信用周期为 ``macro``，资金结构 + 拥挤度
为 ``capital_flow``。
"""

from __future__ import annotations

from enum import Enum


class FactorCategory(str, Enum):
    """因子决策域分类。"""

    PRICE_VOLUME_RISK = "price_volume_risk"
    FINANCIAL_HEALTH = "financial_health"
    FUNDAMENTAL_QUALITY = "fundamental_quality"
    VALUATION = "valuation"
    INDUSTRY_PROSPERITY = "industry_prosperity"
    SENTIMENT = "sentiment"
    MACRO = "macro"
    POLICY = "policy"
    EARNINGS_REVISION = "earnings_revision"
    CAPITAL_FLOW = "capital_flow"
    COMPOSITE = "composite"


FACTOR_CATEGORY_LABELS: dict[str, str] = {
    FactorCategory.PRICE_VOLUME_RISK.value: "价量与风险",
    FactorCategory.FINANCIAL_HEALTH.value: "财务健康",
    FactorCategory.FUNDAMENTAL_QUALITY.value: "基本面质量与成长",
    FactorCategory.VALUATION.value: "估值与安全边际",
    FactorCategory.INDUSTRY_PROSPERITY.value: "行业景气",
    FactorCategory.SENTIMENT.value: "市场情绪",
    FactorCategory.MACRO.value: "宏观与信用",
    FactorCategory.POLICY.value: "政策与监管",
    FactorCategory.EARNINGS_REVISION.value: "盈利预期修正",
    FactorCategory.CAPITAL_FLOW.value: "资金结构与拥挤度",
    FactorCategory.COMPOSITE.value: "复合决策",
}

# 有序展示，供前端按固定顺序分组
FACTOR_CATEGORY_ORDER: list[str] = [category.value for category in FactorCategory]


def factor_category_label(category: str | None) -> str:
    """返回分类的中文显示名，未知或缺失分类回退为“未分类”。"""
    if not category:
        return "未分类"
    return FACTOR_CATEGORY_LABELS.get(category, category)