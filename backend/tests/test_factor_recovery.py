from contextlib import nullcontext
from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import Session

from app.database.base import Base
from app.models.factor import FactorDefinition, FactorRun, FactorValue
from app.models.market import DailyBar, Security, TradingCalendar, ValuationSnapshot
from app.models.system import JobLog
from app.repositories.factor.recovery import FactorRecoveryRepository
from app.repositories.operations import OperationsOverviewRepository
from app.schemas.operations import OperationsActionStartRequest
from app.services import operations_actions
from app.services.factor_recovery import FactorRecoveryService
from app.services.operations_actions import OperationsActionService


@pytest.fixture
def db():
    engine = create_engine("duckdb:///:memory:")
    with engine.begin() as connection:
        for schema in ("factor", "market", "metadata"):
            connection.execute(text(f"CREATE SCHEMA {schema}"))
    Base.metadata.create_all(
        engine,
        tables=[
            FactorDefinition.__table__,
            FactorRun.__table__,
            FactorValue.__table__,
            DailyBar.__table__,
            JobLog.__table__,
            ValuationSnapshot.__table__,
            TradingCalendar.__table__,
            Security.__table__,
        ],
    )
    with Session(engine, expire_on_commit=False) as session:
        yield session
    engine.dispose()


def seed(db, *, factor="ret_1d", version="v1-none", status="success", rows=0):
    definition = FactorDefinition(
        name=factor,
        version=version,
        category="price_volume_risk",
        frequency="daily",
        is_active=True,
        status="active",
        pit_status="point_in_time",
    )
    db.add(definition)
    db.flush()
    original = FactorRun(
        definition_id=definition.id,
        factor_name=factor,
        factor_version=version,
        parameters={
            "target_start_date": "2026-11-02",
            "target_end_date": "2026-11-03",
            "symbols": ["600000.SH"],
            "adjust_type": "none",
            "quality_status": "trusted",
        },
        input_start_date=date(2026, 10, 1),
        input_end_date=date(2026, 11, 3),
        code_version="previous-code",
        release_id="previous-release",
        status=status,
        rows_written=rows,
    )
    db.add(original)
    for day in (2, 3):
        db.add(TradingCalendar(exchange="SH", trade_date=date(2026, 11, day), is_open=True))
    for day in (1, 2, 3):
        price = Decimal(10 + day)
        db.add(
            DailyBar(
                symbol="600000.SH",
                security_type="stock",
                trade_date=date(2026, 11, day),
                adjust_type="none",
                open=price,
                high=price,
                low=price,
                close=price,
                volume=Decimal(1000),
                amount=Decimal(10000),
                quality_status="trusted",
                source_name="test",
                source_count=1,
                quality_score=Decimal(100),
                is_suspect=False,
            )
        )
    db.commit()
    return original, definition


def test_recovery_restores_target_window_and_creates_new_actual_provenance(db):
    original, _ = seed(db)
    service = FactorRecoveryService()
    context, checks = service.inspect(db, original.id)
    assert context.start_date == date(2026, 11, 2)
    assert context.input_start_date == date(2026, 10, 30)
    assert checks["reason_codes"] == []
    result, status = service.execute(db, original_run_id=original.id, action_id=77)
    db.commit()
    assert status == "success"
    assert result["new_run_id"] != original.id
    assert result["recheck"]["first_trade_date"] == "2026-11-02"
    assert result["recheck"]["rows"] == 2
    new = db.get(FactorRun, result["new_run_id"])
    assert new.code_version == operations_actions.settings.app_code_version
    assert new.release_id is None
    assert new.metadata_["operator_recovery"]["original_run_id"] == original.id
    assert original.rows_written == 0 and original.code_version == "previous-code"
    assert original.release_id == "previous-release"
    assert db.scalar(select(func.count()).select_from(FactorValue)) == 2


@pytest.mark.parametrize("change", ["missing_window", "retired", "old_version", "running"])
def test_recovery_rejects_unsafe_original_context(db, change):
    original, definition = seed(db)
    if change == "missing_window":
        original.parameters = {"symbols": None}
    elif change == "retired":
        definition.status = "retired"
    elif change == "old_version":
        original.factor_version = definition.version = "obsolete-none"
    else:
        original.status = "running"
    db.commit()
    with pytest.raises(ValueError):
        FactorRecoveryService().resolve(db, original.id)


def test_recovery_preflight_blocks_missing_prices_and_short_warmup(db):
    original, _ = seed(db, factor="momentum_20")
    _, checks = FactorRecoveryService().inspect(db, original.id)
    assert "PRICE_WARMUP_INSUFFICIENT" in checks["reason_codes"]
    original.parameters = {**original.parameters, "symbols": ["000001.SZ"]}
    _, checks = FactorRecoveryService().inspect(db, original.id)
    assert "TRUSTED_PRICE_INPUT_MISSING" in checks["reason_codes"]


def test_requested_missing_symbol_cannot_be_hidden_by_other_symbol_prices(db):
    original, _ = seed(db)
    original.parameters = {**original.parameters, "symbols": ["600000.SH", "000001.SZ"]}
    _, checks = FactorRecoveryService().inspect(db, original.id)
    assert checks["missing_price_symbols"] == ["000001.SZ"]
    assert "REQUESTED_SYMBOL_PRICE_MISSING" in checks["reason_codes"]


def test_calendar_date_gap_blocks_a_shortened_price_window(db):
    original, _ = seed(db)
    db.add(TradingCalendar(exchange="SH", trade_date=date(2026, 11, 4), is_open=True))
    original.parameters = {**original.parameters, "target_end_date": "2026-11-04"}
    _, checks = FactorRecoveryService().inspect(db, original.id)
    assert checks["missing_price_dates"] == ["2026-11-04"]
    assert "TRUSTED_PRICE_DATES_MISSING" in checks["reason_codes"]


