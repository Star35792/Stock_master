from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.database.base import Base
from app.models.factor import FactorProfile
from app.services import FactorBatchProfilerService, FactorCrossSectionService
from app.services.factor_cross_section import CrossSectionRunResult


class FakeSession:
    def __init__(self) -> None:
        self.commits = 0
        self.rollbacks = 0

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1


class FakeCrossSectionService:
    def __init__(self, failing: set[str] | None = None) -> None:
        self.failing = failing or set()
        self.calls: list[dict] = []

    def run(self, db: object, **kwargs: object) -> CrossSectionRunResult:
        self.calls.append(kwargs)
        name = str(kwargs["factor_name"])
        if name in self.failing:
            raise RuntimeError(f"boom:{name}")
        return CrossSectionRunResult(
            source_factor_name=name,
            source_factor_version=str(kwargs["factor_version"]),
            profile_rows=3,
            rows_by_transform={"winsor": 4, "zscore": 4},
            run_ids={"winsor": 1, "zscore": 2},
        )


class FakeDefinitionRepository:
    def __init__(self, definitions: list[SimpleNamespace]) -> None:
        self.definitions = definitions

    def list(self, db: object, **kwargs: object) -> tuple[list[SimpleNamespace], int]:
        return self.definitions, len(self.definitions)


class FakeProfileRepository:
    def __init__(self, rows: list[dict] | None = None) -> None:
        self.rows = rows or []

    def summarize(
        self, db: object, **kwargs: object
    ) -> tuple[list[dict], int]:
        return self.rows, len(self.rows)


class FakeJobLogRepository:
    def __init__(self) -> None:
        self.created: list[dict] = []
        self.finished: list[dict] = []

    def create(self, db: object, **kwargs: object) -> SimpleNamespace:
        self.created.append(kwargs)
        return SimpleNamespace(id=1, metadata_=kwargs.get("metadata"))

    def finish(self, db: object, **kwargs: object) -> SimpleNamespace:
        self.finished.append(kwargs)
        return SimpleNamespace(**kwargs)


def _definition(name: str, category: str, version: str = "v1-qfq") -> SimpleNamespace:
    return SimpleNamespace(name=name, category=category, version=version)


def _service(
    *,
    definitions: list[SimpleNamespace],
    failing: set[str] | None = None,
    profile_rows: list[dict] | None = None,
) -> tuple[FactorBatchProfilerService, FakeCrossSectionService, FakeJobLogRepository]:
    cross_section = FakeCrossSectionService(failing=failing)
    job_log = FakeJobLogRepository()
    service = FactorBatchProfilerService(
        cross_section_service=cross_section,  # type: ignore[arg-type]
        definition_repository=FakeDefinitionRepository(definitions),  # type: ignore[arg-type]
        profile_repository=FakeProfileRepository(profile_rows),  # type: ignore[arg-type]
        job_log_repository=job_log,  # type: ignore[arg-type]
    )
    return service, cross_section, job_log


def test_batch_profiler_runs_active_factors_and_skips_derived() -> None:
    definitions = [
        _definition("ret_1d", "price_volume_risk"),
        _definition("roe_ttm", "financial"),
        _definition("ret_1d__rank", "price_volume_risk"),  # derived transform
    ]
    service, cross_section, job_log = _service(definitions=definitions)
    db = FakeSession()

    result = service.run(
        db,
        start_date=date(2026, 7, 1),
        end_date=date(2026, 7, 17),
        adjust_type="qfq",
    )

    assert result.success_count == 2
    assert result.failed_count == 0
    assert result.total_profile_rows == 6
    assert result.total_transformed_rows == 16
    assert {call["factor_name"] for call in cross_section.calls} == {"ret_1d", "roe_ttm"}
    assert job_log.created[0]["job_name"] == "factor_batch_profiler"
    assert job_log.created[0]["metadata"]["candidate_count"] == 2
    assert job_log.created[0]["metadata"]["size_factor_name"] == "market_cap"
    assert job_log.created[0]["metadata"]["size_factor_version"] == "v1"
    assert {call["size_factor_name"] for call in cross_section.calls} == {"market_cap"}
    assert {call["size_factor_version"] for call in cross_section.calls} == {"v1"}
    finished = job_log.finished[0]
    assert finished["status"] == "success"
    assert finished["rows_failed"] == 0


def test_batch_profiler_filters_categories() -> None:
    definitions = [
        _definition("ret_1d", "price_volume_risk"),
        _definition("roe_ttm", "financial"),
        _definition("crowding", "crowding"),
    ]
    service, cross_section, _ = _service(definitions=definitions)

    result = service.run(
        FakeSession(),
        start_date=date(2026, 7, 1),
        end_date=date(2026, 7, 17),
        adjust_type="qfq",
        categories={"financial"},
        excluded_categories={"crowding"},
    )

    assert [call["factor_name"] for call in cross_section.calls] == ["roe_ttm"]
    assert result.success_count == 1


