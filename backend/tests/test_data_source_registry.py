"""数据源注册表与通达信独立源的测试。"""

from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.data_sources.tdx_official import TdxOfficialDataSource
from app.models.system import DataSource
from app.repositories.system import DataSourceRepository
from app.services.data_source_registry import (
    DATA_SOURCE_CATALOG,
    DEFAULT_UNREGISTERED_PRIORITY,
    DataSourceRegistryService,
    resolve_source_priority,
)


@pytest.fixture
def db() -> Session:
    engine = create_engine("duckdb:///:memory:")
    with engine.begin() as connection:
        connection.execute(text("CREATE SCHEMA metadata"))
    DataSource.__table__.create(engine)
    with Session(engine) as session:
        yield session


def test_registry_sync_writes_every_known_source(db: Session) -> None:
    service = DataSourceRegistryService()
    priorities = service.sync_catalog(db)

    assert set(priorities) == {spec.source_name for spec in DATA_SOURCE_CATALOG}
    rows = db.scalars(__import__("sqlalchemy").select(DataSource)).all()
    assert len(rows) == len(DATA_SOURCE_CATALOG)


def test_registry_sync_is_idempotent(db: Session) -> None:
    service = DataSourceRegistryService()
    service.sync_catalog(db)
    service.sync_catalog(db)
    service.sync_catalog(db)

    rows = db.scalars(__import__("sqlalchemy").select(DataSource)).all()
    assert len(rows) == len(DATA_SOURCE_CATALOG)


def test_registry_marks_only_genuinely_independent_sources(db: Session) -> None:
    """只有真正独立上游的源才参与跨源对账。

    三个同花顺源共享一个上游，互相印证证明不了任何东西。
    """
    service = DataSourceRegistryService()
    service.sync_catalog(db)

    independent = service.independent_source_names(db)

    assert "tdx_official" in independent
    assert "hithink_finance" not in independent
    assert "hithink_finance_dump" not in independent
    assert "hithink_finance_index" not in independent


def test_unregistered_source_gets_lowest_priority() -> None:
    """未注册的源不能因为"没登记"就自动成为最权威的。"""
    assert resolve_source_priority("hithink_finance") == 50
    assert resolve_source_priority("brand_new_feed") == DEFAULT_UNREGISTERED_PRIORITY


def test_tdx_priority_is_worse_than_hithink_sources() -> None:
    """通达信参与对账但不抢占主源位置。"""
    tdx = resolve_source_priority("tdx_official")

    assert tdx > resolve_source_priority("hithink_finance")
    assert tdx > resolve_source_priority("hithink_finance_dump")
    assert tdx < DEFAULT_UNREGISTERED_PRIORITY


def test_repository_upsert_does_not_reset_omitted_config(db: Session) -> None:
    repository = DataSourceRepository()
    repository.upsert(
        db,
        source_name="tdx_official",
        source_type="vendor",
        priority=70,
        config={"is_independent": True},
    )
    db.commit()

    repository.upsert(
        db,
        source_name="tdx_official",
        source_type="vendor",
        priority=70,
        description="updated",
    )
    db.commit()

    row = repository.get_by_name(db, "tdx_official")
    assert row is not None
    assert row.description == "updated"
    assert row.config == {"is_independent": True}


def test_tdx_adapter_drops_bars_without_traded_price() -> None:
    """未成交的标的应被挡在边界外，而不是进库后被 PRICE_INVALID 拒绝。

    实测 2026-11-10 通达信包里 688053.SH 等 4 只票 open/high/low 均为 0
    （当日无成交），close 却带着发行价。
    """
    source = TdxOfficialDataSource()
    package = SimpleNamespace(
        trade_date=date(2026, 11, 10),
        rows=[
            {
                "symbol": "688053.SH",
                "name": "思科瑞",
                "open": Decimal("0"),
                "high": Decimal("0"),
                "low": Decimal("0"),
                "close": Decimal("37.50"),
                "prev_close": None,
                "volume": Decimal("0"),
                "amount": Decimal("0"),
                "source_url": None,
            },
            {
                "symbol": "600519.SH",
                "name": "贵州茅台",
                "open": Decimal("1200"),
                "high": Decimal("1250"),
                "low": Decimal("1190"),
                "close": Decimal("1242.47"),
                "prev_close": Decimal("1240"),
                "volume": Decimal("1000"),
                "amount": Decimal("1242470"),
                "source_url": None,
            },
        ],
    )

    records = source.to_daily_bar_records(package)

    assert [record.symbol for record in records] == ["600519.SH"]


def test_tdx_adapter_filters_to_allowed_symbols() -> None:
    """通达信独有的 999xxx 指数代码不在项目命名空间内，必须被挡掉。"""
    source = TdxOfficialDataSource()
    package = SimpleNamespace(
        trade_date=date(2026, 11, 10),
        rows=[
            {
                "symbol": "999999.SH",
                "name": "上证指数",
                "open": Decimal("3682"),
                "high": Decimal("3683"),
                "low": Decimal("3660"),
                "close": Decimal("3663.72"),
                "prev_close": None,
                "volume": Decimal("404865177"),
                "amount": Decimal("673951890000"),
                "source_url": None,
            },
            {
                "symbol": "000001.SH",
                "name": "上证指数",
                "open": Decimal("3682"),
                "high": Decimal("3683"),
                "low": Decimal("3660"),
                "close": Decimal("3663.72"),
                "prev_close": None,
                "volume": Decimal("40486517700"),
                "amount": Decimal("673951890000"),
                "source_url": None,
            },
        ],
    )

    records = source.to_daily_bar_records(
        package,
        allowed_symbols={"000001.SH"},
        security_types={"000001.SH": "index"},
    )

    assert [record.symbol for record in records] == ["000001.SH"]
    assert records[0].raw_payload is not None
    assert records[0].raw_payload["security_type"] == "index"
    # 原始成交量原样保留，不做单位换算 —— 口径知识集中在对账层。
    assert records[0].volume == Decimal("40486517700")
