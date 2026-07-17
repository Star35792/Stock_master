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
