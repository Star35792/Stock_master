from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.services import CapitalFlowFactorService


class FakeDailyBarRepository:
    def __init__(self, bars: list[SimpleNamespace]) -> None:
        self.bars = bars
        self.kwargs: dict | None = None

    def list_for_factor_calculation(
        self,
        db: object,
        **kwargs: object,
    ) -> list[SimpleNamespace]:
        self.kwargs = kwargs
        return self.bars


class FakeAlternativeDataSnapshotRepository:
    def __init__(self, snapshots: list[SimpleNamespace]) -> None:
        self.snapshots = snapshots
        self.kwargs: dict | None = None

    def list_available(self, db: object, **kwargs: object) -> list[SimpleNamespace]:
        self.kwargs = kwargs
        return self.snapshots


class FakeSecurityRepository:
    def __init__(self, industries: dict[str, str]) -> None:
        self.industries = industries
        self.symbols: set[str] | None = None

    def get_industries(self, db: object, symbols: set[str]) -> dict[str, str]:
        self.symbols = symbols
        return {symbol: self.industries[symbol] for symbol in symbols}


class FakeFactorDefinitionRepository:
    def __init__(self) -> None:
        self.definitions: list[SimpleNamespace] = []

    def ensure(self, db: object, **kwargs: object) -> SimpleNamespace:
        definition = SimpleNamespace(id=len(self.definitions) + 1, **kwargs)
        self.definitions.append(definition)
        return definition


class FakeFactorRunRepository:
    def __init__(self) -> None:
        self.runs: list[SimpleNamespace] = []
        self.finished: list[dict] = []

    def create(self, db: object, **kwargs: object) -> SimpleNamespace:
        run = SimpleNamespace(id=len(self.runs) + 1, **kwargs)
        self.runs.append(run)
        return run

    def update_status(self, db: object, **kwargs: object) -> SimpleNamespace:
        self.finished.append(kwargs)
        return SimpleNamespace(**kwargs)


class FakeFactorValueRepository:
    def __init__(self) -> None:
        self.values: list[dict] = []

    def upsert_value(self, db: object, **kwargs: object) -> None:
        self.values.append(kwargs)

    def upsert_values(self, db: object, rows: list[dict]) -> int:
        self.values.extend(rows)
        return len(rows)


def _bar(symbol: str, trade_date: date, amount: str) -> SimpleNamespace:
    return SimpleNamespace(
        symbol=symbol,
        security_type="stock",
        trade_date=trade_date,
        amount=Decimal(amount),
    )


def _snapshot(
    symbol: str,
    snapshot_date: date,
    value: str,
    *,
    dataset: str = "eastmoney_main_fund_rank",
    item_key: str | None = None,
    rank: int = 1,
) -> SimpleNamespace:
    return SimpleNamespace(
        dataset=dataset,
        snapshot_date=snapshot_date,
        item_key=item_key or symbol,
        symbol=symbol,
        rank=rank,
        value=Decimal(value),
        attributes={},
    )


