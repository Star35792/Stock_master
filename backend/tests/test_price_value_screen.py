import json
from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import create_engine, delete, func, select
from sqlalchemy.orm import Session

from app.data_sources.eastmoney import EastmoneyDataError
from app.database.session import initialize_database
from app.models.factor import FactorRun, FactorValue
from app.models.market import DailyBar, Security, TradingCalendar, ValuationSnapshot
from app.models.raw import ImportBatch, RawVendorSnapshot
from app.models.strategy import BacktestRun
from app.models.system import JobLog
from app.repositories.market import ValuationSnapshotRepository
from app.scheduler.price_value_screen import seconds_until_next_midnight
from app.services.price_value_input_prep import (
    PriceValueInputPrepService,
    select_comparable_roe_period,
)
from app.services.price_value_reported_roe import (
    DATASET as ROE_DATASET,
    ReportedRoe,
    ReportedRoeCollection,
)
from app.services.price_value_screen import PriceValueScreenService

SHANGHAI = ZoneInfo("Asia/Shanghai")
TARGET = date(2026, 9, 21)
NOW = datetime(2026, 9, 22, 0, 1, tzinfo=SHANGHAI)


def test_comparable_roe_period_uses_same_period_coverage_per_exchange():
    recent = date(2026, 9, 30)
    prior = date(2026, 6, 30)
    matched = {
        exchange: {f"{number:06d}.{exchange}" for number in range(100)}
        for exchange in ("SH", "SZ", "BJ")
    }
    period_observations = {recent.isoformat(): {}, prior.isoformat(): {}}
    for symbols in matched.values():
        for rank, symbol in enumerate(sorted(symbols)):
            old = ReportedRoe(
                ratio=Decimal("0.12"), industry=None,
                report_date=prior,
                notice_date=TARGET, disclosed_at=NOW,
                raw_page_hash="test-page",
            )
            period_observations[prior.isoformat()][symbol] = old
            if rank < 70:
                period_observations[recent.isoformat()][symbol] = ReportedRoe(
                    ratio=Decimal("0.13"), industry=None,
                    report_date=recent, notice_date=TARGET,
                    disclosed_at=NOW, raw_page_hash="test-page",
                )
    assert select_comparable_roe_period(
        report_dates=[recent.isoformat(), prior.isoformat()],
        period_observations=period_observations, matched_by_exchange=matched,
    ) == prior.isoformat()

    period_observations[prior.isoformat()] = {
        symbol: observation
        for symbol, observation in period_observations[prior.isoformat()].items()
        if int(symbol[:6]) < 75
    }
    with pytest.raises(ValueError, match="comparable reported ROE coverage"):
        select_comparable_roe_period(
            report_dates=[recent.isoformat(), prior.isoformat()],
            period_observations=period_observations, matched_by_exchange=matched,
        )