def test_profile_preflight_blocks_a_window_without_values(db):
    seed(db)
    preview = OperationsActionService().preview(
        db,
        action_type="factor_profile_rebuild",
        parameters={"start_date": "2026-11-02", "end_date": "2026-11-03"},
    )
    assert preview.executable is False
    assert preview.checks["missing_count"] == 1


def test_empty_output_is_not_a_closed_problem(db, monkeypatch):
    original, _ = seed(db)
    service = FactorRecoveryService()
    original_context, checks = service.inspect(db, original.id)

    class EmptyCalculator:
        def __init__(self, **kwargs):
            pass

        def run(self, db, **kwargs):
            new = FactorRun(
                definition_id=original.definition_id,
                factor_name=original.factor_name,
                factor_version=original.factor_version,
                parameters=original.parameters,
                code_version=kwargs["code_version"],
                status="success",
                rows_written=0,
            )
            db.add(new)
            db.flush()
            return SimpleNamespace(run_ids={original.factor_name: new.id})

    original_context.factory = EmptyCalculator
    monkeypatch.setattr(service, "inspect", lambda *args: (original_context, checks))
    result, status = service.execute(db, original_run_id=original.id, action_id=1)
    assert status == "partial" and result["problem_closed"] is False
    assert result["availability"] == "empty"


def test_zero_observation_runs_enter_failures_but_later_positive_output_clears_alert(db):
    original, _ = seed(db)
    repository = OperationsOverviewRepository()
    assert repository.get_failed_factor_run(db, run_id=original.id).id == original.id
    assert repository.count_latest_failed_factor_runs(db) == 1
    newer = FactorRun(
        definition_id=original.definition_id,
        factor_name=original.factor_name,
        factor_version=original.factor_version,
        code_version="new",
        parameters={},
        status="success",
        rows_written=2,
        started_at=datetime.now(UTC),
    )
    db.add(newer)
    db.commit()
    assert repository.count_latest_failed_factor_runs(db) == 0
    assert repository.count_failed_factor_runs(db) == 1


def test_research_only_valuation_rows_do_not_satisfy_generic_factor_inputs(db):
    db.add(
        ValuationSnapshot(
            symbol="600000.SH",
            trade_date=date(2026, 11, 2),
            source_name="eastmoney",
            currency="CNY",
            raw_payload={"_research_batch_id": 9},
        )
    )
    db.commit()
    result = FactorRecoveryRepository().dependency_counts(
        db,
        start_date=date(2026, 11, 1),
        end_date=date(2026, 11, 3),
        symbols=None,
        valuation=True,
    )
    assert result["generic_valuations"] == 0


def test_action_idempotency_reads_all_history_before_rechecking_inputs(db, monkeypatch):
    service = OperationsActionService()
    request_hash = service._request_hash("daily_data_sync", {})
    for index in range(105):
        db.add(
            JobLog(
                job_name="operator_action",
                job_type="repair",
                status="success",
                metadata_={
                    "idempotency_key": f"request-{index:04}",
                    "request_hash": request_hash,
                    "action_type": "daily_data_sync",
                    "parameters": {},
                },
            )
        )
    db.commit()
    monkeypatch.setattr(operations_actions, "duckdb_write_lock", lambda *a: nullcontext())
    monkeypatch.setattr(service, "preview", lambda *a, **kw: pytest.fail("must reuse prior action"))
    result = service.start(
        db,
        request=OperationsActionStartRequest(
            action_type="daily_data_sync",
            parameters={},
            idempotency_key="request-0000",
        ),
    )
    assert result.action_id == 1
    assert db.scalar(select(func.count()).select_from(JobLog)) == 105


def test_preview_rejects_overridden_factor_context():
    with pytest.raises(ValueError, match="only accepts"):
        OperationsActionService()._normalize_parameters(
            "factor_values_rebuild",
            {"original_run_id": 1, "start_date": "2020-01-01"},
        )


@pytest.mark.parametrize(
    "sources,remaining_alerts", [(["eastmoney"], 1), (["eastmoney", "hithink"], 0)]
)
def test_source_recovery_creates_new_scoped_business_audit(
    db, monkeypatch, sources, remaining_alerts
):
    from app.services import eastmoney_collection, hithink_alternative_collection
    from app.services.hithink_alternative_collection import AlternativeCollectionResult

    original = JobLog(job_name="alternative_data_collection", job_type="schedule", status="failed")
    db.add(original)
    db.commit()
    result = AlternativeCollectionResult("fresh-batch", 1, 10, 0)

    def factory():
        return SimpleNamespace(collect_current=lambda db: result)

    monkeypatch.setattr(eastmoney_collection, "EastmoneyFlowCollectionService", factory)
    monkeypatch.setattr(
        hithink_alternative_collection, "HithinkAlternativeCollectionService", factory
    )
    service = OperationsActionService(session_factory=lambda: nullcontext(None))
    payload, status = service._execute_action(
        db,
        action_id=77,
        action_type="alternative_data_sync",
        parameters={"sources": sources},
    )
    db.commit()
    assert status == "success" and original.status == "failed"
    assert payload["collection_job_id"] != original.id
    new = db.get(JobLog, payload["collection_job_id"])
    assert new.batch_id == 77
    assert new.metadata_["requested_sources"] == sources
    assert OperationsOverviewRepository().count_latest_failed_jobs(db) == remaining_alerts
