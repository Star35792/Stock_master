"""Frozen small-sample parity for the control and warm research read shapes."""

from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import duckdb

from app.backtest import DailyEventBacktestEngine, TargetWeightSignal
from app.repositories.research_read import _BAR_COLUMNS, WarmResearchReadRepository
from app.services.backtest import _OrmReadFacade


def test_daily_engine_matches_control_and_warm_read_paths() -> None:
    days = [date(2026, 7, 1), date(2026, 7, 2), date(2026, 7, 3)]
    bar_rows = []
    for day in days:
        row = dict.fromkeys(_BAR_COLUMNS)
        row.update(
            symbol="600000.SH", trade_date=day, security_type="stock",
            adjust_type="none", open=Decimal("10"), high=Decimal("10.2"),
            low=Decimal("9.8"), close=Decimal("10"), pre_close=Decimal("10"),
            volume=Decimal("10000"), amount=Decimal("100000"),
            quality_status="trusted", is_suspect=False, is_suspended=False,
            source_name="fixture", source_count=1, quality_score=Decimal("100"),
        )
        bar_rows.append(row)

    types = {
        "trade_date": "DATE", "is_suspect": "BOOLEAN", "is_suspended": "BOOLEAN",
        "source_count": "INTEGER", "selected_raw_id": "BIGINT",
    }
    decimal_fields = {
        "open", "high", "low", "close", "pre_close", "volume", "amount",
        "turnover_rate", "limit_up", "limit_down", "quality_score",
    }
    columns = ", ".join(
        f"{name} {types.get(name, 'DECIMAL(24, 8)' if name in decimal_fields else 'VARCHAR')}"
        for name in _BAR_COLUMNS
    )
    con = duckdb.connect(":memory:")
    try:
        con.execute("CREATE SCHEMA market")
        con.execute(f"CREATE TABLE market.daily_bar ({columns})")
        placeholders = ", ".join("?" for _ in _BAR_COLUMNS)
        con.executemany(
            f"INSERT INTO market.daily_bar VALUES ({placeholders})",
            [tuple(row[name] for name in _BAR_COLUMNS) for row in bar_rows],
        )
        warm_rows = WarmResearchReadRepository(con).list_daily_bars(
            start_date=days[0], end_date=days[-1], adjust_type="none",
            symbols=["600000.SH"], quality_status="trusted",
        )
        control_rows = [
            SimpleNamespace(**(row | {"limit_up": Decimal("11"), "limit_down": Decimal("9")}))
            for row in bar_rows
        ]

        class ControlRepository:
            def list_for_factor_calculation(self, db, **kwargs):
                return control_rows

        facade = _OrmReadFacade(
            SimpleNamespace(daily_bar_repository=ControlRepository()), object(),
        )
        control_read = facade.list_daily_bars(
            start_date=days[0], end_date=days[-1], adjust_type="none",
            symbols=["600000.SH"], quality_status="trusted",
        )
        signal = [TargetWeightSignal(days[0], "600000.SH", Decimal("1"))]
        calendar = {"600000.SH": days}
        results = [DailyEventBacktestEngine(initial_cash=Decimal("100000")).run(
            bars=bars, signals=signal, execution_calendar=calendar,
        ) for bars in (control_read, warm_rows)]
        assert len(results[0].trades) == 1
        assert results[0].trades == results[1].trades
        assert results[0].positions == results[1].positions
        assert results[0].equity_curve == results[1].equity_curve
    finally:
        con.close()
