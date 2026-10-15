"""Financial-sector (bank/insurer/broker) handling for generic statement factors.

Generic balance-sheet and income-statement ratios carry assumptions that do
not hold for financial issuers: leverage is the business model rather than a
distress signal, interest expense is cost of revenue, and there is no gross
margin or working-capital cycle in the industrial sense. Rather than emitting
misleading values, the financial factor service refuses to compute these
factors for securities classified in a financial industry and records the
exclusion in the factor run metadata.

Industry-specific metrics (net interest margin, capital adequacy, combined
ratio, ...) require dedicated data sources and are declared as a limitation
in ``aiDoc/plans/hard-gates-execution-plan.md`` section 5; they are not
silently proxied by the generic factors.

The token list mirrors the existing convention in
``app/services/price_value_screen.py`` so all financial-sector detection in
the platform stays consistent.
"""

FINANCIAL_TOKENS = (
    "银行",
    "保险",
    "证券",
    "券商",
    "期货",
    "金融",
    "信托",
    "bank",
    "insur",
    "financ",
)

POLICY_VERSION = "financial_sector_generic_factor_exclusion_v1"

# Factors whose structural assumptions are invalid for financial issuers.
# Factors kept for financial issuers (roe/roa, growth, cagr, stability) remain
# computable because the underlying statements still report the required lines.
EXCLUDED_FACTORS = frozenset(
    {
        "debt_to_assets",
        "current_ratio",
        "interest_coverage",
        "operating_cash_debt",
        "accrual_ratio",
        "receivable_growth_revenue_gap",
        "inventory_growth_revenue_gap",
        "financial_anomaly_score",
        "gross_margin_ttm",
        "operating_margin_ttm",
        "ocf_to_net_income_ttm",
        "roic_ttm",
    }
)


def is_financial_industry(industry: str | None) -> bool:
    if not industry:
        return False
    normalized = industry.strip().lower()
    if not normalized:
        return False
    return any(token in normalized for token in FINANCIAL_TOKENS)
