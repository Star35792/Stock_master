from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

from app.services.liquidity_risk import TrustedAverageDailyAmountService


class FakeDailyBarRepository:
    def __init__(self, rows) -> None:
        self.rows = rows
        self.captured = {}

    def list_recent_trusted_none_amounts(self, db, **kwargs):
        self.captured.update(kwargs)
        return self.rows


def _rows(symbol: str, count: int, amount: str = "100000"):
    start = date(2026, 6, 20)
    return [
        SimpleNamespace(
            daily_bar_id=index + 1,
            symbol=symbol,
            trade_date=start + timedelta(days=index),
            amount=Decimal(amount),
            source_name="hithink",
            quality_status="trusted",
            updated_at=datetime(2026, 7, 21, tzinfo=UTC),
        )
        for index in range(count)
    ]


def test_trusted_adv_uses_server_side_trailing_amounts() -> None:
    repository = FakeDailyBarRepository(_rows("600000.SH", 20))
    service = TrustedAverageDailyAmountService(repository=repository)

    result = service.calculate(
        object(),
        symbols={"600000.SH"},
        as_of_date=date(2026, 7, 21),
    )["600000.SH"]

    assert result.available is True
    assert result.average_daily_amount == Decimal("100000")
    assert result.observation_count == 20
    assert result.window == 20
    assert repository.captured["end_date"] == date(2026, 7, 21)
    assert len(result.input_hash) == 64


def test_trusted_adv_is_unavailable_when_history_is_short() -> None:
    result = TrustedAverageDailyAmountService(
        repository=FakeDailyBarRepository(_rows("600000.SH", 14))
    ).calculate(
        object(),
        symbols={"600000.SH"},
        as_of_date=date(2026, 7, 21),
    )["600000.SH"]

    assert result.available is False
    assert result.average_daily_amount is None
    assert result.observation_count == 14


def test_trusted_adv_is_unavailable_when_amount_is_missing() -> None:
    rows = _rows("600000.SH", 20)
    rows[-1].amount = None

    result = TrustedAverageDailyAmountService(repository=FakeDailyBarRepository(rows)).calculate(
        object(),
        symbols={"600000.SH"},
        as_of_date=date(2026, 7, 21),
    )["600000.SH"]

    assert result.available is False
    assert result.average_daily_amount is None
