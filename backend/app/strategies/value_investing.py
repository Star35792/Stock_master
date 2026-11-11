"""Value-investing screen with an absolute margin-of-safety price gate.

What this module is
-------------------
A deterministic, read-only screening kernel.  It answers one question for one
security: *is this a value-investing candidate whose price already carries the
required margin of safety?*

It is a **kernel**, not a service: it performs no I/O, opens no database, and
returns plain dictionaries.  The service layer (:mod:`app.services.
value_investing_screen`) supplies the evidence and the service callers decide
what to do with the answer.  Every number is a ``Decimal`` so the price gate
is exact, and every tie is broken explicitly so two runs on the same input
produce byte-identical output.

Why the valuation is anchored twice
-----------------------------------
A single PE anchor is not enough to state an intrinsic value.  When a
company's earnings were depressed for a long stretch, its *historical* PE
median is inflated, and ``EPS x median_PE`` happily produces a "conservative
value" far above anything the business is worth.  Two independent anchors
are therefore computed and the **smaller** one is used:

* an **earnings anchor** ``V_earnings = EPS_norm x PE_fair``;
* a **book anchor** ``V_book = BPS x PB_fair``.

``PE_fair`` / ``PB_fair`` are the stocks' own historical **lower** quantiles
of positive observations, which is the conservative direction: a loss-making
period is not allowed to count as a "cheap" sample, and the anchor is an
actually observed multiple rather than an interpolated one.

The price gate then follows
``aiDoc/strategy/20260922-1314-price-value-margin-strategy.md``:

    buy_cost_ceiling = V_low * (1 - required_margin)
    price condition  : P <= buy_cost_ceiling

``required_margin`` is a locked parameter of the strategy definition, not a
per-call knob, and it is never fitted to realised returns.

Why there is also a business-quality gate
-----------------------------------------
A price gate on its own finds *cheap* securities.  A business that cannot
earn its cost of capital stays cheap forever, and buying it is a value trap
rather than a margin of safety.  "Buying a stock means buying a business"
therefore needs a second, quantitative half: a minimum return on equity.

``min_roe_3y`` is that threshold, expressed as an annualised three-year
return on equity (``sum(net profit) / average(equity) / 3``) over the same
annual window the cash-conversion gate uses.  It is ``None`` in the first
published definition, which screened for cheapness and only required
profitability.  Setting it is a **new version of the definition**, never an
edit of a live one.

The other five research questions of "buying a business" are covered only
partially, and :func:`quality_profile` states exactly how: what is measured,
what is merely proxied, and what has no data source at all.  The screen never
implies that clearing a gate settles the question of business quality.

Boundaries
----------
* Research only.  The kernel produces no weights, no orders and no execution
  eligibility; the report always carries ``research_only=true``.
* Financial issuers and strong-cyclical industries are excluded because the
  generic industrial-company model does not apply to them.  Excluding is not
  a judgement that they are bad companies; it says the *model* does not fit.
* Missing evidence blocks.  An unknown value is never treated as a zero, and
  a missing gate never silently passes.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_CEILING, Decimal, localcontext

#: Version of the screening **kernel** (the code), as opposed to the version
#: of the strategy **definition** (the locked parameter set) which lives in
#: ``app/strategies/registry.py``.  The two move independently: a new kernel
#: may serve several definitions, and a new definition may reuse an old
#: kernel.
#:
#: ``v2`` added the return-on-equity quality gate; ``v3`` added the value-trap
#: gate and the growth path.  The kernel version changes whenever the
#: *behaviour* changes, so that a report can be traced back to the exact
#: rules that produced it -- two materially different kernels must never
#: share one version string.
KERNEL_VERSION = "value_investing_margin_v3"

#: Backwards-compatible alias.
VERSION = KERNEL_VERSION

#: Quantile used for the conservative fair multiple.  Lower is stricter.
DEFAULT_ANCHOR_QUANTILE = Decimal("0.25")

#: Statuses a row can end up with.
STATUS_EXCLUDED = "excluded"
STATUS_BLOCKED = "blocked"
STATUS_QUALIFIED = "qualified"
STATUS_SELECTED = "selected"


@dataclass(frozen=True)
class ValueInvestingParams:
    """Versioned thresholds of the built-in value-investing strategy.

    Every field is a locked parameter of the strategy definition; the
    registry writes them into ``strategy.strategy_definition`` so a run can
    be reproduced from the catalog alone.
    """

    required_margin: Decimal = Decimal("0.30")
    max_pe: Decimal = Decimal("20")
    max_pb: Decimal = Decimal("2")
    min_listed_days: int = 730
    min_amount_20d: Decimal = Decimal("50000000")
    min_history_observations: int = 500
    min_cash_conversion_3y: Decimal = Decimal("0.8")
    #: Minimum annualised three-year return on equity.  ``None`` disables the
    #: business-quality gate entirely, which is what the first published
    #: version of this strategy did: it screened for *cheap* securities and
    #: only asked that they be profitable, not that they be good businesses.
    #:
    #: A cheap price on a business that cannot earn its cost of capital is a
    #: value trap, not a margin of safety.  This gate is the quantitative
    #: half of "buying a stock means buying a business"; the qualitative
    #: half (moat, management, market share) has no data source in this
    #: project and is reported, never silently assumed.
    min_roe_3y: Decimal | None = None
    #: Minimum compound annual growth rate of revenue over the trend window
    #: (``None`` disables the growth path).  Used together with
    #: ``min_net_profit_cagr_3y`` to admit a *growing* business that has not
    #: yet earned a high return on the equity it has accumulated -- the
    #: "growth investment" branch of the same discipline.
    min_revenue_cagr_3y: Decimal | None = None
    #: Minimum compound annual growth rate of attributable net profit over
    #: the trend window (``None`` disables the growth path).
    min_net_profit_cagr_3y: Decimal | None = None
    #: Number of consecutive annual declines that count as a value trap.
    #: ``None`` disables the trap gate.
    decline_streak_years: int | None = None
    #: Maximum tolerated rise in the liabilities-to-assets ratio between the
    #: first and last period of the trend window, as a fraction
    #: (``0.10`` means ten percentage points).  ``None`` disables the check.
    max_debt_ratio_rise_3y: Decimal | None = None
    anchor_quantile: Decimal = DEFAULT_ANCHOR_QUANTILE
    top_n: int = 10
    max_per_industry: int = 2

    def __post_init__(self) -> None:
        # Locked parameters travel through a JSON column, so they arrive as
        # strings.  Normalise them here once instead of scattering casts
        # across the callers, and keep every comparison exact.
        for name in (
            "required_margin",
            "max_pe",
            "max_pb",
            "min_amount_20d",
            "min_cash_conversion_3y",
            "anchor_quantile",
        ):
            object.__setattr__(self, name, Decimal(str(getattr(self, name))))
        if self.min_roe_3y is not None:
            object.__setattr__(self, "min_roe_3y", Decimal(str(self.min_roe_3y)))
        for name in (
            "min_revenue_cagr_3y",
            "min_net_profit_cagr_3y",
            "max_debt_ratio_rise_3y",
        ):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, Decimal(str(value)))
        for name in ("min_listed_days", "min_history_observations", "top_n", "max_per_industry"):
            object.__setattr__(self, name, int(getattr(self, name)))
        if self.decline_streak_years is not None:
            object.__setattr__(
                self, "decline_streak_years", int(self.decline_streak_years)
            )

        if not Decimal(0) < self.required_margin < Decimal(1):
            raise ValueError("required_margin must be within (0, 1)")
        if self.min_roe_3y is not None and self.min_roe_3y <= 0:
            raise ValueError("min_roe_3y must be positive when it is set")
        for name in ("min_revenue_cagr_3y", "min_net_profit_cagr_3y"):
            value = getattr(self, name)
            if value is not None and value <= 0:
                raise ValueError(f"{name} must be positive when it is set")
        if (
            self.decline_streak_years is not None
            and self.decline_streak_years < 1
        ):
            raise ValueError("decline_streak_years must be at least 1")
        if self.max_debt_ratio_rise_3y is not None and self.max_debt_ratio_rise_3y < 0:
            raise ValueError("max_debt_ratio_rise_3y must not be negative")
        if not Decimal(0) < self.anchor_quantile <= Decimal(1):
            raise ValueError("anchor_quantile must be within (0, 1]")
        if self.max_pe <= 0 or self.max_pb <= 0:
            raise ValueError("max_pe and max_pb must be positive")
        if self.min_listed_days < 0:
            raise ValueError("min_listed_days must not be negative")
        if self.min_amount_20d < 0:
            raise ValueError("min_amount_20d must not be negative")
        if self.min_history_observations < 1:
            raise ValueError("min_history_observations must be positive")
        if self.top_n < 1:
            raise ValueError("top_n must be positive")
        if self.max_per_industry < 1:
            raise ValueError("max_per_industry must be positive")

    @property
    def price_factor(self) -> Decimal:
        """``1 - required_margin``: the share of intrinsic value a buyer may pay."""
        return Decimal(1) - self.required_margin


@dataclass(frozen=True)
class ValueInvestingEvidence:
    """Everything the kernel needs to judge one security.

    Missing evidence is expressed as ``None`` and blocks the row; it is never
    coerced into a zero.
    """

    symbol: str
    name: str | None = None
    industry: str | None = None
    #: Trusted unadjusted close on the screen date.
    close: Decimal | None = None
    #: Total shares used for per-share conversion.
    total_shares: Decimal | None = None
    #: Trailing-twelve-month net profit attributable to the parent.
    ttm_net_profit: Decimal | None = None
    #: Period the TTM figure ends at.
    ttm_period_end: date | None = None
    #: Sum of the last three complete annual net profits.
    net_profit_3y: Decimal | None = None
    #: Sum of the last three complete annual operating cash flows.
    ocf_3y: Decimal | None = None
    #: Shareholders' equity of the latest reported balance sheet.
    equity: Decimal | None = None
    equity_period_end: date | None = None
    #: Historical positive PE observations, oldest first.
    pe_history: tuple[Decimal, ...] = ()
    #: Historical positive PB observations, oldest first.
    pb_history: tuple[Decimal, ...] = ()
    #: Conservative fair multiple derived from ``pe_history``.
    #
    # The service layer computes the anchor in SQL because the raw history is
    #: millions of observations; it may hand the result in directly.  When it
    # is absent the kernel derives it from ``pe_history`` with
    # :func:`conservative_quantile`, so the two paths cannot disagree: the
    # service result is checked against the kernel rule in the test suite.
    pe_fair: Decimal | None = None
    pb_fair: Decimal | None = None
    #: Number of positive observations the anchors were derived from.  The
    #: service layer counts them in SQL; when the raw series is handed in the
    #: kernel counts it itself.
    pe_observations: int = 0
    pb_observations: int = 0
    #: Vendor PE/PB of the latest observation, kept for cross-checks.
    vendor_pe_ttm: Decimal | None = None
    vendor_pb_mrq: Decimal | None = None
    listed_days: int | None = None
    #: Mean traded amount over the trailing 20 trading days, in CNY.
    amount_20d: Decimal | None = None
    is_suspended: bool | None = None
    special_treatment_status: str | None = None
    is_financial_sector: bool = False
    is_cyclic_industry: bool = False
    # ---------------------------------------------------------- business quality
    #: Annualised return on equity over the strategy's three-year window:
    #: ``sum(net profit) / average(equity) / 3``.  ``None`` when the window
    #: is incomplete or the equity base is not positive.
    roe_3y: Decimal | None = None
    #: Gross margin of the latest reported period (percent, e.g. ``18.76``).
    gross_margin: Decimal | None = None
    #: Liabilities over assets of the latest reported period (percent).
    debt_ratio: Decimal | None = None
    #: Median annualised three-year ROE of the peers in the same industry.
    industry_roe_median: Decimal | None = None
    #: Median year-on-year attributable net profit growth of the industry.
    industry_profit_yoy_median: Decimal | None = None
    #: Number of distinct years in the trailing five with a cash dividend.
    dividend_years_5y: int | None = None
    # ---------------------------------------------------------- trend / trap
    #: Compound annual growth rate of revenue over the trend window.
    #: ``None`` when the window is incomplete or the base is not positive.
    revenue_cagr_3y: Decimal | None = None
    #: Compound annual growth rate of attributable net profit over the
    #: trend window.  ``None`` when the window is incomplete or the base is
    #: not positive -- a company that only just turned profitable has no
    #: meaningful growth rate yet.
    net_profit_cagr_3y: Decimal | None = None
    #: Number of consecutive annual periods in which revenue declined,
    #: counted backwards from the latest period.
    revenue_decline_streak: int | None = None
    #: Number of consecutive annual periods in which attributable net profit
    #: declined, counted backwards from the latest period.
    net_profit_decline_streak: int | None = None
    #: Change in the liabilities-to-assets ratio between the first and the
    #: last period of the trend window, as a fraction (``0.10`` = ten
    #: percentage points).  Negative means the balance sheet improved.
    debt_ratio_change_3y: Decimal | None = None
    #: How many of the four annual reports in the trend window were present.
    trend_periods_present: int | None = None
    #: How many of the strategy's three annual reports were actually
    #: available.  A partial window turns a three-year test into a
    #: two-year one without saying so, so the kernel blocks the row
    #: instead of quietly shortening the window.
    annual_periods_present: int | None = None
    #: How many of the three annual balance sheets were available.
    equity_periods_present: int | None = None
    notes: tuple[str, ...] = field(default_factory=tuple)


def conservative_quantile(
    values: Sequence[Decimal], quantile: Decimal
) -> Decimal | None:
    """Return the lower nearest-rank quantile of ``values``.

    The result is an actually observed value, so it is never above the
    linearly interpolated quantile: the anchor stays conservative, and the
    computation is exact Decimal arithmetic with no rounding policy to
    argue about.
    """
    if not values:
        return None
    ordered = sorted(values)
    count = len(ordered)
    rank = int(
        (quantile * Decimal(count)).to_integral_value(rounding=ROUND_CEILING)
    )
    rank = min(max(rank, 1), count)
    return ordered[rank - 1]


#: Backwards-compatible alias for the internal helper.
_conservative_quantile = conservative_quantile


def _positive(value: Decimal | None) -> bool:
    return value is not None and value.is_finite() and value > 0


def _non_negative(value: Decimal | None) -> bool:
    return value is not None and value.is_finite() and value >= 0


def quality_profile(params: ValueInvestingParams) -> dict[str, object]:
    """Describe how the six business-research dimensions are covered.

    "Buying a stock means buying a business" is not one check, it is six:
    what the business sells, whether it has a moat, whether its industry
    grows, whether it earns money consistently, whether management can be
    trusted, and whether the advantage survives a decade.

    This project can answer some of those with data, can only *proxy* the
    others, and cannot answer the rest at all.  The screen therefore reports
    the coverage instead of implying that a passing price gate means a good
    business.  Anything listed as ``proxy`` or ``unavailable`` still needs a
    human to read the company.
    """
    return {
        "roe_formula": "3 年累计归母净利 / 3 个年报期末净资产均值 / 3（年化）",
        "dimensions": [
            {
                "key": "how_the_business_earns",
                "label": "企业靠什么赚钱",
                "status": "unavailable",
                "gate": False,
                "fields": [],
                "note": (
                    "主营业务构成（分产品/分地区营收、毛利率）在本项目没有数据源："
                    "财报与估值历史都不含分项营收。只能由人工阅读年报。"
                ),
            },
            {
                "key": "product_competitiveness",
                "label": "产品或服务有没有竞争力",
                "status": "proxy",
                "gate": False,
                "fields": ["gross_margin", "industry_roe_median", "roe_3y"],
                "note": (
                    "无市场份额与护城河数据源。用毛利率水平、行业内 3 年 ROE "
                    "中位数作为相对竞争力的代理，不等同于护城河本身。"
                ),
            },
            {
                "key": "industry_growth_space",
                "label": "行业未来有没有成长空间",
                "status": "proxy",
                "gate": False,
                "fields": [
                    "industry_profit_yoy_median",
                    "revenue_cagr_3y",
                    "net_profit_cagr_3y",
                ],
                "note": (
                    "由行业内公司归母净利同比中位数自建，不是行业景气预测；"
                    "行业标签只有当前值，没有时点历史。企业自身 3 年营收/净利复合"
                    "增速作为行业空间的代理。"
                ),
            },
            {
                "key": "sustained_profitability",
                "label": "企业是否具有持续盈利能力",
                "status": "covered",
                "gate": params.min_roe_3y is not None,
                "fields": [
                    "roe_3y",
                    "net_profit_3y",
                    "cash_conversion_3y",
                    "ttm_net_profit",
                    "net_profit_decline_streak",
                ],
                "note": (
                    "3 年年化 ROE（累计归母净利 / 平均净资产 / 3）+ 三年累计净利为正"
                    "+ 三年经营现金流转化率 + 净利连跌年数（价值陷阱信号）。"
                    + (
                        f"硬闸门：3 年年化 ROE >= {params.min_roe_3y}"
                        f"（或走成长路径：3 年营收复合增速 >= "
                        f"{params.min_revenue_cagr_3y} 且净利复合增速 >= "
                        f"{params.min_net_profit_cagr_3y}）。"
                        if params.min_roe_3y is not None
                        and params.min_revenue_cagr_3y is not None
                        else (
                            f"硬闸门：3 年年化 ROE >= {params.min_roe_3y}。"
                            if params.min_roe_3y is not None
                            else "本版本未设 ROE 硬闸门（只筛价格与最低盈利质量）。"
                        )
                    )
                ),
            },
            {
                "key": "value_trap_avoidance",
                "label": "是不是价值陷阱",
                "status": "covered",
                "gate": params.decline_streak_years is not None,
                "fields": [
                    "revenue_decline_streak",
                    "net_profit_decline_streak",
                    "debt_ratio_change_3y",
                    "revenue_cagr_3y",
                ],
                "note": (
                    "低 PE 不等于便宜：营收连跌、净利连跌、资产负债率上行三者"
                    "任一命中即判为价值陷阱（已知不合格，非证据缺失）。"
                    "陷阱闸门优先于成长路径——营收涨而利润跌、杠杆上升的"
                    "公司不是成长故事，是带好标题的陷阱。"
                ),
            },
            {
                "key": "management_integrity",
                "label": "管理层是否诚信、优秀",
                "status": "proxy",
                "gate": False,
                "fields": ["dividend_years_5y"],
                "note": (
                    "无董监高履历、薪酬、股权质押等数据源。用近 5 年分红连续性作为"
                    "股东回报与治理的弱代理；财报修订次数不可用——库内 152,921 行中"
                    "仅 173 行是真修订，其余是年报/季报两种口径对同一报告期的重复入库，"
                    "拿它当治理信号是假的。这不等于对管理层的评价。"
                ),
            },
            {
                "key": "decade_of_advantage",
                "label": "未来十年是否仍然有竞争优势",
                "status": "unavailable",
                "gate": False,
                "fields": [],
                "note": (
                    "财报最早只到 2022 年报，不足以构建十年序列；护城河也没有"
                    "量化接口。该维度只能由人工长期跟踪。"
                ),
            },
        ],
        "gate_enabled": params.min_roe_3y is not None,
        "min_roe_3y": params.min_roe_3y,
        "growth_path_enabled": params.min_revenue_cagr_3y is not None,
        "value_trap_gate_enabled": params.decline_streak_years is not None,
        "disclaimer": (
            "质地层只覆盖六个维度中的部分：'unavailable' 的维度没有任何数据支撑，"
            "'proxy' 的维度是替代指标而非该问题本身。通过全部硬闸门只说明"
            "「价格已进入安全边际区且可量化的质地指标达标」，不构成对企业质地的结论。"
        ),
    }


def evaluate(
    evidence: ValueInvestingEvidence,
    params: ValueInvestingParams,
) -> dict:
    """Judge one security and return the full audit trail of that judgement."""
    reasons: list[str] = []
    blocked: list[str] = []
    metrics: dict[str, object] = {
        "close": evidence.close,
        "listed_days": evidence.listed_days,
        "amount_20d": evidence.amount_20d,
        "ttm_period_end": (
            evidence.ttm_period_end.isoformat() if evidence.ttm_period_end else None
        ),
        "equity_period_end": (
            evidence.equity_period_end.isoformat()
            if evidence.equity_period_end
            else None
        ),
        "vendor_pe_ttm": evidence.vendor_pe_ttm,
        "vendor_pb_mrq": evidence.vendor_pb_mrq,
        "net_profit_3y": evidence.net_profit_3y,
        "ocf_3y": evidence.ocf_3y,
        "ttm_net_profit": evidence.ttm_net_profit,
        "equity": evidence.equity,
        "total_shares": evidence.total_shares,
    }
    valuation: dict[str, object] = {}

    #: A known-but-unusable input (a loss-making quarter, negative equity)
    #: means the multiple is undefined by construction.  That is an
    #: exclusion, never "missing evidence", so the row must not be blocked.
    earnings_invalid = (
        evidence.ttm_net_profit is not None and evidence.ttm_net_profit <= 0
    )
    equity_invalid = evidence.equity is not None and evidence.equity <= 0

    # ---------------------------------------------------------- admission
    if evidence.listed_days is None:
        blocked.append("listing_date_unverified")
    elif evidence.listed_days < params.min_listed_days:
        reasons.append("listed_days_insufficient")
    if evidence.close is None:
        blocked.append("trusted_quote_missing")
    elif not _positive(evidence.close):
        blocked.append("close_not_positive")
    if evidence.is_suspended is None:
        blocked.append("suspension_state_unknown")
    elif evidence.is_suspended:
        reasons.append("suspended")
    treatment = (evidence.special_treatment_status or "").strip().lower()
    if not treatment:
        blocked.append("special_treatment_unknown")
    elif treatment not in {"normal", "none"}:
        reasons.append("special_treatment_restricted")
    if not evidence.industry:
        blocked.append("industry_missing")
    if evidence.is_financial_sector:
        reasons.append("financial_industry_separate_model")
    if evidence.is_cyclic_industry:
        reasons.append("cyclic_industry_separate_model")
    if evidence.amount_20d is None:
        blocked.append("liquidity_window_missing")
    elif evidence.amount_20d < params.min_amount_20d:
        reasons.append("liquidity_insufficient")

    # ------------------------------------------------------------ quality
    # The three-year gates only mean something on a complete three-year
    # window.  Summing whatever happens to be present silently converts a
    # three-year test into a two-year one, so an incomplete window blocks
    # the row rather than passing it on shorter evidence.
    if evidence.annual_periods_present is not None:
        metrics["annual_periods_present"] = evidence.annual_periods_present
        if evidence.annual_periods_present < 3:
            blocked.append("three_year_window_incomplete")
    if evidence.ttm_net_profit is None:
        blocked.append("ttm_net_profit_missing")
    elif evidence.ttm_net_profit <= 0:
        reasons.append("ttm_net_profit_not_positive")
    if evidence.net_profit_3y is None:
        blocked.append("three_year_net_profit_missing")
    elif evidence.net_profit_3y <= 0:
        reasons.append("three_year_net_profit_not_positive")
    if evidence.ocf_3y is None:
        blocked.append("three_year_operating_cash_flow_missing")
    elif evidence.net_profit_3y and evidence.net_profit_3y > 0:
        conversion = evidence.ocf_3y / evidence.net_profit_3y
        metrics["cash_conversion_3y"] = conversion
        if conversion < params.min_cash_conversion_3y:
            reasons.append("cash_conversion_weak")

    # ------------------------------------------------- business quality gate
    # The price gate alone finds *cheap* securities.  A business that cannot
    # earn its cost of capital stays cheap forever, and buying it is a value
    # trap rather than a margin of safety.  When the versioned definition
    # carries a ``min_roe_3y`` threshold, a low return on equity is a known
    # disqualification (excluded), while an unknown one blocks the row.
    quality: dict[str, object] = {
        "roe_3y": evidence.roe_3y,
        "gross_margin": evidence.gross_margin,
        "debt_ratio": evidence.debt_ratio,
        "industry_roe_median": evidence.industry_roe_median,
        "industry_profit_yoy_median": evidence.industry_profit_yoy_median,
        "dividend_years_5y": evidence.dividend_years_5y,
    }
    metrics["quality"] = quality

    # ------------------------------------------------------- growth path
    # Value investing and growth investing are not opposites: a business
    # that compounds fast has not yet earned a high return on the equity it
    # has accumulated, and requiring a mature return on equity would reject
    # exactly the businesses worth owning.  When the definition carries a
    # growth threshold, such a business qualifies through the growth path
    # instead of the return-on-equity path.  It still has to clear the
    # price gate, so this never buys an expensive security.
    growth_qualifies = False
    if (
        params.min_revenue_cagr_3y is not None
        and params.min_net_profit_cagr_3y is not None
    ):
        metrics["min_revenue_cagr_3y"] = params.min_revenue_cagr_3y
        metrics["min_net_profit_cagr_3y"] = params.min_net_profit_cagr_3y
        revenue_cagr = evidence.revenue_cagr_3y
        profit_cagr = evidence.net_profit_cagr_3y
        if revenue_cagr is None or profit_cagr is None:
            # A negative or missing base makes the growth rate undefined.
            # The business may still qualify on the return-on-equity path;
            # only the growth path is unavailable.
            metrics["growth_path"] = "unavailable"
        elif (
            revenue_cagr >= params.min_revenue_cagr_3y
            and profit_cagr >= params.min_net_profit_cagr_3y
        ):
            growth_qualifies = True
            metrics["growth_path"] = "qualified"
        else:
            metrics["growth_path"] = "not_qualified"

    if params.min_roe_3y is not None:
        metrics["min_roe_3y"] = params.min_roe_3y
        if evidence.roe_3y is None:
            # A negative equity base makes the ratio undefined by
            # construction; that case is already an exclusion, not a gap in
            # the evidence, so it must not be blocked as well.
            if not equity_invalid and not growth_qualifies:
                blocked.append("roe_3y_missing")
        elif evidence.roe_3y < params.min_roe_3y and not growth_qualifies:
            reasons.append("roe_3y_below_threshold")

    # ------------------------------------------------------ value-trap gate
    # A low multiple is only cheap if the business behind it is still worth
    # owning.  A company whose profit keeps falling, whose revenue keeps
    # shrinking, or whose balance sheet keeps levering up is losing value
    # rather than being mispriced -- the classic value trap.  These are
    # known disqualifications (excluded), never missing evidence: the
    # trend is fully observed once the window is complete.
    #
    # The trap gate outranks the growth path: a business that is growing
    # revenue while its profit falls and its leverage climbs is not a
    # growth story, it is a trap with a good headline.
    trend: dict[str, object] = {
        "revenue_cagr_3y": evidence.revenue_cagr_3y,
        "net_profit_cagr_3y": evidence.net_profit_cagr_3y,
        "revenue_decline_streak": evidence.revenue_decline_streak,
        "net_profit_decline_streak": evidence.net_profit_decline_streak,
        "debt_ratio_change_3y": evidence.debt_ratio_change_3y,
    }
    metrics["trend"] = trend

    if evidence.trend_periods_present is not None:
        metrics["trend_periods_present"] = evidence.trend_periods_present
        if evidence.trend_periods_present < 4:
            # The trend window needs four annual reports to observe three
            # year-on-year changes.  A shorter window would turn a
            # three-year test into a one- or two-year one.
            blocked.append("trend_window_incomplete")

    if params.decline_streak_years is not None:
        metrics["decline_streak_years"] = params.decline_streak_years
        streak = params.decline_streak_years
        if (
            evidence.net_profit_decline_streak is not None
            and evidence.net_profit_decline_streak >= streak
        ):
            reasons.append("net_profit_declining")
        if (
            evidence.revenue_decline_streak is not None
            and evidence.revenue_decline_streak >= streak
        ):
            reasons.append("revenue_declining")
    if params.max_debt_ratio_rise_3y is not None:
        metrics["max_debt_ratio_rise_3y"] = params.max_debt_ratio_rise_3y
        if (
            evidence.debt_ratio_change_3y is not None
            and evidence.debt_ratio_change_3y > params.max_debt_ratio_rise_3y
        ):
            reasons.append("leverage_rising")

    # ---------------------------------------------------------- valuation
    if evidence.total_shares is None:
        blocked.append("total_shares_missing")
    elif evidence.total_shares <= 0:
        blocked.append("total_shares_not_positive")
    if evidence.equity is None:
        blocked.append("shareholders_equity_missing")
    elif evidence.equity <= 0:
        reasons.append("shareholders_equity_not_positive")

    eps_norm: Decimal | None = None
    bps: Decimal | None = None
    pe_current: Decimal | None = None
    pb_current: Decimal | None = None
    if _positive(evidence.total_shares):
        if evidence.ttm_net_profit is not None:
            eps_norm = evidence.ttm_net_profit / evidence.total_shares
            metrics["eps_norm"] = eps_norm
        if evidence.equity is not None and evidence.equity > 0:
            bps = evidence.equity / evidence.total_shares
            metrics["bps"] = bps
    if _positive(evidence.close):
        if eps_norm is not None and eps_norm > 0:
            pe_current = evidence.close / eps_norm
            metrics["pe_current"] = pe_current
        if bps is not None and bps > 0:
            pb_current = evidence.close / bps
            metrics["pb_current"] = pb_current

    if pe_current is None:
        # A loss-making company has no PE *by construction*; that is a known
        # exclusion, not missing evidence.  Only an unknown input blocks.
        if earnings_invalid:
            pass
        else:
            blocked.append("current_pe_unavailable")
    elif pe_current <= 0:
        reasons.append("current_pe_not_positive")
    elif pe_current > params.max_pe:
        reasons.append("pe_above_ceiling")
    if pb_current is None:
        if equity_invalid:
            pass
        else:
            blocked.append("current_pb_unavailable")
    elif pb_current <= 0:
        reasons.append("current_pb_not_positive")
    elif pb_current > params.max_pb:
        reasons.append("pb_above_ceiling")

    pe_observations = max(len(evidence.pe_history), evidence.pe_observations)
    pb_observations = max(len(evidence.pb_history), evidence.pb_observations)
    if pe_observations < params.min_history_observations:
        blocked.append("pe_history_insufficient")
    if pb_observations < params.min_history_observations:
        blocked.append("pb_history_insufficient")

    # ------------------------------------------------- margin of safety
    pe_fair = evidence.pe_fair
    if pe_fair is None:
        pe_fair = conservative_quantile(evidence.pe_history, params.anchor_quantile)
    pb_fair = evidence.pb_fair
    if pb_fair is None:
        pb_fair = conservative_quantile(evidence.pb_history, params.anchor_quantile)
    if pe_fair is not None and pe_fair <= 0:
        blocked.append("pe_fair_anchor_not_positive")
        pe_fair = None
    if pb_fair is not None and pb_fair <= 0:
        blocked.append("pb_fair_anchor_not_positive")
        pb_fair = None
    valuation.update(
        {
            "anchor_quantile": params.anchor_quantile,
            "pe_fair": pe_fair,
            "pb_fair": pb_fair,
            "pe_history_observations": pe_observations,
            "pb_history_observations": pb_observations,
        }
    )
    if pe_fair is None:
        blocked.append("pe_fair_anchor_unavailable")
    if pb_fair is None:
        blocked.append("pb_fair_anchor_unavailable")

    v_earnings: Decimal | None = None
    v_book: Decimal | None = None
    v_low: Decimal | None = None
    if pe_fair is not None and eps_norm is not None and eps_norm > 0:
        v_earnings = eps_norm * pe_fair
        valuation["v_earnings"] = v_earnings
    if pb_fair is not None and bps is not None and bps > 0:
        v_book = bps * pb_fair
        valuation["v_book"] = v_book
    candidates = [value for value in (v_earnings, v_book) if value is not None]
    if not candidates:
        if not (earnings_invalid or equity_invalid):
            blocked.append("intrinsic_value_unavailable")
    else:
        # The smaller anchor wins: a stock whose earnings collapsed must be
        # valued on its balance sheet, not on a historical multiple that was
        # inflated by the very collapse.
        v_low = min(candidates)
        valuation["v_low"] = v_low

    buy_ceiling: Decimal | None = None
    price_condition_met = False
    if v_low is not None:
        buy_ceiling = v_low * params.price_factor
        valuation["buy_cost_ceiling"] = buy_ceiling
        valuation["required_margin"] = params.required_margin
        if evidence.close is not None and evidence.close > 0:
            gross_margin = Decimal(1) - evidence.close / v_low
            valuation["gross_margin"] = gross_margin
            valuation["price_gap"] = v_low / evidence.close - Decimal(1)
            price_condition_met = evidence.close <= buy_ceiling
            if not price_condition_met:
                reasons.append("margin_of_safety_not_met")

    status = STATUS_QUALIFIED
    if blocked:
        status = STATUS_BLOCKED
    elif reasons:
        status = STATUS_EXCLUDED

    return {
        "symbol": evidence.symbol,
        "name": evidence.name,
        "industry": evidence.industry,
        "status": status,
        "reason_codes": reasons + blocked,
        "excluded_reasons": reasons,
        "blocking_reasons": blocked,
        "metrics": metrics,
        "valuation": valuation,
        "price_condition_met": price_condition_met,
        "notes": list(evidence.notes),
    }


#: Reason codes that belong to the business-quality gate and nothing else.
#: Stripping them reconstructs the price-only screen, which is the first
#: published version of this strategy.
QUALITY_GATE_REASON_CODES = frozenset({"roe_3y_below_threshold", "roe_3y_missing"})

#: Reason codes that belong to the value-trap gate.  They are reported
#: separately from the quality gate because they answer a different
#: question: not "is this a good business" but "is the cheapness real".
VALUE_TRAP_REASON_CODES = frozenset(
    {
        "net_profit_declining",
        "revenue_declining",
        "leverage_rising",
        "trend_window_incomplete",
    }
)


def _without_quality_gate(rows: list[dict]) -> list[dict]:
    """Return the rows as they would look with the quality gate disabled.

    The business-quality gate is the only difference between the two
    published versions, so removing its reason codes and re-deriving the
    status reproduces the earlier screen exactly -- no second pass over the
    database and no chance of the two versions drifting apart.

    The value-trap gate is *not* stripped: a falling business is a trap
    under every version of this strategy, so the price-only comparison keeps
    it.
    """
    rebuilt: list[dict] = []
    for row in rows:
        excluded = [
            code for code in row["excluded_reasons"]
            if code not in QUALITY_GATE_REASON_CODES
        ]
        blocked = [
            code for code in row["blocking_reasons"]
            if code not in QUALITY_GATE_REASON_CODES
        ]
        if blocked:
            status = STATUS_BLOCKED
        elif excluded:
            status = STATUS_EXCLUDED
        else:
            status = STATUS_QUALIFIED
        rebuilt.append(
            {
                **row,
                "status": status,
                "excluded_reasons": excluded,
                "blocking_reasons": blocked,
                "reason_codes": excluded + blocked,
            }
        )
    return rebuilt


def screen(
    evidence: Sequence[ValueInvestingEvidence],
    params: ValueInvestingParams,
) -> dict:
    """Run the four-layer funnel and return the full screening report.

    The result carries two pools:

    * ``qualified`` -- every hard gate passed, including the price condition.
      This is the pool of securities that already carry the required margin
      of safety.
    * ``shortlist`` -- the research workload taken from the qualified pool:
      at most ``top_n`` names and at most ``max_per_industry`` per industry.

    Neither pool is padded.  Fewer than ``top_n`` survivors means the
    requirements were not met, and the report says so instead of relaxing
    them.
    """
    rows = [evaluate(item, params) for item in evidence]
    qualified = [row for row in rows if row["status"] == STATUS_QUALIFIED]

    def _sort_key(row: dict) -> tuple:
        valuation = row["valuation"]
        gap = valuation.get("price_gap")
        margin = valuation.get("gross_margin")
        return (
            -(gap if isinstance(gap, Decimal) else Decimal(0)),
            -(margin if isinstance(margin, Decimal) else Decimal(0)),
            row["symbol"],
        )

    def _select(pool: list[dict]) -> tuple[list[dict], list[dict]]:
        """Take the research workload out of a qualified pool."""
        ordered = sorted(pool, key=_sort_key)
        shortlist: list[dict] = []
        deferred: list[dict] = []
        per_industry: dict[str, int] = {}
        for row in ordered:
            industry = row["industry"] or ""
            taken = per_industry.get(industry, 0)
            if len(shortlist) < params.top_n and taken < params.max_per_industry:
                row = {**row, "status": STATUS_SELECTED, "rank": len(shortlist) + 1}
                per_industry[industry] = taken + 1
                shortlist.append(row)
            else:
                row = {
                    **row,
                    "status": "qualified_deferred",
                    "rank": len(shortlist) + len(deferred) + 1,
                }
                deferred.append(row)
        return shortlist, deferred

    shortlist, deferred = _select(qualified)

    # The same screen with the business-quality gate switched off.  This is
    # the price gate alone, i.e. the first published version of the
    # strategy.  Reporting it makes the cost of the quality gate visible
    # instead of hiding it behind a shorter list.
    price_only_rows = _without_quality_gate(rows)
    price_only_qualified = [
        row for row in price_only_rows if row["status"] == STATUS_QUALIFIED
    ]
    price_only_shortlist, _ = _select(price_only_qualified)

    reason_counts: dict[str, int] = {}
    for row in rows:
        for code in row["reason_codes"]:
            reason_counts[code] = reason_counts.get(code, 0) + 1

    # How many securities the growth path actually rescued -- that is, the
    # ones that would have failed the return-on-equity gate and qualified on
    # growth instead.  Counting every security that merely meets the growth
    # thresholds would overstate the path's effect: a business with a high
    # return on equity qualified on that ground anyway.
    growth_path_count = 0
    for row in rows:
        metrics = row["metrics"]
        if metrics.get("growth_path") != "qualified":
            continue
        roe = (metrics.get("quality") or {}).get("roe_3y")
        threshold = params.min_roe_3y
        if threshold is None:
            continue
        if roe is None or roe < threshold:
            growth_path_count += 1
    trap_removed = [
        row["symbol"]
        for row in rows
        if set(row["excluded_reasons"]) & VALUE_TRAP_REASON_CODES
    ]

    with localcontext() as context:
        context.prec = 34
        summary = {
            "strategy": VERSION,
            # The kernel version is what lets a stored report be traced back
            # to the exact rules that produced it.  It is carried in the
            # report itself rather than derived by the caller.
            "kernel_version": KERNEL_VERSION,
            "required_margin": params.required_margin,
            "buy_cost_ceiling_factor": params.price_factor,
            "universe_count": len(rows),
            "qualified_count": len(qualified),
            "shortlist_count": len(shortlist),
            "deferred_count": len(deferred),
            "blocked_count": sum(1 for row in rows if row["status"] == STATUS_BLOCKED),
            "excluded_count": sum(1 for row in rows if row["status"] == STATUS_EXCLUDED),
            "reason_counts": dict(sorted(reason_counts.items())),
            "research_only": True,
            "pit_verified": False,
            "execution_eligible": False,
        }

    return {
        **summary,
        "params": {
            "required_margin": params.required_margin,
            "max_pe": params.max_pe,
            "max_pb": params.max_pb,
            "min_listed_days": params.min_listed_days,
            "min_amount_20d": params.min_amount_20d,
            "min_history_observations": params.min_history_observations,
            "min_cash_conversion_3y": params.min_cash_conversion_3y,
            "min_roe_3y": params.min_roe_3y,
            "min_revenue_cagr_3y": params.min_revenue_cagr_3y,
            "min_net_profit_cagr_3y": params.min_net_profit_cagr_3y,
            "decline_streak_years": params.decline_streak_years,
            "max_debt_ratio_rise_3y": params.max_debt_ratio_rise_3y,
            "anchor_quantile": params.anchor_quantile,
            "top_n": params.top_n,
            "max_per_industry": params.max_per_industry,
        },
        "quality_profile": quality_profile(params),
        "rows": rows,
        "qualified_symbols": [row["symbol"] for row in qualified],
        "shortlist": shortlist,
        "deferred": deferred,
        "growth_path_count": growth_path_count,
        "value_trap_removed_count": len(trap_removed),
        "value_trap_removed_symbols": trap_removed,
        "price_gate_only": {
            "note": (
                "同一份证据下关闭企业质地闸门的结果（等价于 value_investing_margin@v1），"
                "用于显示质地闸门到底剔除了谁"
            ),
            "qualified_count": len(price_only_qualified),
            "qualified_symbols": [row["symbol"] for row in price_only_qualified],
            "shortlist": price_only_shortlist,
            "removed_by_quality_gate": [
                row["symbol"]
                for row in price_only_qualified
                if row["symbol"] not in {item["symbol"] for item in qualified}
            ],
        },
    }
