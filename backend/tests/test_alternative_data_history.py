"""Tests for the alternative-data history backfill.

The backfill is the piece that turns the six-family datasets from "two
snapshot days" into something a factor profiler can compute an IC over.
Three invariants matter most and are asserted here:

* **the requested date must win** -- several endpoints do not echo the
  requested date back (``limit-up-pool`` / ``limit-break-pool`` /
  ``limit-down-pool``), and their ``timestamp`` is the *response* time.
  Without an explicit injection the backfill would stamp every historical
  row with the day the backfill ran, which is textbook look-ahead;
* **a failing day must not abort the round** -- one flaky endpoint should
  not throw away the days that did succeed;
* **the backfill must be idempotent** -- re-running the same range must not
  duplicate rows.
"""

from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker

from app.database.base import Base
from app.models.market import AlternativeDataSnapshot, TradingCalendar
from app.models.raw import ImportBatch, RawVendorSnapshot
from app.services.hithink_alternative_history import (
    DEFAULT_HISTORY_DATASETS,
    HISTORY_FETCHERS,
    HithinkAlternativeHistoryService,
    open_trade_dates,
)


def _session():
    engine = create_engine("duckdb:///:memory:")
    with engine.begin() as connection:
        connection.execute(text("CREATE SCHEMA IF NOT EXISTS market"))
        connection.execute(text("CREATE SCHEMA IF NOT EXISTS raw"))
    Base.metadata.create_all(
        engine,
        tables=[
            AlternativeDataSnapshot.__table__,
            TradingCalendar.__table__,
            ImportBatch.__table__,
            RawVendorSnapshot.__table__,
        ],
    )
    return sessionmaker(bind=engine, expire_on_commit=False)()


def _seed_calendar(db, days: list[date]) -> None:
    for day in days:
        db.add(
            TradingCalendar(
                exchange="SH",
                trade_date=day,
                is_open=True,
                source_name="test",
            )
        )
    db.commit()


class FakeDataSource:
    """端点替身：只回放调用参数，不联网。"""

    name = "fake_hithink"
    source_type = "api"

    def __init__(self, *, fail_on: set[str] | None = None) -> None:
        self.fail_on = fail_on or set()
        self.calls: list[tuple[str, object]] = []

    def _guard(self, label: str) -> None:
        self.calls.append((label, None))
        if label in self.fail_on:
            raise RuntimeError(f"boom: {label}")

    def fetch_hot_stock_list_history(self, *, trade_date: date) -> dict:
        self.calls.append(("hot_stock", trade_date))
        return {
            "date": trade_date.isoformat(),
            "date_ms": 1792425600000,
            # 供给下限是 10 行（``min_expected_rows``），测试替身必须给足，
            # 否则会被当成空集——那正是供给检查该拦下的情况。
            "item": [
                {
                    "thscode": f"6000{i:02d}.SH",
                    "ticker": f"6000{i:02d}",
                    "name": "热股",
                    "rank": i + 1,
                }
                for i in range(12)
            ],
        }

    def fetch_limit_up_pool(self, *, date_ms: int | None = None) -> dict:
        self.calls.append(("limit_up_pool", date_ms))
        # 该端点**不回显**请求日期，timestamp 是响应时刻。
        return {
            "timestamp": 1794393201626,
            "item": [
                {
                    "thscode": f"6001{i:02d}.SH",
                    "ticker": f"6001{i:02d}",
                    "name": "涨停",
                    "last_price": 12.0,
                }
                for i in range(12)
            ],
        }

    def fetch_limit_break_pool(self, *, date_ms: int | None = None) -> dict:
        self.calls.append(("failed_limit_up", date_ms))
        return {
            "timestamp": 1794393255573,
            "item": [
                {"thscode": "600002.SH", "ticker": "600002", "name": "炸板", "last_price": 8.0}
            ],
        }

    def fetch_limit_down_pool(self, *, date_ms: int | None = None) -> dict:
        self.calls.append(("limit_down_pool", date_ms))
        return {
            "timestamp": 1794393256318,
            "item": [
                {"thscode": "600003.SH", "ticker": "600003", "name": "跌停", "last_price": 3.0}
            ],
        }

    def fetch_limit_up_statistics(self, *, date: str | None = None) -> dict:
        self.calls.append(("limit_up_statistics", date))
        return {
            "trade_date": date,
            "seal_rate": 0.64,
            "open_stock_count": 29,
            "limit_up_stock_count": 52,
            "touched_limit_up_stock_count": 81,
        }

    def fetch_dragon_tiger_list(self, *, board_type: str = "all", trade_date: date | None = None):
        label = "dragon_tiger" if board_type == "all" else "dragon_tiger_hot_money"
        self.calls.append((label, trade_date))
        if board_type == "all":
            return {
                "trade_date": trade_date.isoformat() if trade_date else None,
                "timestamp": 1792425600000,
                "stock_items": [
                    {"thscode": "600004.SH", "ticker": "600004", "name": "上榜", "net_value": 1.0}
                ],
                "hot_money_items": [],
            }
        return {
            "trade_date": trade_date.isoformat() if trade_date else None,
            "timestamp": 1792425600000,
            "hot_money_items": [
                {
                    "name": "低位挖掘",
                    "buying": 100.0,
                    "rows": [
                        {
                            "thscode": "600005.SH",
                            "ticker": "600005",
                            "name": "席位股",
                            "net_value": 2.0,
                        }
                    ],
                }
            ],
        }