def _database(tmp_path):
    engine = create_engine(f"duckdb:///{tmp_path / 'screen.duckdb'}")
    initialize_database(engine)
    with Session(engine) as db:
        for exchange in ("SH", "SZ", "BJ"):
            db.add(TradingCalendar(exchange=exchange, trade_date=TARGET, is_open=True))
        for symbol, industry in (("600001.SH", "食品饮料"), ("600002.SH", "银行")):
            db.add(
                Security(
                    symbol=symbol,
                    name=symbol,
                    security_type="stock",
                    exchange="SH",
                    currency="CNY",
                    industry=industry,
                    is_active=True,
                    list_date=date(2000, 1, 1),
                )
            )
            db.add(
                DailyBar(
                    symbol=symbol,
                    security_type="stock",
                    trade_date=TARGET,
                    adjust_type="none",
                    open=Decimal("10"),
                    high=Decimal("10"),
                    low=Decimal("10"),
                    close=Decimal("10"),
                    volume=Decimal("100000"),
                    amount=Decimal("1000000"),
                    quality_status="trusted",
                    is_suspect=False,
                    is_suspended=False,
                    special_treatment_status="normal",
                    source_name="test",
                )
            )
            db.add(
                ValuationSnapshot(
                    symbol=symbol,
                    trade_date=TARGET,
                    pe_ttm=Decimal("12"),
                    pb=Decimal("1.5"),
                    close=Decimal("10"),
                    currency="CNY",
                    source_name="eastmoney",
                    raw_payload={
                        "f9": 12,
                        "_research_batch_id": 123,
                        "_raw_page_hash": "fixture",
                        "_roe_reported_ratio": "0.12",
                        "_roe_report_date": "2026-06-30",
                        "_roe_notice_date": "2026-08-30",
                        "_roe_raw_page_hash": "roe-fixture",
                    },
                )
            )
        run = FactorRun(
            definition_id=1,
            factor_name="roe_ttm",
            factor_version="v1",
            parameters={},
            code_version="test",
            status="success",
            rows_written=2,
        )
        db.add(run)
        db.flush()
        for symbol in ("600001.SH", "600002.SH"):
            db.add(
                FactorValue(
                    symbol=symbol,
                    trade_date=TARGET,
                    factor_name="roe_ttm",
                    factor_version="v1",
                    value=Decimal("0.12"),
                    run_id=run.id,
                )
            )
        db.add(
            ImportBatch(
                id=123,
                batch_no="fixture",
                source_name="eastmoney",
                source_type="api",
                dataset="price_value_valuation_observation",
                status="success",
            )
        )
        db.add(
            RawVendorSnapshot(
                id=123,
                batch_id=123,
                source_name="eastmoney",
                dataset="price_value_valuation_observation",
                snapshot_date=TARGET,
                raw_payload={},
                row_hash="fixture",
            )
        )
        db.add(RawVendorSnapshot(
            batch_id=123, source_name="eastmoney", dataset=ROE_DATASET,
            snapshot_date=TARGET, raw_payload={}, row_hash="roe-fixture",
        ))
        db.add(
            JobLog(
                job_name="price_value_input_prep",
                job_type="import",
                status="success",
                batch_id=123,
                metadata_={
                    "target_trade_date": TARGET.isoformat(),
                    "batch_id": 123,
                    "raw_page_hashes": ["fixture"],
                    "roe_raw_page_hashes": ["roe-fixture"],
                    "roe_report_dates": ["2026-06-30"],
                },
            )
        )
        db.commit()
    return engine


def test_midnight_schedule_uses_shanghai_date():
    assert seconds_until_next_midnight(datetime(2026, 9, 21, 23, 59, tzinfo=SHANGHAI)) == 60
    assert seconds_until_next_midnight(datetime(2026, 9, 21, 16, tzinfo=ZoneInfo("UTC"))) == 86400


def test_screen_is_read_only_research_and_idempotently_persisted(tmp_path):
    engine = _database(tmp_path)
    service = PriceValueScreenService(output_dir=tmp_path / "screens")
    with Session(engine) as db:
        report = service.run(db, now=NOW)
        repeated = service.run(db, now=NOW)
        assert report["run_id"] == repeated["run_id"]
        assert report["target_trade_date"] == TARGET.isoformat()
        assert report["research_only"] and not report["execution_eligible"]
        assert report["universe_count"] == 2
        assert report["ready_count"] == 1
        assert report["rows"][0]["status"] == "ready_for_review"
        assert "financial_industry_separate_model" in report["rows"][1]["reason_codes"]
        assert service.latest(db)["snapshot_hash"] == report["snapshot_hash"]
        assert service.profit_status(db)["value_backtest"] is None
        assert service.profit_status(db)["paper_accounts"] == []
        assert db.scalar(select(func.count()).select_from(JobLog)) == 2
    engine.dispose()


def test_screen_blocks_cross_date_inputs(tmp_path):
    engine = _database(tmp_path)
    with Session(engine) as db:
        for item in db.scalars(select(ValuationSnapshot)):
            item.trade_date = date(2026, 9, 18)
        db.commit()
        report = PriceValueScreenService(output_dir=tmp_path / "screens").scan(db, now=NOW)
        assert report["status"] == "blocked"
        assert report["ready_count"] == 0
        assert "valuation_missing_for_target" in report["global_reason_codes"]
    engine.dispose()


