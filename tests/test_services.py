from datetime import date, datetime, timezone
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
            updated_at=datetime(2026, 7, 16, tzinfo=timezone.utc),
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