def test_capital_flow_factor_service_calculates_main_fund_factors() -> None:
    bars = [
        _bar("AAA", date(2026, 7, 17), "1000"),
        _bar("AAA", date(2026, 7, 18), "1200"),
        _bar("BBB", date(2026, 7, 17), "2000"),
        _bar("BBB", date(2026, 7, 18), "2100"),
    ]
    snapshots = [
        _snapshot("AAA", date(2026, 7, day), str(day), rank=day)
        for day in range(1, 18)
    ] + [
        _snapshot("BBB", date(2026, 7, 17), "300", rank=2),
        _snapshot("AAA", date(2026, 7, 17), "50", dataset="northbound_flow"),
        _snapshot("BBB", date(2026, 7, 17), "100", dataset="northbound_flow"),
        _snapshot("AAA", date(2026, 7, 17), "0.9", dataset="institutional_crowding"),
        _snapshot("BBB", date(2026, 7, 17), "0.4", dataset="institutional_crowding"),
    ]
    margin_start = date(2026, 6, 27).toordinal()
    for offset in range(21):
        snapshots.append(
            _snapshot(
                "AAA",
                date.fromordinal(margin_start + offset),
                str(100 + offset),
                dataset="margin_financing",
            )
        )
    etf_start = date(2026, 6, 28).toordinal()
    for offset in range(20):
        snapshots.extend(
            [
                _snapshot(
                    "",
                    date.fromordinal(etf_start + offset),
                    "10",
                    dataset="etf_flow_industry",
                    item_key="Tech",
                ),
                _snapshot(
                    "",
                    date.fromordinal(etf_start + offset),
                    "5",
                    dataset="etf_flow_industry",
                    item_key="Finance",
                ),
            ]
        )
    daily_bar_repository = FakeDailyBarRepository(bars)
    snapshot_repository = FakeAlternativeDataSnapshotRepository(snapshots)
    security_repository = FakeSecurityRepository({"AAA": "Tech", "BBB": "Finance"})
    definition_repository = FakeFactorDefinitionRepository()
    run_repository = FakeFactorRunRepository()
    value_repository = FakeFactorValueRepository()
    service = CapitalFlowFactorService(
        daily_bar_repository=daily_bar_repository,
        snapshot_repository=snapshot_repository,
        security_repository=security_repository,
        definition_repository=definition_repository,
        run_repository=run_repository,
        value_repository=value_repository,
    )

    result = service.run(
        object(),
        start_date=date(2026, 7, 18),
        end_date=date(2026, 7, 18),
        symbols=["aaa"],
        adjust_type="qfq",
        code_version="abc123",
    )

    assert result.rows_by_factor == {
        "main_fund_net_inflow": 2,
        "main_fund_net_inflow_ratio": 2,
        "main_fund_rank_score": 2,
        "main_fund_inflow_change_5": 1,
        "main_fund_inflow_percentile_20": 0,
        "northbound_flow_ratio": 2,
        "margin_financing_change_20": 1,
        "etf_flow_industry_20": 2,
        "institutional_crowding_score": 2,
    }
    assert len(definition_repository.definitions) == 9
    assert all(run["status"] == "success" for run in run_repository.finished)
    assert daily_bar_repository.kwargs is not None
    assert daily_bar_repository.kwargs["symbols"] == ["AAA"]
    assert snapshot_repository.kwargs is not None
    assert snapshot_repository.kwargs["datasets"] == [
        "eastmoney_main_fund_rank",
        "etf_flow_industry",
        "institutional_crowding",
        "margin_financing",
        "northbound_flow",
    ]
    assert security_repository.symbols == {"AAA", "BBB"}

    values = {
        (item["factor_name"], item["symbol"]): item["value"]
        for item in value_repository.values
    }
    assert values[("main_fund_net_inflow", "AAA")] == Decimal("17.0000000000")
    assert values[("main_fund_net_inflow_ratio", "AAA")] == Decimal("0.0170000000")
    assert values[("main_fund_rank_score", "AAA")] == Decimal("0.0588235294")
    assert values[("main_fund_inflow_change_5", "AAA")] == Decimal("5.0000000000")
    assert values[("main_fund_net_inflow", "BBB")] == Decimal("300.0000000000")
    assert values[("main_fund_net_inflow_ratio", "BBB")] == Decimal("0.1500000000")
    assert ("main_fund_inflow_change_5", "BBB") not in values
    assert values[("northbound_flow_ratio", "AAA")] == Decimal("0.0500000000")
    assert values[("northbound_flow_ratio", "BBB")] == Decimal("0.0500000000")
    assert values[("margin_financing_change_20", "AAA")] == Decimal("20.0000000000")
    assert ("margin_financing_change_20", "BBB") not in values
    assert values[("etf_flow_industry_20", "AAA")] == Decimal("200.0000000000")
    assert values[("etf_flow_industry_20", "BBB")] == Decimal("100.0000000000")
    assert values[("institutional_crowding_score", "AAA")] == Decimal("0.9000000000")


def test_capital_flow_factor_service_validates_inputs() -> None:
    service = CapitalFlowFactorService(
        daily_bar_repository=FakeDailyBarRepository([]),
        snapshot_repository=FakeAlternativeDataSnapshotRepository([]),
        definition_repository=FakeFactorDefinitionRepository(),
        run_repository=FakeFactorRunRepository(),
        value_repository=FakeFactorValueRepository(),
    )

    with pytest.raises(ValueError, match="start_date"):
        service.run(
            object(),
            start_date=date(2026, 7, 19),
            end_date=date(2026, 7, 18),
        )

    with pytest.raises(ValueError, match="unknown factor names"):
        service.run(
            object(),
            start_date=date(2026, 7, 18),
            end_date=date(2026, 7, 18),
            factor_names=["not_exists"],
        )