def test_screen_uses_reported_roe_only_when_raw_page_matches(tmp_path):
    engine = _database(tmp_path)
    with Session(engine) as db:
        for row in db.scalars(select(ValuationSnapshot)):
            row.raw_payload = {
                **row.raw_payload,
                "_roe_reported_ratio": "0.145",
                "_roe_report_date": "2026-06-30",
                "_roe_notice_date": "2026-08-30",
                "_roe_raw_page_hash": "roe-fixture",
            }
        db.commit()
        report = PriceValueScreenService(output_dir=tmp_path / "screens").scan(db, now=NOW)
        assert report["ready_count"] == 1
        assert report["rows"][0]["roe_reported_vendor"] == "0.145"
        assert report["rows"][0]["roe_method"] == "vendor_weighted_average"

        snapshot = db.scalar(select(RawVendorSnapshot).where(
            RawVendorSnapshot.dataset == "price_value_reported_roe_observation"
        ))
        snapshot.row_hash = "wrong-hash"
        db.commit()
        blocked = PriceValueScreenService(output_dir=tmp_path / "screens").scan(db, now=NOW)
        assert blocked["ready_count"] == 0
        assert "roe_input_batch_unverified" in blocked["global_reason_codes"]
    engine.dispose()


def test_unverified_or_conflicting_industry_never_enters_ready_list(tmp_path):
    engine = _database(tmp_path)
    with Session(engine) as db:
        first = db.scalar(select(Security).where(Security.symbol == "600001.SH"))
        first.industry = "工业"
        row = db.scalar(select(ValuationSnapshot).where(
            ValuationSnapshot.symbol == "600001.SH"
        ))
        row.raw_payload = {**row.raw_payload, "_industry_reported": "银行"}
        db.commit()
        report = PriceValueScreenService(output_dir=tmp_path / "screens").scan(
            db, now=NOW
        )
        assert report["ready_count"] == 0
        assert "industry_source_conflict" in report["rows"][0]["reason_codes"]
        assert "financial_industry_separate_model" in report["rows"][0]["reason_codes"]
        first.industry = "工业"
        row.raw_payload = {**row.raw_payload, "_industry_reported": "���Т�"}
        db.commit()
        report = PriceValueScreenService(output_dir=tmp_path / "screens").scan(
            db, now=NOW
        )
        assert "industry_source_conflict" not in report["rows"][0]["reason_codes"]
        assert report["rows"][0]["vendor_industry_hint"] is None
        first.industry = "-"
        row.raw_payload = {**row.raw_payload, "_industry_reported": "食品饮料"}
        db.commit()
        report = PriceValueScreenService(output_dir=tmp_path / "screens").scan(
            db, now=NOW
        )
        assert "industry_missing" in report["rows"][0]["reason_codes"]
        assert report["rows"][0]["vendor_industry_hint"] == "食品饮料"
        first.industry = "券商"
        row.raw_payload = {**row.raw_payload, "_industry_reported": "券商"}
        db.commit()
        report = PriceValueScreenService(output_dir=tmp_path / "screens").scan(
            db, now=NOW
        )
        assert "financial_industry_separate_model" in report["rows"][0]["reason_codes"]
    engine.dispose()


def test_older_roe_report_period_is_separate_review(tmp_path):
    engine = _database(tmp_path)
    with Session(engine) as db:
        row = db.scalar(select(ValuationSnapshot).where(
            ValuationSnapshot.symbol == "600001.SH"
        ))
        row.raw_payload = {**row.raw_payload, "_roe_report_date": "2026-03-31"}
        db.commit()
        report = PriceValueScreenService(output_dir=tmp_path / "screens").scan(
            db, now=NOW
        )
        assert report["ready_count"] == 0
        assert "roe_report_period_not_comparable" in report["rows"][0]["reason_codes"]
    engine.dispose()


