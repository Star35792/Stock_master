from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.data_sources.base import DailyBarRecord
from app.database.session import initialize_database
from app.models.market import DailyBar, Security, TradingCalendar
from app.repositories.market import DailyBarRepository
from app.services.gap_backfill import MissingDailyBarBackfillService


def _candidate(
    day: date,
    *,
    symbol: str = "600000.SH",
    reason: str = "missing",
    list_date: date | None = date(2000, 1, 1),
):
    return SimpleNamespace(
        symbol=symbol,
        name="浦发银行",
        security_type="stock",
        exchange="SH",
        list_date=list_date,
        delist_date=None,
        trade_date=day,
        adjust_type="qfq",
        gap_reason=reason,
    )


class FakeDailyBarRepository:
    def __init__(self, rows):
        self.rows = rows
        self.offsets = []

    def list_repair_candidates_by_calendar(self, db, *, limit, offset, **kwargs):
        self.offsets.append(offset)
        return self.rows[offset : offset + limit], len(self.rows)


class FakeStatusRepository:
    def __init__(self, statuses=None):
        self.statuses = statuses or {}

    def explicit_statuses_on_date(self, db, *, symbols, trade_date):
        return {
            symbol: self.statuses[(symbol, trade_date)]
            for symbol in symbols
            if (symbol, trade_date) in self.statuses
        }


class FakeBatch:
    id = 9

    def __init__(self, request_params):
        self.request_params = request_params


class FakeRawRepository:
    def __init__(self, *, insert_changed=True):
        self.rows = []
        self.finished = None
        self.batch = None
        self.insert_changed = insert_changed

    def create_batch(self, db, **kwargs):
        self.batch = FakeBatch(kwargs["request_params"])
        return self.batch

    def add_daily_bar_if_changed(self, db, row):
        self.rows.append(row)
        return self.insert_changed

    def finish_batch(self, db, batch, **kwargs):
        self.finished = kwargs


class FakeDb:
    def commit(self):
        pass

    def rollback(self):
        pass


class FakeSource:
    name = "primary"
    source_type = "vendor"
    last_source_name = None

    def __init__(self, records):
        self.records = records

    def fetch_daily_bars(self, **kwargs):
        return list(self.records)


class FailingSource(FakeSource):
    def fetch_daily_bars(self, **kwargs):
        raise RuntimeError("source unavailable")


def test_repository_classifies_missing_partial_and_suspect_rows() -> None:
    engine = create_engine("duckdb:///:memory:")
    initialize_database(engine)
    days = [date(2026, 1, day) for day in range(5, 9)]
    with Session(engine) as db:
        db.add(
            Security(
                symbol="600000.SH",
                name="浦发银行",
                security_type="stock",
                exchange="SH",
                list_date=date(1999, 11, 10),
            )
        )
        db.add_all([TradingCalendar(exchange="SH", trade_date=day, is_open=True) for day in days])
        for day, status, volume, amount in (
            (days[1], "trusted", None, Decimal("1000")),
            (days[2], "suspect", Decimal("100"), Decimal("1000")),
            (days[3], "manual_fixed", None, Decimal("1000")),
        ):
            db.add(
                DailyBar(
                    symbol="600000.SH",
                    security_type="stock",
                    trade_date=day,
                    adjust_type="qfq",
                    open=Decimal("10"),
                    high=Decimal("11"),
                    low=Decimal("9"),
                    close=Decimal("10"),
                    volume=volume,
                    amount=amount,
                    source_name="test",
                    source_count=1,
                    quality_score=Decimal("100"),
                    quality_status=status,
                    is_suspect=status == "suspect",
                )
            )
        db.commit()

        rows, total = DailyBarRepository().list_repair_candidates_by_calendar(
            db, start_date=days[0], end_date=days[-1], adjust_type="qfq"
        )

    engine.dispose()
    assert total == 3
    assert [(row.trade_date, row.gap_reason) for row in rows] == [
        (days[0], "missing"),
        (days[1], "required_field_missing"),
        (days[2], "suspect"),
    ]


