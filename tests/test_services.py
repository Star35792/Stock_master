from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace

from app.services import MarketQueryService


class FakeDailyBarRepository:
    def list_by_symbol(self, db: object, **kwargs: object) -> tuple[list[SimpleNamespace], int]:
        assert kwargs["symbol"] == "600519.SH"
        row = SimpleNamespace(
            symbol="600519.SH",
            security_type="stock",
            trade_date=date(2026, 7, 16),
            adjust_type="none",
            open=Decimal("1500.0000"),
            high=Decimal("1510.0000"),
            low=Decimal("1490.0000"),
            close=Decimal("1505.0000"),
            pre_close=Decimal("1498.0000"),
            change_amount=Decimal("7.0000"),
            pct_change=Decimal("0.467290"),
            volume=Decimal("1000.0000"),
            amount=Decimal("1505000.0000"),
            turnover_rate=None,
            amplitude=None,
            source_name="csv",
            source_count=1,
            quality_score=Decimal("100.00"),
            quality_status="trusted",
            is_suspect=False,
            updated_at=datetime(2026, 7, 16, tzinfo=UTC),
        )
        return [row], 1

    def list_missing_by_calendar(
        self, db: object, **kwargs: object
    ) -> tuple[list[SimpleNamespace], int]:
        assert kwargs["symbol"] == "600519.SH"
        row = SimpleNamespace(
            symbol="600519.SH",
            name="贵州茅台",
            security_type="stock",
            exchange="SH",
            trade_date=date(2026, 7, 17),
            adjust_type="none",
        )
        return [row], 1


class FakeSourceCompareRepository:
    def __init__(self) -> None:
        self.kwargs: dict | None = None

    def list(self, db: object, **kwargs: object) -> tuple[list[SimpleNamespace], int]:
        self.kwargs = kwargs
        row = SimpleNamespace(
            id=1,
            symbol="600519.SH",
            trade_date=date(2026, 7, 16),
            adjust_type="none",
            source_a="csv",
            source_b="hithink_finance",
            raw_id_a=1,
            raw_id_b=2,
            close_diff=Decimal("0.000000"),
            close_diff_pct=Decimal("0.000000"),
            volume_diff_pct=None,
            amount_diff_pct=None,
            is_conflict=False,
            conflict_reason=None,
            created_at=datetime(2026, 7, 16, tzinfo=UTC),
        )
        return [row], 1


class FakeManualCorrectionRepository:
    def __init__(self) -> None:
        self.kwargs: dict | None = None

    def list(self, db: object, **kwargs: object) -> tuple[list[SimpleNamespace], int]:
        self.kwargs = kwargs
        row = SimpleNamespace(
            id=1,
            dataset="daily_bar",
            symbol="600519.SH",
            trade_date=date(2026, 7, 16),
            adjust_type="none",
            field_name="close",
            old_value="1505.0000",
            new_value="1506.0000",
            reason="manual source confirmed",
            corrected_by="liuzu",
            created_at=datetime(2026, 7, 16, tzinfo=UTC),
            applied_at=datetime(2026, 7, 16, tzinfo=UTC),
        )
        return [row], 1


class FakeCorporateActionRepository:
    def __init__(self) -> None:
        self.kwargs: dict | None = None

    def list(self, db: object, **kwargs: object) -> tuple[list[SimpleNamespace], int]:
        self.kwargs = kwargs
        row = SimpleNamespace(
            id=1,
            symbol="600519.SH",
            ex_date=date(2026, 7, 17),
            action_type="cash_dividend",
            cash_dividend=Decimal("10.000000"),
            bonus_share_ratio=None,
            transfer_share_ratio=None,
            rights_issue_ratio=None,
            rights_issue_price=None,
            split_ratio=None,
            record_date=date(2026, 7, 16),
            payment_date=date(2026, 7, 18),
            source_name="hithink_finance",
            source_event_id="event-1",
            note=None,
            created_at=datetime(2026, 7, 17, tzinfo=UTC),
            updated_at=datetime(2026, 7, 17, tzinfo=UTC),
        )
        return [row], 1