class _Vendor:
    name = "eastmoney"
    source_type = "api"

    def __init__(self, *, fail_after_page=False):
        self.fail_after_page = fail_after_page
        self.calls = 0

    def iter_valuation_snapshot_pages(self):
        self.calls += 1
        items = [
            {"f12": "600001", "f13": 1, "f2": 10, "f9": 12, "f23": 1.5, "f20": 1000, "f21": 500},
            {"f12": "600002", "f13": 1, "f2": 10, "f9": 9, "f23": 1.1, "f20": 900, "f21": 400},
        ]
        yield 1, 2, items, json.dumps({"data": {"total": 2, "diff": items}})
        if self.fail_after_page:
            raise RuntimeError("vendor pagination failed")


class _ReportedRoe:
    def collect_into_batch(self, db, *, batch_id, target, observed_at):
        db.add(RawVendorSnapshot(
            batch_id=batch_id, source_name="eastmoney", dataset=ROE_DATASET,
            snapshot_date=target, raw_payload={}, row_hash="test-roe-page",
        ))
        db.commit()
        observations = {
            symbol: ReportedRoe(
                ratio=Decimal("0.12"), industry=industry,
                report_date=date(2026, 6, 30), notice_date=date(2026, 8, 30),
                disclosed_at=observed_at, raw_page_hash="test-roe-page",
            )
            for symbol, industry in (
                ("600001.SH", "食品饮料"), ("600002.SH", "银行")
            )
        }
        return ReportedRoeCollection(
            observations=observations,
            period_observations={"2026-06-30": observations},
            raw_page_hashes=["test-roe-page"],
            source_rows=2, report_dates=["2026-06-30"],
        )


def _prep_service(*, source, **kwargs):
    return PriceValueInputPrepService(
        source=source, reported_roe_service=_ReportedRoe(),
        clock=lambda: datetime(2026, 9, 21, 21, 1, tzinfo=SHANGHAI),
        **kwargs,
    )


class _Factor:
    def run(self, db, **kwargs):
        assert kwargs["adjust_type"] == "none"
        assert kwargs["factor_names"] == ["roe_ttm"]
        run = FactorRun(
            definition_id=1,
            factor_name="roe_ttm",
            factor_version="v1",
            parameters={},
            code_version="test",
            status="success",
            rows_written=2,
        )
        db.add(run)
        db.flush()
        for symbol in ("600001.SH", "600002.SH"):
            db.add(
                FactorValue(
                    symbol=symbol,
                    trade_date=TARGET,
                    factor_name="roe_ttm",
                    factor_version="v1",
                    value=Decimal("0.12"),
                    run_id=run.id,
                )
            )

        class Result:
            rows_by_factor = {"roe_ttm": 2}
            run_ids = {"roe_ttm": run.id}

        return Result()


def _prep_database(tmp_path):
    engine = _database(tmp_path)
    with Session(engine) as db:
        for model in (
            FactorValue,
            FactorRun,
            ValuationSnapshot,
            JobLog,
            RawVendorSnapshot,
            ImportBatch,
        ):
            db.execute(delete(model))
        db.add(
            JobLog(
                job_name="auto_data_sync",
                job_type="import",
                status="success",
                metadata_={"end_date": TARGET.isoformat()},
            )
        )
        db.commit()
    return engine