def _service(fake: FakeDataSource) -> HithinkAlternativeHistoryService:
    return HithinkAlternativeHistoryService(
        data_source=fake,  # type: ignore[arg-type]
        request_interval_seconds=0.0,
    )


def test_backfill_stamps_the_requested_date_not_the_response_time() -> None:
    """不回显日期的端点必须由调用方注入请求日期。

    端点响应里的 ``timestamp`` 是响应时刻（回填当天）。若不注入，
    2026-10-20 的涨停数据会被标成回填当天——教科书级的前视污染。
    """
    db = _session()
    _seed_calendar(db, [date(2026, 10, 20)])
    fake = FakeDataSource()

    result = _service(fake).collect_history(
        db,
        start_date=date(2026, 10, 20),
        end_date=date(2026, 10, 20),
    )

    assert result.status == "success"
    rows = list(db.scalars(select(AlternativeDataSnapshot)).all())
    assert rows
    for row in rows:
        assert row.snapshot_date == date(2026, 10, 20), row.dataset
    db.close()


def test_backfill_covers_every_supported_dataset() -> None:
    db = _session()
    _seed_calendar(db, [date(2026, 10, 20)])
    fake = FakeDataSource()

    _service(fake).collect_history(
        db,
        start_date=date(2026, 10, 20),
        end_date=date(2026, 10, 20),
    )

    datasets = {row.dataset for row in db.scalars(select(AlternativeDataSnapshot)).all()}
    assert datasets == set(DEFAULT_HISTORY_DATASETS)
    db.close()


def test_backfill_uses_the_right_history_parameter_per_endpoint() -> None:
    """每个端点的历史参数不同：池类用毫秒、榜单类用 ISO 日期。"""
    db = _session()
    _seed_calendar(db, [date(2026, 10, 20)])
    fake = FakeDataSource()

    _service(fake).collect_history(
        db,
        start_date=date(2026, 10, 20),
        end_date=date(2026, 10, 20),
    )

    calls = dict(fake.calls)
    # 池类端点：毫秒时间戳（上海时区零点）
    assert isinstance(calls["limit_up_pool"], int)
    assert calls["limit_up_pool"] == 1792425600000
    assert isinstance(calls["failed_limit_up"], int)
    assert isinstance(calls["limit_down_pool"], int)
    # 榜单类端点：ISO 日期字符串
    assert calls["hot_stock"] == date(2026, 10, 20)
    assert calls["limit_up_statistics"] == "2026-10-20"
    assert calls["dragon_tiger"] == date(2026, 10, 20)
    db.close()


