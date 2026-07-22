from contextlib import nullcontext
from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.services.paper_equity import (
    PaperEquityConflict,
    PaperEquitySnapshotService,
    PaperEquityValidationError,
)

NOW = datetime(2026, 7, 21, tzinfo=UTC)
VALUATION_DATE = date(2026, 7, 21)


class FakeSession:
    def begin_nested(self):
        return nullcontext()


class FakePaperRepository:
    def __init__(self, *, cash="10000", positions=None, latest_fill_date=None) -> None:
        self.account = SimpleNamespace(
            id=1,
            cash_balance=Decimal(cash),
            updated_at=NOW,
        )
        self.positions = positions or []
        self.latest_fill_date = latest_fill_date

    def get_account_for_update(self, db, *, account_id: int):
        return self.account if account_id == 1 else None

    def list_positions(self, db, *, account_id: int):
        return self.positions

    def get_latest_fill_date(self, db, *, account_id: int):
        return self.latest_fill_date


class FakeEquityRepository:
    def __init__(self, previous=None) -> None:
        self.previous = previous
        self.by_key = {}
        self.by_date = {}
        self.components = {}
        self.save_count = 0

    def get_by_idempotency_key(self, db, *, idempotency_key: str):
        return self.by_key.get(idempotency_key)

    def get_by_account_date(self, db, *, account_id: int, valuation_date):
        return self.by_date.get((account_id, valuation_date))

    def get_latest(self, db, *, account_id: int):
        return self.previous

    def get_with_components(self, db, *, account_id: int, snapshot_id: int):
        candidates = [*self.by_key.values()]
        if self.previous is not None:
            candidates.append(self.previous)
        snapshot = next(
            (
                item
                for item in candidates
                if item.id == snapshot_id and item.account_id == account_id
            ),
            None,
        )
        return snapshot, self.components.get(snapshot_id, [])

    def save(self, db, *, snapshot_values: dict, component_values: list[dict]):
        self.save_count += 1
        snapshot = SimpleNamespace(
            id=100 + self.save_count,
            created_at=NOW,
            **snapshot_values,
        )
        components = [SimpleNamespace(**value) for value in component_values]
        self.by_key[snapshot.idempotency_key] = snapshot
        self.by_date[(snapshot.account_id, snapshot.valuation_date)] = snapshot
        self.components[snapshot.id] = components
        self.previous = snapshot
        return snapshot


class FakeSecurityRepository:
    def get_many(self, db, *, symbols: set[str]):
        return [SimpleNamespace(symbol=symbol, exchange="SH") for symbol in sorted(symbols)]


class FakeBarRepository:
    def __init__(self, *, close="100", quality_status="trusted", is_suspect=False):
        self.close = Decimal(close)
        self.quality_status = quality_status
        self.is_suspect = is_suspect

    def list_by_symbols_on_date_for_update(self, db, *, symbols, trade_date, adjust_type):
        return [
            SimpleNamespace(
                id=index + 1,
                symbol=symbol,
                trade_date=trade_date,
                adjust_type=adjust_type,
                close=self.close,
                source_name="hithink",
                quality_status=self.quality_status,
                is_suspect=self.is_suspect,
                updated_at=NOW,
            )
            for index, symbol in enumerate(sorted(symbols))
        ]


class FakeCalendarRepository:
    def __init__(self, open_dates=None) -> None:
        self.open_dates = open_dates or [VALUATION_DATE]

    def get_many_on_date(self, db, *, exchanges, trade_date):
        return [SimpleNamespace(exchange=exchange, is_open=True) for exchange in sorted(exchanges)]

    def list_open_dates(self, db, **kwargs):
        return self.open_dates


class FakeCorporateActionRepository:
    def __init__(self, actions=None) -> None:
        self.actions = actions or []

    def list_for_symbols_date_range(self, db, **kwargs):
        return self.actions


def _position(symbol="600000.SH", quantity=100):
    return SimpleNamespace(
        symbol=symbol,
        quantity=quantity,
        average_cost=Decimal("90"),
        updated_at=NOW,
    )