def test_plan_reads_every_page_without_duplicates() -> None:
    first = date(2020, 1, 1)
    rows = [_candidate(first + timedelta(days=index)) for index in range(1001)]
    daily = FakeDailyBarRepository(rows)
    service = MissingDailyBarBackfillService(
        daily_bar_repository=daily,
        security_status_repository=FakeStatusRepository(),
    )

    plan = service.plan(
        object(),
        symbols=["600000.SH"],
        start_date=first,
        end_date=first + timedelta(days=1000),
    )

    assert daily.offsets == [0, 1000]
    assert len(plan.candidates) == 1001
    assert len({(row.symbol, row.trade_date) for row in plan.candidates}) == 1001


def test_plan_classifies_status_lifecycle_partial_and_suspect() -> None:
    days = [date(2026, 1, day) for day in range(5, 10)]
    rows = [
        _candidate(days[0]),
        _candidate(days[1]),
        _candidate(days[2], list_date=None),
        _candidate(days[3], reason="required_field_missing"),
        _candidate(days[4], reason="suspect"),
        _candidate(days[4] + timedelta(days=1)),
    ]
    service = MissingDailyBarBackfillService(
        daily_bar_repository=FakeDailyBarRepository(rows),
        security_status_repository=FakeStatusRepository(
            {
                ("600000.SH", days[1]): "suspended",
                ("600000.SH", days[4] + timedelta(days=1)): "conflict",
            }
        ),
    )

    plan = service.plan(
        object(),
        symbols=["600000.SH"],
        start_date=days[0],
        end_date=days[-1] + timedelta(days=1),
    )

    assert [(row.trade_date, row.reason) for row in plan.candidates] == [
        (days[0], "missing"),
        (days[3], "required_field_missing"),
    ]
    assert plan.missing_rows == 4
    assert plan.partial_rows == 1
    assert plan.suspect_rows == 1
    assert plan.status_explained_rows == 1
    assert plan.lifecycle_unknown_rows == 1
    assert plan.status_conflict_rows == 1
    assert plan.unresolved_rows == 3


def test_diagnostic_only_plan_returns_partial_without_creating_batch() -> None:
    day = date(2026, 1, 5)
    service = MissingDailyBarBackfillService(
        daily_bar_repository=FakeDailyBarRepository([_candidate(day, reason="suspect")]),
        security_status_repository=FakeStatusRepository(),
    )

    result = service.backfill(FakeDb(), symbols=["600000.SH"], start_date=day, end_date=day)

    assert result.batch_no is None
    assert result.expected_rows == 0
    assert result.suspect_rows == 1
    assert result.unresolved_rows == 1
    assert result.status == "partial"


def test_partial_source_result_is_audited_as_partial() -> None:
    days = [date(2026, 1, 5), date(2026, 1, 6)]
    daily = FakeDailyBarRepository([_candidate(day) for day in days])
    record = DailyBarRecord(
        symbol="600000.SH",
        trade_date=days[0],
        adjust_type="qfq",
        open=1,
        high=1,
        low=1,
        close=1,
    )
    raw = FakeRawRepository()
    service = MissingDailyBarBackfillService(
        primary_data_source=FakeSource([record]),
        data_source=FakeSource([]),
        daily_bar_repository=daily,
        security_status_repository=FakeStatusRepository(),
        repository=raw,
    )

    result = service.backfill(
        FakeDb(), symbols=["600000.SH"], start_date=days[0], end_date=days[-1]
    )

    assert result.expected_rows == 2
    assert result.inserted_rows == 1
    assert result.unresolved_rows == 1
    assert result.unresolved_actionable_rows == 1
    assert raw.finished["status"] == "partial"
    assert raw.finished["rejected_count"] == 0
    assert "1 planned rows were not returned" in raw.finished["error_message"]


