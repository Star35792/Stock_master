"""跨源对账被真正激活的回归测试。

背景：``market.daily_bar_source_compare`` 建好表却长期是 0 行，因为
``build_from_raw`` 把候选限定在单一来源（``source_name=``），比较集合里永远
只有一个源，对账无从发生。这些测试锁住"比较集合必须跨源"这个不变量。
"""

from datetime import date
from decimal import Decimal
from types import SimpleNamespace

from app.services.daily_bar_market_builder import DailyBarMarketBuildService


class FakeDb:
    def commit(self) -> None:
        pass


class FakeRawRepository:
    """记录 ``latest_candidates_by_source`` 收到的 ``source_name`` 参数。"""

    def __init__(self, rows: list[SimpleNamespace], selected: SimpleNamespace) -> None:
        self.rows = rows
        self.selected = selected
        self.group_source_name: str | None = "unset"
        self.candidate_source_name: str | None = "unset"

    def latest_candidates(self, db: object, **kwargs: object) -> list[SimpleNamespace]:
        self.candidate_source_name = kwargs.get("source_name")  # type: ignore[assignment]
        return [self.selected]

    def latest_candidates_by_source(
        self, db: object, **kwargs: object
    ) -> dict[tuple[str, date, str], list[SimpleNamespace]]:
        self.group_source_name = kwargs.get("source_name")  # type: ignore[assignment]
        return {
            (
                self.selected.symbol,
                self.selected.trade_date,
                self.selected.adjust_type,
            ): self.rows
        }


class FakeMarketRepository:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def upsert_from_raw(self, db: object, **kwargs: object) -> None:
        self.calls.append(kwargs)


class FakeSecurityRepository:
    def get_symbols(self, db: object, symbols: set[str]) -> set[str]:
        return symbols

    def get_special_treatment_statuses(
        self, db: object, symbols: set[str]
    ) -> dict[str, str]:
        return {}


class FakeTradingCalendarRepository:
    def get_open_dates(self, db: object, *, exchange: str, dates: set[date]) -> set[date]:
        return dates


class FakeCompareRepository:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def upsert_pair(self, db: object, **kwargs: object) -> None:
        self.calls.append(kwargs)


class FakeIssueRepository:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def create(self, db: object, **kwargs: object) -> None:
        self.calls.append(kwargs)


def _bar(**overrides) -> SimpleNamespace:
    values = {
        "id": 1,
        "symbol": "600519.SH",
        "trade_date": date(2026, 11, 10),
        "adjust_type": "none",
        "source_name": "hithink_finance",
        "security_type": "stock",
        "open": Decimal("1200.00"),
        "high": Decimal("1250.00"),
        "low": Decimal("1190.00"),
        "close": Decimal("1242.47"),
        "pre_close": Decimal("1240.00"),
        "change_amount": Decimal("2.47"),
        "pct_change": Decimal("0.199194"),
        "volume": Decimal("1000"),
        "amount": Decimal("1242470"),
        "turnover_rate": None,
        "amplitude": None,
        "limit_up": None,
        "limit_down": None,
        "is_suspended": False,
        "special_treatment_status": None,
        "imported_at": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _build(raw_repository: FakeRawRepository) -> tuple[object, FakeCompareRepository]:
    compare_repository = FakeCompareRepository()
    service = DailyBarMarketBuildService(
        raw_repository=raw_repository,  # type: ignore[arg-type]
        market_repository=FakeMarketRepository(),  # type: ignore[arg-type]
        compare_repository=compare_repository,  # type: ignore[arg-type]
        security_repository=FakeSecurityRepository(),  # type: ignore[arg-type]
        trading_calendar_repository=FakeTradingCalendarRepository(),  # type: ignore[arg-type]
        issue_repository=FakeIssueRepository(),  # type: ignore[arg-type]
    )
    return service.build_from_raw(FakeDb()), compare_repository  # type: ignore[arg-type]


def test_comparison_set_spans_all_sources_even_when_candidate_is_pinned() -> None:
    """候选仍由 source_name 限定，但**比较集合**必须跨全部来源。

    这是对账曾经休眠的根因：比较集合被同一个 source_name 收窄，于是永远只有
    一个源可比。
    """
    hithink = _bar(id=1, source_name="hithink_finance")
    tdx = _bar(id=2, source_name="tdx_official")
    raw_repository = FakeRawRepository([hithink, tdx], hithink)

    _build(raw_repository)

    assert raw_repository.candidate_source_name is None
    assert raw_repository.group_source_name is None


def test_two_consistent_sources_are_recorded_as_non_conflict() -> None:
    hithink = _bar(id=1, source_name="hithink_finance")
    tdx = _bar(id=2, source_name="tdx_official", close=Decimal("1242.4700"))
    raw_repository = FakeRawRepository([hithink, tdx], hithink)

    result, compare_repository = _build(raw_repository)

    assert result.conflict_count == 0  # type: ignore[attr-defined]
    assert len(compare_repository.calls) == 1
    assert compare_repository.calls[0]["is_conflict"] is False
    assert compare_repository.calls[0]["source_a"] == "hithink_finance"
    assert compare_repository.calls[0]["source_b"] == "tdx_official"


def test_volume_unit_difference_is_not_reported_as_conflict() -> None:
    """通达信指数成交量是「手」、项目是「股」—— 精确 100 倍不是冲突。"""
    index_prices = {
        "open": Decimal("3682.0369"),
        "high": Decimal("3683.7262"),
        "low": Decimal("3660.7167"),
        "close": Decimal("3663.7200"),
    }
    hithink = _bar(
        id=1,
        source_name="hithink_finance",
        security_type="index",
        volume=Decimal("40486517700"),
        **index_prices,
    )
    tdx = _bar(
        id=2,
        source_name="tdx_official",
        security_type="index",
        volume=Decimal("404865177"),
        **index_prices,
    )
    raw_repository = FakeRawRepository([hithink, tdx], hithink)

    result, compare_repository = _build(raw_repository)

    assert result.conflict_count == 0  # type: ignore[attr-defined]
    assert compare_repository.calls[0]["is_conflict"] is False


def test_real_amount_inconsistency_is_reported_as_conflict() -> None:
    """399002.SZ「深成指R」的成交额差 50.4% 必须被抓出来。"""
    hithink = _bar(
        id=1,
        source_name="hithink_finance",
        symbol="399002.SZ",
        security_type="index",
        open=Decimal("15286.7070"),
        high=Decimal("15286.7070"),
        low=Decimal("15286.7070"),
        close=Decimal("15286.7070"),
        volume=Decimal("34633771000"),
        amount=Decimal("412372110000"),
    )
    tdx = _bar(
        id=2,
        source_name="tdx_official",
        symbol="399002.SZ",
        security_type="index",
        open=Decimal("15286.7073"),
        high=Decimal("15286.7073"),
        low=Decimal("15286.7073"),
        close=Decimal("15286.7073"),
        volume=Decimal("526723497"),
        amount=Decimal("830819926016"),
    )
    raw_repository = FakeRawRepository([hithink, tdx], hithink)

    result, compare_repository = _build(raw_repository)

    assert result.conflict_count == 1  # type: ignore[attr-defined]
    assert compare_repository.calls[0]["is_conflict"] is True
    assert "amount differs" in (compare_repository.calls[0]["conflict_reason"] or "")