def test_evening_input_prep_keeps_raw_lineage_and_is_idempotent(tmp_path):
    engine = _prep_database(tmp_path)
    vendor = _Vendor()
    prep = _prep_service(source=vendor)
    with Session(engine) as db:
        first = prep.run(db, now=datetime(2026, 9, 21, 21, 1, tzinfo=SHANGHAI))
        second = prep.run(db, now=datetime(2026, 9, 21, 22, 1, tzinfo=SHANGHAI))
        assert first == second
        assert vendor.calls == 1
        assert db.scalar(select(func.count()).select_from(RawVendorSnapshot)) == 2
        assert db.scalar(select(ImportBatch.status)) == "success"
        assert db.scalar(select(func.count()).select_from(ValuationSnapshot)) == 2
        assert db.scalar(select(ValuationSnapshot.pe_ttm)) is None
        assert db.scalar(select(func.count()).select_from(FactorRun)) == 0
        assert db.scalar(select(func.count()).select_from(FactorValue)) == 0
        report = PriceValueScreenService(output_dir=tmp_path / "screens").scan(db, now=NOW)
        assert report["input_batch_id"] == first["batch_id"]
        assert report["ready_count"] == 1
        assert report["rows"][0]["pe_vendor_unverified"] == "12"
        assert report["status"] == "partial"
        db.add(
            RawVendorSnapshot(
                batch_id=first["batch_id"], source_name="eastmoney", dataset="other",
                snapshot_date=TARGET, raw_payload={}, row_hash="later",
            )
        )
        db.add(
            ValuationSnapshot(
                symbol="600003.SH", trade_date=TARGET, currency="CNY",
                source_name="eastmoney", raw_payload={},
            )
        )
        db.commit()  # Normal sequence inserts still work after the research batch.
    engine.dispose()


def test_input_prep_uses_common_roe_period_and_shows_newer_report(tmp_path):
    engine = _prep_database(tmp_path)

    class MixedRoe(_ReportedRoe):
        def collect_into_batch(self, db, *, batch_id, target, observed_at):
            base = super().collect_into_batch(
                db, batch_id=batch_id, target=target, observed_at=observed_at,
            )
            recent = base.observations["600001.SH"]
            prior = {
                symbol: ReportedRoe(
                    ratio=Decimal("0.08"), industry=observation.industry,
                    report_date=date(2026, 3, 31), notice_date=observation.notice_date,
                    disclosed_at=observation.disclosed_at,
                    raw_page_hash=observation.raw_page_hash,
                )
                for symbol, observation in base.observations.items()
            }
            return ReportedRoeCollection(
                observations={"600001.SH": recent, "600002.SH": prior["600002.SH"]},
                period_observations={
                    "2026-06-30": {"600001.SH": recent},
                    "2026-03-31": prior,
                },
                raw_page_hashes=base.raw_page_hashes,
                source_rows=3,
                report_dates=["2026-06-30", "2026-03-31"],
            )

    prep = PriceValueInputPrepService(
        source=_Vendor(), reported_roe_service=MixedRoe(),
        clock=lambda: datetime(2026, 9, 21, 21, 1, tzinfo=SHANGHAI),
    )
    with Session(engine) as db:
        result = prep.run(db, now=datetime(2026, 9, 21, 21, 1, tzinfo=SHANGHAI))
        assert result["roe_report_dates"][0] == "2026-03-31"
        report = PriceValueScreenService(output_dir=tmp_path / "screens").scan(
            db, now=NOW,
        )
        first = report["rows"][0]
        assert first["status"] == "ready_for_review"
        assert first["roe_report_date"] == "2026-03-31"
        assert first["roe_reported_vendor"] == "0.08"
        assert first["roe_newer_report_date"] == "2026-06-30"
        assert first["roe_newer_reported_vendor"] == "0.12"
        assert first["roe_newer_raw_page_hash"] == "test-roe-page"
        market_row = db.scalar(select(ValuationSnapshot).where(
            ValuationSnapshot.symbol == "600001.SH"
        ))
        market_row.raw_payload = {
            **market_row.raw_payload, "_roe_newer_raw_page_hash": "invalid-hash",
        }
        db.commit()
        blocked = PriceValueScreenService(output_dir=tmp_path / "screens").scan(
            db, now=NOW,
        )
        first = blocked["rows"][0]
        assert first["roe_newer_reported_vendor"] is None
        assert "roe_observation_lineage_unverified" in first["reason_codes"]
    engine.dispose()