def test_batch_profiler_isolates_factor_failure() -> None:
    definitions = [
        _definition("ret_1d", "price_volume_risk"),
        _definition("roe_ttm", "financial"),
    ]
    service, _, job_log = _service(definitions=definitions, failing={"roe_ttm"})
    db = FakeSession()

    result = service.run(
        db,
        start_date=date(2026, 7, 1),
        end_date=date(2026, 7, 17),
        adjust_type="qfq",
    )

    assert result.success_count == 1
    assert result.failed_count == 1
    assert result.failed_factors == [("roe_ttm", "v1-qfq", "RuntimeError: boom:roe_ttm")]
    assert db.rollbacks == 1
    finished = job_log.finished[0]
    assert finished["status"] == "partial"
    assert finished["rows_failed"] == 1
    assert finished["metadata"]["failed_factors"][0]["factor_name"] == "roe_ttm"


def test_batch_profiler_rejects_invalid_range() -> None:
    service, _, _ = _service(definitions=[_definition("ret_1d", "price_volume_risk")])
    with pytest.raises(ValueError, match="earlier than or equal"):
        service.run(
            FakeSession(),
            start_date=date(2026, 7, 17),
            end_date=date(2026, 7, 1),
            adjust_type="qfq",
        )


def test_batch_profiler_rejects_size_factor_pair_mismatch() -> None:
    service, _, _ = _service(definitions=[_definition("ret_1d", "price_volume_risk")])
    with pytest.raises(ValueError, match="provided together"):
        service.run(
            FakeSession(),
            start_date=date(2026, 7, 1),
            end_date=date(2026, 7, 17),
            adjust_type="qfq",
            size_factor_name="market_cap",
            size_factor_version=None,
        )


def test_batch_profiler_summarize_maps_rows() -> None:
    rows = [
        {
            "factor_name": "ret_1d",
            "factor_version": "v1-qfq",
            "trade_date_count": 12,
            "first_trade_date": date(2026, 7, 1),
            "last_trade_date": date(2026, 7, 17),
            "mean_coverage_rate": Decimal("0.95"),
            "mean_ic_1d": Decimal("0.03"),
            "mean_rank_ic_1d": Decimal("0.04"),
            "mean_ic_5d": Decimal("0.02"),
            "mean_rank_ic_5d": None,
            "mean_ic_20d": None,
            "mean_rank_ic_20d": None,
        }
    ]
    service, _, _ = _service(
        definitions=[_definition("ret_1d", "price_volume_risk")],
        profile_rows=rows,
    )

    summaries, total = service.summarize(FakeSession())

    assert total == 1
    summary = summaries[0]
    assert summary.factor_name == "ret_1d"
    assert summary.trade_date_count == 12
    assert summary.mean_ic_1d == Decimal("0.03")
    assert summary.mean_rank_ic_5d is None
    assert summary.has_ic_evidence is True


def test_batch_profiler_summarize_without_ic_evidence() -> None:
    rows = [
        {
            "factor_name": "ret_1d",
            "factor_version": "v1-qfq",
            "trade_date_count": 5,
            "first_trade_date": date(2026, 7, 1),
            "last_trade_date": date(2026, 7, 17),
            "mean_coverage_rate": Decimal("0.90"),
            "mean_ic_1d": None,
            "mean_rank_ic_1d": None,
            "mean_ic_5d": None,
            "mean_rank_ic_5d": None,
            "mean_ic_20d": None,
            "mean_rank_ic_20d": None,
        }
    ]
    service, _, _ = _service(
        definitions=[_definition("ret_1d", "price_volume_risk")],
        profile_rows=rows,
    )

    summaries, _ = service.summarize(FakeSession())
    assert summaries[0].has_ic_evidence is False


def test_batch_profiler_default_cross_section_service_is_real() -> None:
    service = FactorBatchProfilerService()
    assert isinstance(service.cross_section_service, FactorCrossSectionService)


def test_profile_repository_summarize_aggregates_across_dates() -> None:
    engine = create_engine("duckdb:///:memory:")
    with engine.begin() as connection:
        connection.execute(text("CREATE SCHEMA IF NOT EXISTS factor"))
    Base.metadata.create_all(engine, tables=[FactorProfile.__table__])
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    with factory() as db:
        db.execute(
            text(
                "INSERT INTO factor.factor_profile ("
                "id, factor_name, factor_version, trade_date, universe_size, "
                "observation_count, missing_count, coverage_rate, missing_rate, "
                "extreme_count, ic_1d, rank_ic_1d, ic_5d, rank_ic_5d, code_version"
                ") VALUES "
                "(1, 'ret_1d', 'v1-qfq', DATE '2026-07-01', 100, 90, 10, 0.9, 0.1, "
                "0, 0.02, 0.03, 0.01, 0.01, 'local'), "
                "(2, 'ret_1d', 'v1-qfq', DATE '2026-07-02', 100, 95, 5, 0.95, 0.05, "
                "0, 0.04, 0.05, NULL, NULL, 'local')"
            )
        )
        db.commit()

        service = FactorBatchProfilerService()
        summaries, total = service.summarize(db)

    assert total == 1
    summary = summaries[0]
    assert summary.factor_name == "ret_1d"
    assert summary.trade_date_count == 2
    assert summary.first_trade_date == date(2026, 7, 1)
    assert summary.last_trade_date == date(2026, 7, 2)
    assert summary.mean_coverage_rate == pytest.approx(Decimal("0.925"))
    assert summary.mean_ic_1d == pytest.approx(Decimal("0.03"))
    assert summary.mean_rank_ic_1d == pytest.approx(Decimal("0.04"))
    assert summary.mean_ic_5d == pytest.approx(Decimal("0.01"))
    assert summary.mean_ic_20d is None
    assert summary.has_ic_evidence is True
