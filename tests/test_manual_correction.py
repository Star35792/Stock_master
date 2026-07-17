from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.services import ManualCorrectionService


class FakeDb:
    def __init__(self) -> None:
        self.committed = False

    def commit(self) -> None:
        self.committed = True


class FakeDailyBarRepository:
    def __init__(self, daily_bar: SimpleNamespace | None) -> None:
        self.daily_bar = daily_bar
        self.calls: list[dict] = []

    def get_by_key(self, db: object, **kwargs: object) -> SimpleNamespace | None:
        self.calls.append({"method": "get_by_key", **kwargs})
        return self.daily_bar

    def apply_manual_correction(self, db: object, **kwargs: object) -> None:
        self.calls.append({"method": "apply_manual_correction", **kwargs})
        setattr(kwargs["daily_bar"], kwargs["field_name"], kwargs["value"])
        kwargs["daily_bar"].quality_status = "manual_fixed"
        kwargs["daily_bar"].is_suspect = False


class FakeCorrectionRepository:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def create(self, db: object, **kwargs: object) -> None:
        self.calls.append(kwargs)


def _daily_bar(**overrides: object) -> SimpleNamespace:
    values = {
        "symbol": "600519.SH",
        "trade_date": date(2026, 7, 17),
        "adjust_type": "none",
        "open": Decimal("1200.00"),
        "high": Decimal("1250.00"),
        "low": Decimal("1190.00"),
        "close": Decimal("1242.47"),
        "volume": Decimal("1000"),
        "amount": Decimal("1242470"),
        "quality_status": "trusted",
        "is_suspect": False,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_manual_correction_updates_daily_bar_and_writes_audit() -> None:
    db = FakeDb()
    daily_bar = _daily_bar()
    daily_bar_repository = FakeDailyBarRepository(daily_bar)
    correction_repository = FakeCorrectionRepository()
    service = ManualCorrectionService(
        daily_bar_repository=daily_bar_repository,  # type: ignore[arg-type]
        correction_repository=correction_repository,  # type: ignore[arg-type]
    )

    result = service.apply_daily_bar_correction(
        db,  # type: ignore[arg-type]
        symbol="600519.sh",
        trade_date=date(2026, 7, 17),
        field_name="close",
        new_value="1243.00",
        reason="manual source confirmed",
        corrected_by="liuzu",
    )

    assert daily_bar.close == Decimal("1243.00")
    assert daily_bar.quality_status == "manual_fixed"
    assert result.old_value == "1242.47"
    assert result.new_value == "1243.00"
    assert result.quality_status == "manual_fixed"
    assert correction_repository.calls == [
        {
            "dataset": "daily_bar",
            "symbol": "600519.SH",
            "trade_date": date(2026, 7, 17),
            "adjust_type": "none",
            "field_name": "close",
            "old_value": "1242.47",
            "new_value": "1243.00",
            "reason": "manual source confirmed",
            "corrected_by": "liuzu",
        }
    ]
    assert db.committed is True


def test_manual_correction_rejects_unknown_field() -> None:
    service = ManualCorrectionService(
        daily_bar_repository=FakeDailyBarRepository(_daily_bar()),  # type: ignore[arg-type]
        correction_repository=FakeCorrectionRepository(),  # type: ignore[arg-type]
    )

    with pytest.raises(ValueError, match="field_name"):
        service.apply_daily_bar_correction(
            FakeDb(),  # type: ignore[arg-type]
            symbol="600519.SH",
            trade_date=date(2026, 7, 17),
            field_name="source_name",
            new_value="csv",
            reason="manual source confirmed",
            corrected_by="liuzu",
        )


def test_manual_correction_rejects_invalid_ohlc() -> None:
    db = FakeDb()
    correction_repository = FakeCorrectionRepository()
    service = ManualCorrectionService(
        daily_bar_repository=FakeDailyBarRepository(_daily_bar()),  # type: ignore[arg-type]
        correction_repository=correction_repository,  # type: ignore[arg-type]
    )

    with pytest.raises(ValueError, match="OHLC"):
        service.apply_daily_bar_correction(
            db,  # type: ignore[arg-type]
            symbol="600519.SH",
            trade_date=date(2026, 7, 17),
            field_name="low",
            new_value="1300",
            reason="manual source confirmed",
            corrected_by="liuzu",
        )

    assert correction_repository.calls == []
    assert db.committed is False


def test_manual_correction_requires_existing_daily_bar() -> None:
    service = ManualCorrectionService(
        daily_bar_repository=FakeDailyBarRepository(None),  # type: ignore[arg-type]
        correction_repository=FakeCorrectionRepository(),  # type: ignore[arg-type]
    )

    with pytest.raises(ValueError, match="not found"):
        service.apply_daily_bar_correction(
            FakeDb(),  # type: ignore[arg-type]
            symbol="600519.SH",
            trade_date=date(2026, 7, 17),
            field_name="close",
            new_value="1243.00",
            reason="manual source confirmed",
            corrected_by="liuzu",
        )