def test_same_evening_dated_fallback_preserves_price_gate(tmp_path):
    engine = _prep_database(tmp_path)

    class DatedVendor(_Vendor):
        def iter_valuation_snapshot_pages(self):
            raise EastmoneyDataError("current clist unavailable")
            yield  # pragma: no cover

        def iter_same_day_dated_valuation_pages(self, *, trade_date):
            assert trade_date == TARGET
            items = [
                {
                    "f12": "600001", "f13": 1, "f2": 10, "f9": 12,
                    "f23": 1.5, "f20": 1000, "f21": 500,
                    "_supplier_endpoint": "RPT_VALUEANALYSIS_DET",
                    "_supplier_payload": {"BOARD_NAME": "食品饮料"},
                },
                {
                    "f12": "600002", "f13": 1, "f2": 10, "f9": 9,
                    "f23": 1.1, "f20": 900, "f21": 400,
                    "_supplier_endpoint": "RPT_VALUEANALYSIS_DET",
                    "_supplier_payload": {"BOARD_NAME": "银行"},
                },
            ]
            yield 1, 2, items, json.dumps({"result": {"count": 2, "data": items}})

    with Session(engine) as db:
        result = _prep_service(source=DatedVendor()).run(
            db, now=datetime(2026, 9, 21, 21, 1, tzinfo=SHANGHAI)
        )
        assert result["valuation_mode"] == "same_day_dated_fallback"
        assert db.scalar(select(func.count()).select_from(ValuationSnapshot)) == 2
    engine.dispose()


def test_existing_unproven_market_row_is_archived_and_replaced(tmp_path):
    engine = _prep_database(tmp_path)
    with Session(engine) as db:
        db.add(
            ValuationSnapshot(
                symbol="600001.SH",
                trade_date=TARGET,
                pe_ttm=Decimal("99"),
                pb=Decimal("8"),
                close=Decimal("9"),
                total_market_cap=Decimal("123456"),
                currency="CNY",
                source_name="eastmoney",
                raw_payload={"legacy": True},
            )
        )
        db.commit()
        prep = _prep_service(source=_Vendor())
        result = prep.run(db, now=datetime(2026, 9, 21, 21, 1, tzinfo=SHANGHAI))
        current = db.scalar(
            select(ValuationSnapshot).where(ValuationSnapshot.symbol == "600001.SH")
        )
        assert current.raw_payload["_research_batch_id"] == result["batch_id"]
        assert current.pe_ttm is None
        archived = db.scalar(
            select(RawVendorSnapshot).where(
                RawVendorSnapshot.dataset == "price_value_replaced_market_rows"
            )
        )
        assert archived.raw_payload["previous_rows"][0]["raw_payload"] == {"legacy": True}
        assert archived.raw_payload["previous_rows"][0]["total_market_cap"] == "123456.000000"
        assert PriceValueScreenService(output_dir=tmp_path / "screens").scan(
            db, now=NOW
        )["ready_count"] == 1
    engine.dispose()


def test_beijing_market_gap_blocks_input_prep(tmp_path):
    engine = _prep_database(tmp_path)
    with Session(engine) as db:
        db.add(
            Security(
                symbol="920992.BJ", name="BJ test", security_type="stock", exchange="BJ",
                currency="CNY", industry="工业", is_active=True, list_date=TARGET,
            )
        )
        db.add(
            DailyBar(
                symbol="920992.BJ", security_type="stock", trade_date=TARGET,
                adjust_type="none", open=Decimal("10"), high=Decimal("10"),
                low=Decimal("10"), close=Decimal("10"), volume=Decimal("100000"),
                amount=Decimal("1000000"), quality_status="trusted", is_suspect=False,
                is_suspended=False, special_treatment_status="normal", source_name="test",
            )
        )
        db.commit()
        prep = _prep_service(source=_Vendor())
        try:
            prep.run(db, now=datetime(2026, 9, 21, 21, 1, tzinfo=SHANGHAI))
        except ValueError as exc:
            assert "BJ research input coverage" in str(exc)
        else:
            raise AssertionError("expected Beijing coverage gate")
        assert db.scalar(select(func.count()).select_from(ValuationSnapshot)) == 0
    engine.dispose()


