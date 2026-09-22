from datetime import date
from decimal import Decimal

from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session

from app.database.session import apply_schema_compatibility, initialize_database
from app.models.market import DailyBar
from app.models.raw import RawDailyBar
from app.repositories.market import DailyBarRepository


def test_legacy_bar_upgrade_is_idempotent_and_preserves_unknown_tradability(tmp_path):
    engine = create_engine(f"duckdb:///{tmp_path / 'legacy-bars.duckdb'}")
    with engine.begin() as conn:
        for schema in ("raw", "market", "factor"):
            conn.execute(text(f"CREATE SCHEMA {schema}"))
        conn.execute(text("CREATE TABLE factor.factor_run (id BIGINT)"))
        conn.execute(text("CREATE TABLE market.adjustment_factor (id BIGINT)"))
        for table in ("raw.daily_bar", "market.daily_bar"):
            conn.execute(text(f"CREATE TABLE {table} (symbol VARCHAR, close DECIMAL(18, 4))"))
            conn.execute(text(f"INSERT INTO {table} VALUES ('600001.SH', 6.5000)"))
    apply_schema_compatibility(engine)
    with engine.begin() as conn:
        conn.execute(text("UPDATE market.daily_bar SET limit_up=7, is_suspended=true"))
    apply_schema_compatibility(engine)
    with engine.connect() as conn:
        raw = conn.execute(text("SELECT * FROM raw.daily_bar")).mappings().one()
        market = conn.execute(text("SELECT * FROM market.daily_bar")).mappings().one()
        assert raw["symbol"] == market["symbol"] == "600001.SH"
        assert raw["close"] == market["close"]
        assert raw["limit_up"] is None and market["limit_up"] == 7
        assert raw["limit_down"] is market["limit_down"] is None
        assert raw["is_suspended"] is False and market["is_suspended"] is True
        assert raw["special_treatment_status"] is market["special_treatment_status"] is None
    engine.dispose()


def test_tradability_fields_follow_selected_raw_source_into_market(tmp_path):
    engine = create_engine(f"duckdb:///{tmp_path / 'raw-to-market.duckdb'}")
    initialize_database(engine)
    with Session(engine) as db:
        raw = RawDailyBar(
            batch_id=1,
            source_name="fixture",
            source_type="csv",
            source_priority=1,
            symbol="600001.SH",
            security_type="stock",
            trade_date=date(2026, 1, 5),
            adjust_type="none",
            open=Decimal("6"),
            high=Decimal("6"),
            low=Decimal("6"),
            close=Decimal("6"),
            volume=Decimal("0"),
            amount=Decimal("0"),
            limit_up=Decimal("6.6"),
            limit_down=Decimal("5.4"),
            is_suspended=True,
            special_treatment_status="normal",
        )
        db.add(raw)
        db.flush()
        DailyBarRepository().upsert_from_raw(
            db,
            raw_bar=raw,
            quality_score=Decimal("100"),
            quality_status="trusted",
            is_suspect=False,
        )
        db.commit()
        market = db.scalar(select(DailyBar))
        assert market.limit_up == raw.limit_up
        assert market.limit_down == raw.limit_down
        assert market.is_suspended is True
        assert market.special_treatment_status == "normal"
        assert market.selected_raw_id == raw.id
    engine.dispose()