def test_backfill_skips_non_trading_days() -> None:
    """非交易日不该发请求——只会拿回空集并烧限流额度。"""
    db = _session()
    _seed_calendar(db, [date(2026, 10, 20), date(2026, 10, 22)])
    db.add(
        TradingCalendar(
            exchange="SH",
            trade_date=date(2026, 10, 21),
            is_open=False,
            source_name="test",
        )
    )
    db.commit()
    fake = FakeDataSource()

    _service(fake).collect_history(
        db,
        start_date=date(2026, 10, 20),
        end_date=date(2026, 10, 22),
    )

    requested_days = {value for label, value in fake.calls if isinstance(value, date)}
    assert requested_days == {date(2026, 10, 20), date(2026, 10, 22)}
    db.close()


def test_one_failing_day_does_not_abort_the_round() -> None:
    """单日失败只记账，其余日期照常回填。"""
    db = _session()
    _seed_calendar(db, [date(2026, 10, 20), date(2026, 10, 21)])
    fake = FakeDataSource()

    service = _service(fake)
    service.data_source = _FlakySource(fail_dates={date(2026, 10, 20)})  # type: ignore[assignment]

    result = service.collect_history(
        db,
        start_date=date(2026, 10, 20),
        end_date=date(2026, 10, 21),
    )

    assert result.status == "failed"
    assert result.failures
    days = {row.snapshot_date for row in db.scalars(select(AlternativeDataSnapshot)).all()}
    assert date(2026, 10, 21) in days
    db.close()


def test_backfill_is_idempotent() -> None:
    db = _session()
    _seed_calendar(db, [date(2026, 10, 20)])
    fake = FakeDataSource()
    service = _service(fake)

    service.collect_history(db, start_date=date(2026, 10, 20), end_date=date(2026, 10, 20))
    first = len(list(db.scalars(select(AlternativeDataSnapshot)).all()))
    service.collect_history(db, start_date=date(2026, 10, 20), end_date=date(2026, 10, 20))
    second = len(list(db.scalars(select(AlternativeDataSnapshot)).all()))

    assert first == second
    db.close()


def test_unknown_dataset_is_rejected() -> None:
    db = _session()
    with pytest.raises(ValueError, match="unknown datasets"):
        _service(FakeDataSource()).collect_history(
            db,
            start_date=date(2026, 10, 20),
            end_date=date(2026, 10, 20),
            datasets=("no_such_dataset",),
        )
    db.close()


def test_trade_date_range_must_be_ordered() -> None:
    db = _session()
    with pytest.raises(ValueError):
        open_trade_dates(db, date(2026, 10, 22), date(2026, 10, 20))
    db.close()


def test_hot_money_rows_keep_the_seat_in_the_item_key() -> None:
    """席位归属必须留在 item_key 里，否则同股多席位会被静默覆盖。"""
    db = _session()
    _seed_calendar(db, [date(2026, 10, 20)])
    _service(FakeDataSource()).collect_history(
        db, start_date=date(2026, 10, 20), end_date=date(2026, 10, 20)
    )

    row = db.scalar(
        select(AlternativeDataSnapshot).where(
            AlternativeDataSnapshot.dataset == "dragon_tiger_hot_money"
        )
    )
    assert row is not None
    assert row.item_key.startswith("低位挖掘|")
    db.close()


def test_limit_up_statistics_is_stored_as_one_market_aggregate_row() -> None:
    db = _session()
    _seed_calendar(db, [date(2026, 10, 20)])
    _service(FakeDataSource()).collect_history(
        db, start_date=date(2026, 10, 20), end_date=date(2026, 10, 20)
    )

    rows = list(
        db.scalars(
            select(AlternativeDataSnapshot).where(
                AlternativeDataSnapshot.dataset == "limit_up_statistics"
            )
        )
    )
    assert len(rows) == 1
    assert rows[0].item_key == "market"
    assert float(rows[0].value) == pytest.approx(0.64)
    db.close()


def test_every_registered_history_dataset_has_a_fetcher() -> None:
    assert set(HISTORY_FETCHERS) == set(DEFAULT_HISTORY_DATASETS)


class _FlakySource(FakeDataSource):
    """在指定日期上抛异常的端点替身。"""

    def __init__(self, *, fail_dates: set[date]) -> None:
        super().__init__()
        self.fail_dates = fail_dates

    def fetch_limit_up_pool(self, *, date_ms: int | None = None) -> dict:
        if date_ms == 1792425600000:
            raise RuntimeError("upstream hiccup")
        return super().fetch_limit_up_pool(date_ms=date_ms)
