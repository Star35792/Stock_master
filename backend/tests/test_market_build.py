from datetime import date
from decimal import Decimal
from types import SimpleNamespace

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.data_sources.hithink import HithinkFinanceDataSource
from app.models.market import DailyBar
from app.repositories.market import DailyBarRepository
from app.services.daily_bar_market_builder import DailyBarMarketBuildService, _validate_candidate


def _raw_bar(**overrides):
    values = {
        "id": 1,
        "symbol": "600519.SH",
        "trade_date": date(2026, 7, 17),
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
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class FakeDb:
    def __init__(self) -> None:
        self.committed = False

    def commit(self) -> None:
        self.committed = True


class FakeRawRepository:
    def __init__(self, rows: list[SimpleNamespace], selected: SimpleNamespace) -> None:
        self.rows = rows
        self.selected = selected

    def latest_candidates(self, db: object, **kwargs: object) -> list[SimpleNamespace]:
        return [self.selected]

    def latest_candidates_by_source(
        self, db: object, **kwargs: object
    ) -> dict[tuple[str, date, str], list[SimpleNamespace]]:
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


def test_hithink_security_mapping() -> None:
    record = HithinkFinanceDataSource._to_security(
        {
            "thscode": "600519.SH",
            "ticker": "600519",
            "name": "贵州茅台",
            "exchange": "SH",
            "asset_type": "a-share",
            "currency": "CNY",
        }
    )

    assert record.symbol == "600519.SH"
    assert record.raw_symbol == "600519"
    assert record.name == "贵州茅台"
    assert record.security_type == "stock"
    assert record.exchange == "SH"


def test_market_build_validation_accepts_clean_raw_bar() -> None:
    issues = _validate_candidate(
        _raw_bar(),
        existing_symbols={"600519.SH"},
        open_dates_by_exchange={"SH": {date(2026, 7, 17)}},
    )

    assert issues == []


def test_market_build_validation_rejects_missing_security_and_bad_price() -> None:
    issues = _validate_candidate(
        _raw_bar(high=Decimal("1180.00")),
        existing_symbols=set(),
        open_dates_by_exchange={"SH": {date(2026, 7, 17)}},
    )

    assert {issue["issue_type"] for issue in issues} == {
        "SECURITY_NOT_FOUND",
        "PRICE_INVALID",
    }


def test_market_build_promotes_consistent_multi_source_candidate() -> None:
    hithink = _raw_bar(id=1, source_name="hithink_finance")
    csv = _raw_bar(id=2, source_name="csv")
    db = FakeDb()
    market_repository = FakeMarketRepository()
    compare_repository = FakeCompareRepository()
    issue_repository = FakeIssueRepository()
    service = DailyBarMarketBuildService(
        raw_repository=FakeRawRepository([hithink, csv], hithink),  # type: ignore[arg-type]
        market_repository=market_repository,  # type: ignore[arg-type]
        compare_repository=compare_repository,  # type: ignore[arg-type]
        security_repository=FakeSecurityRepository(),  # type: ignore[arg-type]
        trading_calendar_repository=FakeTradingCalendarRepository(),  # type: ignore[arg-type]
        issue_repository=issue_repository,  # type: ignore[arg-type]
    )

    result = service.build_from_raw(db)  # type: ignore[arg-type]

    assert result.trusted_count == 1
    assert result.conflict_count == 0
    assert market_repository.calls[0]["source_count"] == 2
    assert market_repository.calls[0]["quality_score"] == Decimal("100.00")
    assert market_repository.calls[0]["quality_status"] == "trusted"
    assert market_repository.calls[0]["is_suspect"] is False
    assert compare_repository.calls[0]["is_conflict"] is False
    assert issue_repository.calls == []
    assert db.committed is True


def test_market_repository_does_not_overwrite_manual_fix() -> None:
    engine = create_engine("duckdb:///:memory:")
    with engine.begin() as connection:
        connection.execute(text("CREATE SCHEMA market"))
    DailyBar.__table__.create(engine)

    with Session(engine) as db:
        existing = DailyBar(
            symbol="600519.SH",
            security_type="stock",
            trade_date=date(2026, 7, 17),
            adjust_type="none",
            open=Decimal("1200"),
            high=Decimal("1250"),
            low=Decimal("1190"),
            close=Decimal("1243"),
            volume=Decimal("1000"),
            amount=Decimal("1243000"),
            source_name="manual",
            source_count=1,
            quality_score=Decimal("100"),
            quality_status="manual_fixed",
            is_suspect=False,
            selected_raw_id=7,
        )
        db.add(existing)
        db.commit()

        DailyBarRepository().upsert_from_raw(
            db,
            raw_bar=_raw_bar(id=99, close=Decimal("1242")),  # type: ignore[arg-type]
            quality_score=Decimal("100"),
            quality_status="trusted",
            is_suspect=False,
        )
        db.commit()
        db.refresh(existing)

        assert existing.close == Decimal("1243.0000")
        assert existing.quality_status == "manual_fixed"
        assert existing.selected_raw_id == 7


def test_market_build_blocks_price_conflict_between_sources() -> None:
    hithink = _raw_bar(id=1, source_name="hithink_finance")
    csv = _raw_bar(id=2, source_name="csv", close=Decimal("1243.00"))
    db = FakeDb()
    market_repository = FakeMarketRepository()
    compare_repository = FakeCompareRepository()
    issue_repository = FakeIssueRepository()
    service = DailyBarMarketBuildService(
        raw_repository=FakeRawRepository([hithink, csv], hithink),  # type: ignore[arg-type]
        market_repository=market_repository,  # type: ignore[arg-type]
        compare_repository=compare_repository,  # type: ignore[arg-type]
        security_repository=FakeSecurityRepository(),  # type: ignore[arg-type]
        trading_calendar_repository=FakeTradingCalendarRepository(),  # type: ignore[arg-type]
        issue_repository=issue_repository,  # type: ignore[arg-type]
    )

    result = service.build_from_raw(db)  # type: ignore[arg-type]

    assert result.trusted_count == 0
    assert result.rejected_count == 1
    assert result.issue_count == 1
    assert result.conflict_count == 1
    assert market_repository.calls == []
    assert compare_repository.calls[0]["is_conflict"] is True
    assert issue_repository.calls[0]["issue_type"] == "SOURCE_CONFLICT"
    assert db.committed is True


def test_market_build_keeps_warning_candidate_as_suspect() -> None:
    row = _raw_bar(
        open=Decimal("140.00"),
        high=Decimal("155.00"),
        low=Decimal("139.00"),
        close=Decimal("150.00"),
        pre_close=Decimal("100.00"),
        change_amount=Decimal("50.00"),
        amount=Decimal("0"),
    )
    db = FakeDb()
    market_repository = FakeMarketRepository()
    issue_repository = FakeIssueRepository()
    service = DailyBarMarketBuildService(
        raw_repository=FakeRawRepository([row], row),  # type: ignore[arg-type]
        market_repository=market_repository,  # type: ignore[arg-type]
        security_repository=FakeSecurityRepository(),  # type: ignore[arg-type]
        trading_calendar_repository=FakeTradingCalendarRepository(),  # type: ignore[arg-type]
        issue_repository=issue_repository,  # type: ignore[arg-type]
    )

    result = service.build_from_raw(db)  # type: ignore[arg-type]

    assert result.trusted_count == 0
    assert result.suspect_count == 1
    assert result.rejected_count == 0
    assert result.issue_count == 2
    assert market_repository.calls[0]["quality_score"] == Decimal("60.00")
    assert market_repository.calls[0]["quality_status"] == "suspect"
    assert market_repository.calls[0]["is_suspect"] is True
    assert {issue["issue_type"] for issue in issue_repository.calls} == {
        "PRICE_MOVE_EXTREME",
        "TURNOVER_INCONSISTENT",
    }
    assert db.committed is True


def test_market_build_rejects_suspended_raw_bar() -> None:
    row = _raw_bar(is_suspended=True)
    db = FakeDb()
    market_repository = FakeMarketRepository()
    issue_repository = FakeIssueRepository()
    service = DailyBarMarketBuildService(
        raw_repository=FakeRawRepository([row], row),  # type: ignore[arg-type]
        market_repository=market_repository,  # type: ignore[arg-type]
        security_repository=FakeSecurityRepository(),  # type: ignore[arg-type]
        trading_calendar_repository=FakeTradingCalendarRepository(),  # type: ignore[arg-type]
        issue_repository=issue_repository,  # type: ignore[arg-type]
    )

    result = service.build_from_raw(db)  # type: ignore[arg-type]

    assert result.trusted_count == 0
    assert result.rejected_count == 1
    assert result.issue_count == 1
    assert market_repository.calls == []
    assert issue_repository.calls[0]["issue_type"] == "SUSPENDED_BAR_REJECTED"
    assert db.committed is True