def test_failed_publish_rolls_back_market_and_retries(tmp_path):
    engine = _prep_database(tmp_path)

    class BrokenValuation(ValuationSnapshotRepository):
        def insert_research_rows(self, db, *, rows):
            super().insert_research_rows(db, rows=rows)
            raise RuntimeError("after market insert")

    with Session(engine) as db:
        failed = _prep_service(source=_Vendor(), valuation_repository=BrokenValuation())
        try:
            failed.run(db, now=datetime(2026, 9, 21, 21, 1, tzinfo=SHANGHAI))
        except RuntimeError as exc:
            assert "after market insert" in str(exc)
        else:
            raise AssertionError("expected injected publish failure")
        assert db.scalar(select(func.count()).select_from(ValuationSnapshot)) == 0
        assert db.scalar(select(func.count()).select_from(FactorRun)) == 0
        assert db.scalar(select(func.count()).select_from(RawVendorSnapshot)) == 2
        recovered = _prep_service(source=_Vendor())
        result = recovered.run(db, now=datetime(2026, 9, 21, 21, 31, tzinfo=SHANGHAI))
        assert result["accepted_count"] == 2
        assert db.scalar(select(func.count()).select_from(ValuationSnapshot)) == 2
    engine.dispose()


def test_failed_final_commit_marks_raw_batch_failed(tmp_path, monkeypatch):
    engine = _prep_database(tmp_path)
    with Session(engine) as db:
        real_commit = db.commit
        injected = False

        def failing_commit():
            nonlocal injected
            finishing = any(
                isinstance(row, ImportBatch) and row.status == "success"
                for row in db.identity_map.values()
            )
            if finishing and not injected:
                injected = True
                raise RuntimeError("final commit failed")
            real_commit()

        monkeypatch.setattr(db, "commit", failing_commit)
        prep = _prep_service(source=_Vendor())
        try:
            prep.run(db, now=datetime(2026, 9, 21, 21, 1, tzinfo=SHANGHAI))
        except RuntimeError as exc:
            assert "final commit failed" in str(exc)
        else:
            raise AssertionError("expected injected final commit failure")
        assert injected
        assert db.scalar(select(ImportBatch.status)) == "failed"
        assert db.scalar(select(func.count()).select_from(ValuationSnapshot)) == 0
        assert db.scalar(
            select(JobLog.status).where(JobLog.job_name == "price_value_input_prep")
        ) == "failed"
    engine.dispose()


def test_failed_screen_run_is_visible_as_blocked_instead_of_missing(tmp_path, monkeypatch):
    engine = _database(tmp_path)
    service = PriceValueScreenService(output_dir=tmp_path / "screens")

    def broken_scan(*args, **kwargs):
        raise ValueError("broken fixture")

    monkeypatch.setattr(service, "scan", broken_scan)
    with Session(engine) as db:
        try:
            service.run(db, now=NOW)
        except ValueError:
            pass
        else:
            raise AssertionError("expected screen failure")
        latest = service.latest(db)
        assert latest["status"] == "blocked"
        assert latest["global_reason_codes"] == ["screen_job_failed"]
        assert latest["error_message"].startswith("筛选任务失败")
        assert latest["rows"] == []
        assert latest["current_coverage_by_exchange"] == {}
        assert latest["latest_daily_sync"] is None or "status" in latest["latest_daily_sync"]
        assert latest["roe_reported_count"] is None
    engine.dispose()


