from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.database.session import initialize_database
from app.models.portfolio import PortfolioPlanRecord
from app.portfolio import PortfolioConstraints, PortfolioPlan
from app.portfolio.style_risk import PortfolioStyleRisk, StyleExposure
from app.repositories.portfolio import PortfolioPlanRepository
from app.services.portfolio import PortfolioConstructionService, hash_portfolio_snapshot


def test_plan_listing_orders_newest_first_and_paginates() -> None:
    engine = create_engine("duckdb:///:memory:")
    try:
        with engine.begin() as connection:
            connection.execute(text("CREATE SCHEMA portfolio"))
            PortfolioPlanRecord.__table__.create(connection)
        with Session(engine) as db:
            for plan_id, created in (
                (1, datetime(2026, 7, 20, tzinfo=UTC)),
                (2, datetime(2026, 7, 21, tzinfo=UTC)),
                (3, datetime(2026, 7, 21, tzinfo=UTC)),
            ):
                db.add(
                    PortfolioPlanRecord(
                        id=plan_id,
                        name=f"plan-{plan_id}",
                        as_of_date=date(2026, 7, 21),
                        parameters={},
                        total_equity=Decimal("1000.0000"),
                        cash_before=Decimal("1000.0000"),
                        target_cash=Decimal("1000.0000"),
                        cash_after=Decimal("1000.0000"),
                        code_version="test",
                        created_at=created,
                    )
                )
            db.commit()

            repository = PortfolioPlanRepository()
            first, total = repository.list_plans(db, limit=2, offset=0)
            second, second_total = repository.list_plans(db, limit=2, offset=2)
            assert total == second_total == 3
            assert [row.id for row in first] == [3, 2]
            assert [row.id for row in second] == [1]
    finally:
        engine.dispose()


def test_plan_listing_serializes_summary_without_optional_detail_columns() -> None:
    engine = create_engine("duckdb:///:memory:")
    try:
        with engine.begin() as connection:
            connection.execute(text("CREATE SCHEMA portfolio"))
            connection.execute(text("""
                CREATE TABLE portfolio.portfolio_plan (
                    id INTEGER PRIMARY KEY, name VARCHAR, as_of_date DATE,
                    total_equity DECIMAL(20, 4), code_version VARCHAR,
                    created_at TIMESTAMP WITH TIME ZONE
                )
            """))
            connection.execute(text("""
                INSERT INTO portfolio.portfolio_plan VALUES
                (1, 'legacy', '2026-11-17', 1234.5678, 'test',
                 '2026-11-17 00:00:00+00')
            """))
        with Session(engine) as db:
            page = PortfolioConstructionService().list_plans(db, limit=50, offset=0)
            assert page.total == 1
            assert page.items[0].plan_id == 1
            assert page.items[0].name == "legacy"
            assert page.items[0].as_of_date == date(2026, 11, 17)
            assert page.model_dump(mode="json")["items"][0]["total_equity"] == "1234.5678"
    finally:
        engine.dispose()


def test_portfolio_style_risk_round_trips_through_json_and_plan_detail(tmp_path) -> None:
    engine = create_engine(f"duckdb:///{tmp_path / 'portfolio-risk.duckdb'}")
    try:
        initialize_database(engine)
        repository = PortfolioPlanRepository()
        with Session(engine) as db:
            record = repository.save(
                db,
                name="risk draft",
                as_of_date=date(2026, 11, 13),
                parameters={},
                plan=PortfolioPlan(
                    total_equity=Decimal("1000"),
                    cash_before=Decimal("1000"),
                    target_cash=Decimal("1000"),
                    cash_after=Decimal("1000"),
                    targets=(),
                    style_risk=PortfolioStyleRisk(
                        style_exposures=(
                            StyleExposure(
                                style_name="value",
                                portfolio_exposure=Decimal("0.1234567890"),
                                benchmark_exposure=Decimal("0.0234567890"),
                                active_exposure=Decimal("0.1000000000"),
                            ),
                        ),
                        tracking_error=Decimal("0.0812345678"),
                        portfolio_volatility=Decimal("0.1512345678"),
                        missing_styles=(),
                        missing_benchmark=False,
                        factor_versions={"momentum": "v1-qfq"},
                        factor_as_of_date=date(2026, 11, 13),
                        excluded_styles=("size", "value"),
                        sample_start_date=date(2026, 8, 17),
                        sample_end_date=date(2026, 11, 13),
                        return_observation_count=60,
                    ),
                ),
                code_version="test",
                idempotency_key="risk-draft",
                request_hash=hash_portfolio_snapshot({}),
                input_snapshot={},
            )
            plan_id = record.id
            db.commit()
        with Session(engine) as db:
            detail = PortfolioConstructionService(repository=repository).get_plan(
                db, plan_id=plan_id
            )
            assert detail.style_risk.tracking_error == Decimal("0.0812345678")
            assert detail.style_risk.style_exposures[0].active_exposure == Decimal("0.1000000000")
            assert detail.style_risk.factor_versions == {"momentum": "v1-qfq"}
            assert detail.style_risk.factor_as_of_date == date(2026, 11, 13)
            assert detail.style_risk.return_observation_count == 60
            replay = PortfolioConstructionService(repository=repository).build(
                db,
                name="risk draft",
                as_of_date=date(2026, 11, 13),
                assets=[],
                cash=Decimal("1000"),
                constraints=PortfolioConstraints(),
                code_version="test",
                idempotency_key="risk-draft",
                input_snapshot={},
            )
            assert replay.plan_id == plan_id
            assert replay.style_risk.tracking_error == Decimal("0.0812345678")
    finally:
        engine.dispose()
