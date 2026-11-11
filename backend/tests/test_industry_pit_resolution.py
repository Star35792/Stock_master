"""时点行业标签的回归测试。

这个文件盯的是**前视偏差**本身，而不是某个因子的数值。

背景
----
``market.security.industry`` 只存**当前**行业标签。历史因子/回测直接用它，
等于把今天的行业归属套到过去的截面上——一个 2024 年才被划进「银行Ⅱ」的
股票，在 2020 年的横截面里也会被当成银行股参与行业中性化。

修复路径是 ``market.security_industry_history``：申万归属变动表把每个标的
展开成真实的生效区间（实测 11,963 区间 / 5,585 标的，2,024 只历史上变更过
行业，最多 10 次），读取端按目标交易日做 as-of 解析。

这些测试钉住三件事：

1. as-of 解析本身正确（区间边界、开放区间、回退）；
2. 横截面行业中性化**按交易日**分组，不是按当前标签；
3. 估值因子「行业内分位」**按交易日**分组。
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.repositories.market.security_industry_history import (
    IndustryInterval,
    resolve_industries_by_date,
)

#: 一个在 2026-07-01 从「旧行业」换到「新行业」的标的。
_OLD = IndustryInterval(
    effective_from=date(2020, 1, 1),
    effective_to=date(2026, 7, 1),
    industry="旧行业",
)
_NEW = IndustryInterval(
    effective_from=date(2026, 7, 1),
    effective_to=None,
    industry="新行业",
)


# ---------------------------------------------------------------------- as-of


def test_as_of_picks_the_interval_effective_on_that_date() -> None:
    intervals = {"600519.SH": [_OLD, _NEW]}

    resolved = resolve_industries_by_date(
        intervals,
        [date(2020, 1, 1), date(2026, 6, 30), date(2026, 7, 1)],
    )

    assert resolved[date(2020, 1, 1)]["600519.SH"] == "旧行业"
    assert resolved[date(2026, 6, 30)]["600519.SH"] == "旧行业"
    # 边界日归新区间：effective_to 是排他的。
    assert resolved[date(2026, 7, 1)]["600519.SH"] == "新行业"


def test_as_of_excludes_dates_before_the_first_interval() -> None:
    intervals = {"600519.SH": [_NEW]}

    resolved = resolve_industries_by_date(intervals, [date(2019, 12, 31)])

    assert "600519.SH" not in resolved[date(2019, 12, 31)]


def test_as_of_falls_back_to_current_label_only_when_no_interval_covers() -> None:
    intervals = {"600519.SH": [_NEW]}
    fallback = {"600519.SH": "当前标签", "000001.SZ": "当前标签2"}

    resolved = resolve_industries_by_date(
        intervals,
        [date(2019, 12, 31), date(2026, 8, 1)],
        fallback=fallback,
    )

    # 2019 年没有区间覆盖 → 回退到当前标签（显式行为，不是默认行为）。
    assert resolved[date(2019, 12, 31)]["600519.SH"] == "当前标签"
    # 2026 年有区间 → 时点事实优先于回退。
    assert resolved[date(2026, 8, 1)]["600519.SH"] == "新行业"
    # 没有区间的标的直接用回退。
    assert resolved[date(2026, 8, 1)]["000001.SZ"] == "当前标签2"


def test_as_of_without_fallback_omits_uncovered_symbols() -> None:
    resolved = resolve_industries_by_date({}, [date(2026, 8, 1)])

    assert resolved[date(2026, 8, 1)] == {}


def test_as_of_handles_multiple_intervals_per_symbol() -> None:
    intervals = {
        "A": [
            IndustryInterval(date(2020, 1, 1), date(2021, 1, 1), "I1"),
            IndustryInterval(date(2021, 1, 1), date(2022, 1, 1), "I2"),
            IndustryInterval(date(2022, 1, 1), None, "I3"),
        ]
    }

    resolved = resolve_industries_by_date(
        intervals,
        [date(2020, 6, 1), date(2021, 6, 1), date(2023, 6, 1)],
    )

    assert resolved[date(2020, 6, 1)]["A"] == "I1"
    assert resolved[date(2021, 6, 1)]["A"] == "I2"
    assert resolved[date(2023, 6, 1)]["A"] == "I3"


# ------------------------------------------------------- 横截面行业中性化 PIT


def _row_at(symbol: str, factor_name: str, value: str, trade_date: date):
    from datetime import UTC, datetime
    from types import SimpleNamespace

    return SimpleNamespace(
        symbol=symbol,
        trade_date=trade_date,
        factor_name=factor_name,
        factor_version="v1-none",
        value=Decimal(value),
        created_at=datetime(2026, 7, 17, tzinfo=UTC),
    )


def test_cross_section_industry_neutralization_follows_point_in_time_labels() -> None:
    """行业中性化必须按当日生效的行业分组。

    场景（标的选 A / D / C，因为测试替身给的当前标签是
    ``A→I1``、``B→I1``、``C→I2``、``D→I2``）：

    * 当前标签：A 单独一组（I1），D 与 C 同组（I2）；
    * 时点标签：2026-06-30 时 A 与 D 同属「旧行业」，C 属「另一行业」。

    两种分组结构不同 → 残差必然不同。若服务仍用当前标签，两次结果会完全一致，
    前视偏差就在这里。
    """

    from app.services import FactorCrossSectionService
    from tests.test_factor_cross_section import (
        FakeDailyBarRepository,
        FakeDefinitionRepository,
        FakeFactorValueRepository,
        FakeProfileRepository,
        FakeRunRepository,
        FakeSecurityIndustryHistoryRepository,
        FakeSecurityRepository,
        FakeTradingCalendarRepository,
    )

    trade_date = date(2026, 6, 30)
    source_rows = [
        _row_at("A", "ret_1d", "10", trade_date),
        _row_at("D", "ret_1d", "20", trade_date),
        _row_at("C", "ret_1d", "50", trade_date),
    ]

    def _build(history_repository):
        return FactorCrossSectionService(
            value_repository=FakeFactorValueRepository(source_rows, []),
            profile_repository=FakeProfileRepository(),
            definition_repository=FakeDefinitionRepository(),
            run_repository=FakeRunRepository(),
            daily_bar_repository=FakeDailyBarRepository(),
            security_repository=FakeSecurityRepository(),
            security_industry_history_repository=history_repository,
            trading_calendar_repository=FakeTradingCalendarRepository(),
        )

    def _run(service):
        result = service.run(
            object(),
            factor_name="ret_1d",
            factor_version="v1-none",
            start_date=trade_date,
            end_date=trade_date,
            adjust_type="none",
            code_version="abc123",
        )
        return result, {
            row["symbol"]: row["value"]
            for row in service.value_repository.values
            if row["factor_name"] == "ret_1d__industry_neutral"
        }

    current_result, current_values = _run(
        _build(FakeSecurityIndustryHistoryRepository())
    )
    pit_result, pit_values = _run(
        _build(
            FakeSecurityIndustryHistoryRepository(
                {
                    "A": [
                        IndustryInterval(date(2020, 1, 1), date(2026, 7, 1), "旧行业"),
                        IndustryInterval(date(2026, 7, 1), None, "新行业"),
                    ],
                    "D": [IndustryInterval(date(2020, 1, 1), None, "旧行业")],
                }
            )
        )
    )

    assert current_result.rows_by_transform["industry_neutral"] == 3
    assert pit_result.rows_by_transform["industry_neutral"] == 3
    assert current_values != pit_values
    # 当前标签：A 单独（I1），D 与 C 同组（I2）→ A 残差 0，D=-15，C=+15。
    assert current_values["A"] == Decimal("0")
    assert current_values["D"] == Decimal("-15")
    assert current_values["C"] == Decimal("15")
    # 时点标签：A 与 D 同组（均值 15），C 单独 → A=-5，D=+5，C=0。
    assert pit_values["A"] == Decimal("-5")
    assert pit_values["D"] == Decimal("5")
    assert pit_values["C"] == Decimal("0")


# ---------------------------------------------------------- 估值行业内分位 PIT


def test_valuation_industry_gap_uses_point_in_time_industry() -> None:
    """``industry_valuation_gap`` 的行业内分位必须按当日行业算。

    行业内分位 = 个股盈利收益率分位 − 同行业分位中位数。行业分组用错，
    中位数就错，因子值整体偏移。

    场景：当前标签下 A 属 I1、B 与 C 属 I2；时点标签下 A 与 B 同属「旧行业」、
    C 属「另一行业」。两种分组给出的组内中位数不同 → 因子值必须不同。
    """

    from app.services import ValuationFactorService
    from tests.test_valuation_factors import (
        FakeDailyBarRepository,
        FakeFactorDefinitionRepository,
        FakeFactorRunRepository,
        FakeFactorValueRepository,
        FakeFinancialStatementRepository,
        FakeSecurityIndustryHistoryRepository,
        FakeSecurityRepository,
        FakeValuationSnapshotRepository,
        _bar,
        _statement,
        _valuation,
    )

    trade_date = date(2020, 6, 30)
    symbols = ("600519.SH", "000001.SZ", "000002.SZ")

    def _build(history_repository, industries):
        service = ValuationFactorService(
            daily_bar_repository=FakeDailyBarRepository(
                [_bar(trade_date, symbol) for symbol in symbols]
            ),
            valuation_repository=FakeValuationSnapshotRepository(
                [
                    _valuation(trade_date, "1000", "600519.SH"),
                    _valuation(trade_date, "2000", "000001.SZ"),
                    _valuation(trade_date, "3000", "000002.SZ"),
                ]
            ),
            security_repository=FakeSecurityRepository(industries),
            security_industry_history_repository=history_repository,
            financial_statement_repository=FakeFinancialStatementRepository(
                [
                    _statement(
                        "income",
                        {
                            "operating_revenue": "1200",
                            "net_profit": "300",
                            "deducted_net_profit": "270",
                            "operating_profit": "500",
                        },
                        report_date=date(2020, 3, 31),
                        symbol=symbol,
                    )
                    for symbol in symbols
                ]
            ),
            definition_repository=FakeFactorDefinitionRepository(),
            run_repository=FakeFactorRunRepository(),
            value_repository=FakeFactorValueRepository(),
        )
        return service

    # 当前标签：A 单独一组，B 与 C 同组。
    current = _build(
        FakeSecurityIndustryHistoryRepository(),
        {"600519.SH": "I1", "000001.SZ": "I2", "000002.SZ": "I2"},
    )
    # 时点标签：A 与 B 同组，C 单独一组 —— 分组结构与当前标签不同。
    pit = _build(
        FakeSecurityIndustryHistoryRepository(
            {
                "600519.SH": [IndustryInterval(date(2019, 1, 1), None, "旧行业")],
                "000001.SZ": [IndustryInterval(date(2019, 1, 1), None, "旧行业")],
                "000002.SZ": [IndustryInterval(date(2019, 1, 1), None, "另一行业")],
            }
        ),
        {"600519.SH": "I1", "000001.SZ": "I2", "000002.SZ": "I2"},
    )

    def _gaps(service):
        service.run(
            object(),
            start_date=trade_date,
            end_date=trade_date,
            symbols=list(symbols),
            adjust_type="none",
            code_version="abc123",
        )
        return {
            (row["symbol"], row["trade_date"]): row["value"]
            for row in service.value_repository.values
            if row["factor_name"] == "industry_valuation_gap"
        }

    current_gaps = _gaps(current)
    pit_gaps = _gaps(pit)

    assert current_gaps, "当前标签场景应当产出 industry_valuation_gap"
    assert pit_gaps, "时点标签场景应当产出 industry_valuation_gap"
    # 分组结构不同 → 至少有一个标的的行业内分位不同。
    assert current_gaps != pit_gaps