def test_screen_recovers_after_failed_job_with_unchanged_inputs(tmp_path, monkeypatch):
    engine = _database(tmp_path)
    service = PriceValueScreenService(output_dir=tmp_path / "screens")
    with Session(engine) as db:
        first = service.run(db, now=NOW)
        original_scan = service.scan

        def broken_scan(*args, **kwargs):
            raise ValueError("temporary failure")

        monkeypatch.setattr(service, "scan", broken_scan)
        try:
            service.run(db, now=NOW)
        except ValueError:
            pass
        else:
            raise AssertionError("expected temporary failure")
        assert service.latest(db)["status"] == "blocked"
        monkeypatch.setattr(service, "scan", original_scan)
        recovered = service.run(db, now=NOW)
        assert recovered["run_id"] != first["run_id"]
        assert service.latest(db)["run_id"] == recovered["run_id"]
        assert recovered["ready_count"] == 1
    engine.dispose()


def test_zero_reviewable_candidates_are_blocked(tmp_path):
    engine = _database(tmp_path)
    with Session(engine) as db:
        for row in db.scalars(select(ValuationSnapshot)):
            row.raw_payload = {**row.raw_payload, "f9": None}
        db.commit()
        report = PriceValueScreenService(output_dir=tmp_path / "screens").scan(db, now=NOW)
        assert report["ready_count"] == 0
        assert report["status"] == "blocked"
        assert report["global_reason_codes"] == ["no_reviewable_candidates"]
    engine.dispose()


def test_failed_evening_fetch_preserves_raw_and_blocks_midnight(tmp_path):
    engine = _prep_database(tmp_path)
    prep = _prep_service(source=_Vendor(fail_after_page=True))
    with Session(engine) as db:
        try:
            prep.run(db, now=datetime(2026, 9, 21, 21, 1, tzinfo=SHANGHAI))
        except RuntimeError as exc:
            assert "pagination failed" in str(exc)
        else:
            raise AssertionError("expected vendor failure")
        assert db.scalar(select(func.count()).select_from(RawVendorSnapshot)) == 1
        assert db.scalar(select(ImportBatch.status)) == "failed"
        assert db.scalar(
            select(JobLog.status).where(JobLog.job_name == "price_value_input_prep")
        ) == "failed"
        report = PriceValueScreenService(output_dir=tmp_path / "screens").scan(db, now=NOW)
        assert report["status"] == "blocked"
        assert "input_preparation_missing_for_target" in report["global_reason_codes"]
    engine.dispose()


def test_evening_valuation_page_crossing_midnight_cannot_publish(tmp_path):
    engine = _prep_database(tmp_path)
    after_midnight = datetime(2026, 9, 22, 0, 1, tzinfo=SHANGHAI)
    prep = PriceValueInputPrepService(
        source=_Vendor(), reported_roe_service=_ReportedRoe(),
        clock=lambda: after_midnight,
    )
    with Session(engine) as db:
        try:
            prep.run(db, now=datetime(2026, 9, 21, 23, 59, tzinfo=SHANGHAI))
        except ValueError as exc:
            assert "crossed its target trading date" in str(exc)
        else:
            raise AssertionError("expected midnight capture gate")
        assert db.scalar(select(func.count()).select_from(RawVendorSnapshot)) == 1
        assert db.scalar(select(func.count()).select_from(ValuationSnapshot)) == 0
        assert db.scalar(select(ImportBatch.status)) == "failed"
    engine.dispose()


def test_profit_status_reports_existing_hypothetical_run_exactly(tmp_path):
    engine = _database(tmp_path)
    with Session(engine) as db:
        db.add(
            BacktestRun(
                name="research",
                strategy_name="price_value_assumption_v1",
                parameters={},
                code_version="test",
                data_version="test",
                start_date=TARGET,
                end_date=TARGET,
                initial_cash=Decimal("100000"),
                final_equity=Decimal("120000.1250"),
                total_return=Decimal("0.20000125"),
                status="success",
                finished_at=NOW,
            )
        )
        db.commit()
        status = PriceValueScreenService(output_dir=tmp_path / "screens").profit_status(db)
        assert Decimal(status["value_backtest"]["hypothetical_profit"]) == Decimal(
            "20000.1250"
        )
        assert status["value_backtest"]["pit_verified"] is False
        assert status["paper_accounts"] == []
    engine.dispose()