def test_unchanged_raw_row_is_skipped_without_being_rejected() -> None:
    day = date(2026, 1, 5)
    record = DailyBarRecord(
        symbol="600000.SH",
        trade_date=day,
        adjust_type="qfq",
        open=1,
        high=1,
        low=1,
        close=1,
    )
    raw = FakeRawRepository(insert_changed=False)
    service = MissingDailyBarBackfillService(
        primary_data_source=FakeSource([record]),
        data_source=FakeSource([]),
        daily_bar_repository=FakeDailyBarRepository([_candidate(day)]),
        security_status_repository=FakeStatusRepository(),
        repository=raw,
    )

    result = service.backfill(FakeDb(), symbols=["600000.SH"], start_date=day, end_date=day)

    assert result.inserted_rows == 0
    assert result.skipped_rows == 1
    assert result.skipped_unchanged_rows == 1
    assert result.rejected_rows == 0
    assert raw.finished == {
        "status": "success",
        "row_count": 1,
        "accepted_count": 0,
        "rejected_count": 0,
        "error_message": None,
    }
    assert raw.batch.request_params["skipped_unchanged_count"] == 1


def test_in_scope_side_rows_and_duplicates_are_skipped_not_rejected() -> None:
    planned_day = date(2026, 1, 5)
    unplanned_day = date(2026, 1, 6)

    def record(day):
        return DailyBarRecord(
            symbol="600000.SH",
            trade_date=day,
            adjust_type="qfq",
            open=1,
            high=1,
            low=1,
            close=1,
        )

    raw = FakeRawRepository()
    service = MissingDailyBarBackfillService(
        primary_data_source=FakeSource(
            [record(planned_day), record(unplanned_day), record(unplanned_day)]
        ),
        data_source=FakeSource([]),
        daily_bar_repository=FakeDailyBarRepository([_candidate(planned_day)]),
        security_status_repository=FakeStatusRepository(),
        repository=raw,
    )

    result = service.backfill(
        FakeDb(),
        symbols=["600000.SH"],
        start_date=planned_day,
        end_date=unplanned_day,
    )

    assert result.fetched_rows == 3
    assert result.inserted_rows == 1
    assert result.skipped_rows == 2
    assert result.skipped_duplicate_rows == 1
    assert result.ignored_in_scope_rows == 1
    assert result.rejected_rows == 0
    assert result.status == "success"
    assert raw.finished["rejected_count"] == 0
    assert raw.batch.request_params["skipped_duplicate_count"] == 1
    assert raw.batch.request_params["ignored_in_scope_count"] == 1


def test_conflicting_duplicate_payload_is_rejected_and_left_unresolved() -> None:
    day = date(2026, 1, 5)

    def record(close: int) -> DailyBarRecord:
        return DailyBarRecord(
            symbol="600000.SH",
            trade_date=day,
            adjust_type="qfq",
            open=1,
            high=max(close, 1),
            low=1,
            close=close,
        )

    raw = FakeRawRepository()
    service = MissingDailyBarBackfillService(
        primary_data_source=FakeSource([record(1), record(2)]),
        data_source=FakeSource([record(1)]),
        daily_bar_repository=FakeDailyBarRepository([_candidate(day)]),
        security_status_repository=FakeStatusRepository(),
        repository=raw,
    )

    with pytest.raises(RuntimeError, match="conflicting response keys"):
        service.backfill(FakeDb(), symbols=["600000.SH"], start_date=day, end_date=day)

    assert raw.rows == []
    assert raw.finished["status"] == "failed"
    assert raw.finished["rejected_count"] == 1
    assert raw.batch.request_params["unresolved_actionable_rows"] == 1
    assert raw.batch.request_params["conflicting_response_count"] == 1
    assert raw.batch.request_params["conflicting_response_keys"][0]["key"] == (
        "600000.SH|2026-01-05|qfq"
    )
    assert len(
        raw.batch.request_params["conflicting_response_keys"][0]["variant_digests"]
    ) == 2