class FakeAdjustmentFactorRepository:
    def __init__(self) -> None:
        self.kwargs: dict | None = None

    def list(self, db: object, **kwargs: object) -> tuple[list[SimpleNamespace], int]:
        self.kwargs = kwargs
        row = SimpleNamespace(
            id=1,
            symbol="600519.SH",
            trade_date=date(2026, 7, 17),
            adjust_type="qfq",
            factor=Decimal("0.9123456789"),
            source_name="hithink_finance",
            version="v1",
            quality_status="trusted",
            created_at=datetime(2026, 7, 17, tzinfo=UTC),
            updated_at=datetime(2026, 7, 17, tzinfo=UTC),
        )
        return [row], 1


class FakeSecurityRepository:
    def __init__(self, security: SimpleNamespace | None = None) -> None:
        self.security = security
        self.symbol: str | None = None

    def get_by_symbol(self, db: object, *, symbol: str) -> SimpleNamespace | None:
        self.symbol = symbol
        return self.security


class FakeTradingCalendarRepository:
    def __init__(self, calendar: SimpleNamespace | None = None) -> None:
        self.calendar = calendar
        self.kwargs: dict | None = None

    def list(self, db: object, **kwargs: object) -> tuple[list[SimpleNamespace], int]:
        self.kwargs = kwargs
        row = SimpleNamespace(
            exchange="SH",
            trade_date=date(2026, 7, 17),
            is_open=True,
            previous_trade_date=date(2026, 7, 16),
            next_trade_date=date(2026, 7, 20),
            is_half_day=False,
            source_name="hithink_finance",
            updated_at=datetime(2026, 7, 17, tzinfo=UTC),
        )
        return [row], 1

    def get_by_exchange_date(
        self,
        db: object,
        *,
        exchange: str,
        trade_date: date,
    ) -> SimpleNamespace | None:
        self.kwargs = {"exchange": exchange, "trade_date": trade_date}
        return self.calendar


def test_market_service_normalizes_symbol() -> None:
    service = MarketQueryService(daily_bar_repository=FakeDailyBarRepository())

    result = service.list_daily_bars(
        object(),
        symbol="600519.sh",
        adjust_type="none",
        start_date=None,
        end_date=None,
        quality_status="trusted",
        limit=100,
        offset=0,
    )

    assert result.total == 1
    assert result.items[0].symbol == "600519.SH"


def test_market_service_lists_missing_daily_bars() -> None:
    service = MarketQueryService(daily_bar_repository=FakeDailyBarRepository())

    result = service.list_missing_daily_bars(
        object(),
        start_date=date(2026, 7, 17),
        end_date=date(2026, 7, 17),
        adjust_type="none",
        symbol="600519.sh",
        security_type="stock",
        exchange="SH",
        limit=100,
        offset=0,
    )

    assert result.total == 1
    assert result.items[0].symbol == "600519.SH"
    assert result.items[0].trade_date == date(2026, 7, 17)


def test_market_service_lists_source_compares() -> None:
    repository = FakeSourceCompareRepository()
    service = MarketQueryService(source_compare_repository=repository)

    result = service.list_source_compares(
        object(),
        symbol="600519.sh",
        start_date=None,
        end_date=None,
        adjust_type="none",
        is_conflict=False,
        limit=100,
        offset=0,
    )

    assert result.total == 1
    assert result.items[0].source_a == "csv"
    assert repository.kwargs is not None
    assert repository.kwargs["symbol"] == "600519.SH"
    assert repository.kwargs["is_conflict"] is False


def test_market_service_lists_trading_calendar() -> None:
    repository = FakeTradingCalendarRepository()
    service = MarketQueryService(trading_calendar_repository=repository)

    result = service.list_trading_calendar(
        object(),
        exchange="SH",
        start_date=date(2026, 7, 17),
        end_date=date(2026, 7, 17),
        is_open=True,
        limit=100,
        offset=0,
    )

    assert result.total == 1
    assert result.items[0].exchange == "SH"
    assert result.items[0].is_open is True
    assert repository.kwargs is not None
    assert repository.kwargs["exchange"] == "SH"