def _previous_snapshot(**overrides):
    values = {
        "id": 50,
        "account_id": 1,
        "valuation_date": date(2026, 7, 18),
        "high_watermark": Decimal("20000"),
        "monitoring_start_date": date(2026, 7, 18),
        "observation_count": 1,
        "history_status": "bootstrap",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _service(**overrides):
    return PaperEquitySnapshotService(
        paper_repository=overrides.get("paper_repository", FakePaperRepository()),
        equity_repository=overrides.get("equity_repository", FakeEquityRepository()),
        security_repository=overrides.get("security_repository", FakeSecurityRepository()),
        daily_bar_repository=overrides.get("bar_repository", FakeBarRepository()),
        calendar_repository=overrides.get("calendar_repository", FakeCalendarRepository()),
        corporate_action_repository=overrides.get(
            "corporate_action_repository", FakeCorporateActionRepository()
        ),
    )


def test_capture_cash_only_bootstrap_snapshot() -> None:
    result = _service().capture(
        FakeSession(),
        account_id=1,
        valuation_date=VALUATION_DATE,
        idempotency_key="equity-1",
    )

    assert result.cash_balance == Decimal("10000.0000")
    assert result.positions_market_value == Decimal("0.0000")
    assert result.total_equity == Decimal("10000.0000")
    assert result.observed_high_watermark == Decimal("10000.0000")
    assert result.drawdown_since_monitoring_start == 0
    assert result.history_status == "bootstrap"
    assert result.execution_allowed is False


def test_capture_continuous_snapshot_calculates_observed_drawdown() -> None:
    repository = FakeEquityRepository(previous=_previous_snapshot())
    result = _service(
        paper_repository=FakePaperRepository(
            cash="8000",
            positions=[_position()],
        ),
        equity_repository=repository,
    ).capture(
        FakeSession(),
        account_id=1,
        valuation_date=VALUATION_DATE,
        idempotency_key="equity-2",
    )

    assert result.positions_market_value == Decimal("10000.0000")
    assert result.total_equity == Decimal("18000.0000")
    assert result.observed_high_watermark == Decimal("20000.0000")
    assert result.drawdown_since_monitoring_start == Decimal("-0.1000000000")
    assert result.history_status == "continuous"
    assert result.observation_count == 2
    assert result.components[0].adjust_type == "none"


def test_capture_replays_same_idempotent_request() -> None:
    repository = FakeEquityRepository()
    service = _service(equity_repository=repository)

    first = service.capture(
        FakeSession(),
        account_id=1,
        valuation_date=VALUATION_DATE,
        idempotency_key="equity-3",
    )
    replay = service.capture(
        FakeSession(),
        account_id=1,
        valuation_date=VALUATION_DATE,
        idempotency_key="equity-3",
    )

    assert first.snapshot_id == replay.snapshot_id
    assert repository.save_count == 1


def test_capture_rejects_same_date_with_different_idempotency_key() -> None:
    repository = FakeEquityRepository()
    service = _service(equity_repository=repository)
    service.capture(
        FakeSession(),
        account_id=1,
        valuation_date=VALUATION_DATE,
        idempotency_key="equity-4",
    )

    with pytest.raises(PaperEquityConflict, match="already exists"):
        service.capture(
            FakeSession(),
            account_id=1,
            valuation_date=VALUATION_DATE,
            idempotency_key="equity-5",
        )


def test_capture_rejects_untrusted_market_bar_without_partial_snapshot() -> None:
    repository = FakeEquityRepository()
    service = _service(
        paper_repository=FakePaperRepository(positions=[_position()]),
        equity_repository=repository,
        bar_repository=FakeBarRepository(quality_status="suspect"),
    )

    with pytest.raises(PaperEquityValidationError, match="trusted"):
        service.capture(
            FakeSession(),
            account_id=1,
            valuation_date=VALUATION_DATE,
            idempotency_key="equity-6",
        )

    assert repository.save_count == 0


def test_capture_rejects_unhandled_corporate_action() -> None:
    service = _service(
        paper_repository=FakePaperRepository(positions=[_position()]),
        corporate_action_repository=FakeCorporateActionRepository(
            actions=[SimpleNamespace(symbol="600000.SH")]
        ),
    )

    with pytest.raises(PaperEquityValidationError, match="corporate actions"):
        service.capture(
            FakeSession(),
            account_id=1,
            valuation_date=VALUATION_DATE,
            idempotency_key="equity-7",
        )