def test_out_of_scope_source_record_is_rejected() -> None:
    day = date(2026, 1, 5)
    wrong = DailyBarRecord(
        symbol="000001.SZ",
        trade_date=day,
        adjust_type="qfq",
        open=1,
        high=1,
        low=1,
        close=1,
    )
    planned = DailyBarRecord(
        symbol="600000.SH",
        trade_date=day,
        adjust_type="qfq",
        open=1,
        high=1,
        low=1,
        close=1,
    )
    raw = FakeRawRepository()
    service = MissingDailyBarBackfillService(
        primary_data_source=FakeSource([planned, wrong]),
        data_source=FakeSource([]),
        daily_bar_repository=FakeDailyBarRepository([_candidate(day)]),
        security_status_repository=FakeStatusRepository(),
        repository=raw,
    )

    result = service.backfill(FakeDb(), symbols=["600000.SH"], start_date=day, end_date=day)

    assert result.rejected_rows == 1
    assert result.status == "partial"
    assert raw.finished["rejected_count"] == 1
    assert raw.batch.request_params["rejected_out_of_scope_count"] == 1


def test_wrong_adjust_type_cannot_resolve_a_planned_key() -> None:
    days = [date(2026, 1, 5), date(2026, 1, 6)]
    valid = DailyBarRecord(
        symbol="600000.SH",
        trade_date=days[0],
        adjust_type="qfq",
        open=1,
        high=1,
        low=1,
        close=1,
    )
    wrong_adjust = DailyBarRecord(
        symbol="600000.SH",
        trade_date=days[1],
        adjust_type="none",
        open=1,
        high=1,
        low=1,
        close=1,
    )
    raw = FakeRawRepository()
    service = MissingDailyBarBackfillService(
        primary_data_source=FakeSource([valid, wrong_adjust]),
        data_source=FakeSource([]),
        daily_bar_repository=FakeDailyBarRepository([_candidate(day) for day in days]),
        security_status_repository=FakeStatusRepository(),
        repository=raw,
    )

    result = service.backfill(
        FakeDb(), symbols=["600000.SH"], start_date=days[0], end_date=days[1]
    )

    assert result.inserted_rows == 1
    assert result.rejected_rows == 1
    assert result.unresolved_actionable_rows == 1
    assert {(row.symbol, row.trade_date, row.adjust_type) for row in raw.rows} == {
        ("600000.SH", days[0], "qfq")
    }


def test_wrong_symbol_response_is_rejected_even_if_symbol_is_planned_elsewhere() -> None:
    day = date(2026, 1, 5)
    returned = DailyBarRecord(
        symbol="000001.SZ",
        trade_date=day,
        adjust_type="qfq",
        open=1,
        high=1,
        low=1,
        close=1,
    )
    raw = FakeRawRepository()
    service = MissingDailyBarBackfillService(
        primary_data_source=FakeSource([returned]),
        data_source=FakeSource([]),
        daily_bar_repository=FakeDailyBarRepository(
            [_candidate(day), _candidate(day, symbol="000001.SZ")]
        ),
        security_status_repository=FakeStatusRepository(),
        repository=raw,
    )

    result = service.backfill(
        FakeDb(),
        symbols=["600000.SH", "000001.SZ"],
        start_date=day,
        end_date=day,
    )

    assert result.inserted_rows == 1
    assert result.rejected_rows == 1
    assert result.unresolved_actionable_rows == 1
    assert [row.symbol for row in raw.rows] == ["000001.SZ"]


def test_source_error_keeps_batch_partial_when_fallback_fills_every_key() -> None:
    day = date(2026, 1, 5)
    record = DailyBarRecord(
        symbol="600000.SH",
        trade_date=day,
        adjust_type="qfq",
        open=1,
        high=1,
        low=1,
        close=1,
    )
    raw = FakeRawRepository()
    service = MissingDailyBarBackfillService(
        primary_data_source=FailingSource([]),
        data_source=FakeSource([record]),
        daily_bar_repository=FakeDailyBarRepository([_candidate(day)]),
        security_status_repository=FakeStatusRepository(),
        repository=raw,
    )

    result = service.backfill(FakeDb(), symbols=["600000.SH"], start_date=day, end_date=day)

    assert result.unresolved_rows == 0
    assert result.status == "partial"
    assert result.source_error_count == 1
    assert raw.finished["status"] == "partial"
    assert "source unavailable" in raw.finished["error_message"]