def test_market_service_gets_security_lifecycle_for_trading_day() -> None:
    security = SimpleNamespace(
        symbol="600519.SH",
        name="璐靛窞鑼呭彴",
        security_type="stock",
        exchange="SH",
        list_date=date(2001, 8, 27),
        delist_date=None,
        is_active=True,
    )
    calendar = SimpleNamespace(is_open=True)
    security_repository = FakeSecurityRepository(security)
    trading_calendar_repository = FakeTradingCalendarRepository(calendar)
    service = MarketQueryService(
        security_repository=security_repository,
        trading_calendar_repository=trading_calendar_repository,
    )

    result = service.get_security_lifecycle(
        object(),
        symbol="600519.sh",
        as_of_date=date(2026, 7, 17),
    )

    assert result is not None
    assert result.symbol == "600519.SH"
    assert result.lifecycle_status == "listed"
    assert result.is_trading_day is True
    assert result.should_have_daily_bar is True
    assert result.reason == "eligible"
    assert security_repository.symbol == "600519.SH"


def test_market_service_gets_security_lifecycle_after_delist_date() -> None:
    security = SimpleNamespace(
        symbol="000001.SH",
        name="涓婅瘉鎸囨暟",
        security_type="index",
        exchange="SH",
        list_date=date(1990, 12, 19),
        delist_date=date(2026, 7, 16),
        is_active=False,
    )
    calendar = SimpleNamespace(is_open=True)
    service = MarketQueryService(
        security_repository=FakeSecurityRepository(security),
        trading_calendar_repository=FakeTradingCalendarRepository(calendar),
    )

    result = service.get_security_lifecycle(
        object(),
        symbol="000001.SH",
        as_of_date=date(2026, 7, 17),
    )

    assert result is not None
    assert result.lifecycle_status == "delisted"
    assert result.should_have_daily_bar is False
    assert result.reason == "after_delist_date"


def test_market_service_lists_manual_corrections() -> None:
    repository = FakeManualCorrectionRepository()
    service = MarketQueryService(manual_correction_repository=repository)

    result = service.list_manual_corrections(
        object(),
        symbol="600519.sh",
        start_date=None,
        end_date=None,
        adjust_type="none",
        field_name="close",
        corrected_by="liuzu",
        limit=100,
        offset=0,
    )

    assert result.total == 1
    assert result.items[0].field_name == "close"
    assert repository.kwargs is not None
    assert repository.kwargs["symbol"] == "600519.SH"
    assert repository.kwargs["corrected_by"] == "liuzu"


def test_market_service_lists_corporate_actions() -> None:
    repository = FakeCorporateActionRepository()
    service = MarketQueryService(corporate_action_repository=repository)

    result = service.list_corporate_actions(
        object(),
        symbol="600519.sh",
        start_date=None,
        end_date=None,
        action_type="cash_dividend",
        source_name="hithink_finance",
        limit=100,
        offset=0,
    )

    assert result.total == 1
    assert result.items[0].symbol == "600519.SH"
    assert result.items[0].cash_dividend == Decimal("10.000000")
    assert repository.kwargs is not None
    assert repository.kwargs["symbol"] == "600519.SH"


def test_market_service_lists_adjustment_factors() -> None:
    repository = FakeAdjustmentFactorRepository()
    service = MarketQueryService(adjustment_factor_repository=repository)

    result = service.list_adjustment_factors(
        object(),
        symbol="600519.sh",
        start_date=None,
        end_date=None,
        adjust_type="qfq",
        source_name="hithink_finance",
        version="v1",
        quality_status="trusted",
        limit=100,
        offset=0,
    )

    assert result.total == 1
    assert result.items[0].symbol == "600519.SH"
    assert result.items[0].factor == Decimal("0.9123456789")
    assert repository.kwargs is not None
    assert repository.kwargs["symbol"] == "600519.SH"
