from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import duckdb

from app.lake.data_lake import DataLake
from app.lake.qfq import create_qfq_view


@dataclass(frozen=True)
class LakeFactorResult:
    path: str
    row_count: int
    start_date: date
    end_date: date


class LakeFactorPipeline:
    def __init__(self, lake: DataLake) -> None:
        self.lake = lake

    def query_qfq_bars(
        self,
        *,
        symbols: list[str] | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> list[dict[str, Any]]:
        connection = duckdb.connect()
        try:
            self._create_qfq_view(connection)
            filters, params = _filters(symbols, start_date, end_date)
            cursor = connection.execute(
                f"""
                SELECT symbol, trade_date, open_qfq AS open, high_qfq AS high,
                       low_qfq AS low, close_qfq AS close, volume, amount, qfq_factor
                FROM qfq_bars
                WHERE {' AND '.join(filters)}
                ORDER BY symbol, trade_date
                """,
                params,
            )
            columns = [column[0] for column in cursor.description]
            return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]
        finally:
            connection.close()

    def build_factors(
        self,
        *,
        release: str,
        start_date: date,
        end_date: date,
        symbols: list[str] | None = None,
        rebalance_dates: list[date] | None = None,
    ) -> LakeFactorResult:
        if start_date > end_date:
            raise ValueError("start_date must be earlier than or equal to end_date")
        if not release.strip() or any(character in release for character in "\\/:"):
            raise ValueError("release must be a non-empty path-safe name")
        target = self.lake.root / "factor" / f"release={release}" / "data.parquet"
        if target.exists():
            raise FileExistsError(f"factor release already exists: {release}")
        target.parent.mkdir(parents=True, exist_ok=False)

        connection = duckdb.connect()
        try:
            self._create_qfq_view(connection)
            connection.execute("CREATE TEMP TABLE rebalance_dates(trade_date DATE)")
            if rebalance_dates:
                connection.executemany(
                    "INSERT INTO rebalance_dates VALUES (?)",
                    [(item,) for item in sorted(set(rebalance_dates))],
                )
            filters, params = _filters(symbols, start_date, end_date, alias="f")
            if rebalance_dates:
                filters.append("f.trade_date IN (SELECT trade_date FROM rebalance_dates)")
            connection.execute(
                f"""
                CREATE TEMP TABLE factor_output AS
                    WITH returns AS (
                        SELECT *, close_qfq / lag(close_qfq) OVER w - 1 AS ret_1d
                        FROM qfq_bars
                        WINDOW w AS (PARTITION BY symbol ORDER BY trade_date)
                    ), factors AS (
                        SELECT
                            symbol, trade_date,
                            close_qfq / lag(close_qfq, 20) OVER w - 1 AS momentum_20,
                            close_qfq / lag(close_qfq, 60) OVER w - 1 AS momentum_60,
                            CASE WHEN count(ret_1d) OVER w20 = 20
                                 THEN stddev_pop(ret_1d) OVER w20 END AS volatility_20,
                            CASE WHEN count(ret_1d) OVER w20 = 20
                                 THEN sqrt(avg(pow(least(ret_1d, 0), 2)) OVER w20)
                                 END AS downside_volatility_20,
                            CASE WHEN count(amount) OVER w20 = 20
                                 THEN avg(amount) OVER w20 END AS liquidity_20,
                            CASE WHEN count(volume) OVER w20 = 20
                                 THEN -stddev_pop(volume) OVER w20
                                      / nullif(avg(volume) OVER w20, 0)
                                 END AS volume_stability_20
                        FROM returns
                        WINDOW
                            w AS (PARTITION BY symbol ORDER BY trade_date),
                            w20 AS (PARTITION BY symbol ORDER BY trade_date
                                    ROWS BETWEEN 19 PRECEDING AND CURRENT ROW)
                    )
                    SELECT f.*
                    FROM factors f
                    WHERE {' AND '.join(filters)}
                    ORDER BY f.trade_date, f.symbol
                """,
                params,
            )
            connection.execute(
                "COPY factor_output TO ? "
                "(FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE 100000)",
                [str(target)],
            )
            row_count = connection.execute(
                "SELECT count(*) FROM read_parquet(?)", [str(target)]
            ).fetchone()[0]
        except Exception:
            if target.exists():
                target.unlink()
            target.parent.rmdir()
            raise
        finally:
            connection.close()
        return LakeFactorResult(str(target.resolve()), row_count, start_date, end_date)

    def _create_qfq_view(self, connection: duckdb.DuckDBPyConnection) -> None:
        daily = Path(self.lake.latest("daily_bar").path) / "**" / "*.parquet"
        actions = Path(self.lake.latest("corporate_action").path) / "**" / "*.parquet"
        create_qfq_view(connection, daily_glob=str(daily), action_glob=str(actions))


def _filters(
    symbols: list[str] | None,
    start_date: date | None,
    end_date: date | None,
    *,
    alias: str = "",
) -> tuple[list[str], list[Any]]:
    prefix = f"{alias}." if alias else ""
    filters = ["1 = 1"]
    params: list[Any] = []
    if symbols:
        normalized = sorted({symbol.strip().upper() for symbol in symbols if symbol.strip()})
        if normalized:
            filters.append(f"{prefix}symbol IN ({','.join('?' for _ in normalized)})")
            params.extend(normalized)
    if start_date:
        filters.append(f"{prefix}trade_date >= ?")
        params.append(start_date)
    if end_date:
        filters.append(f"{prefix}trade_date <= ?")
        params.append(end_date)
    return filters, params


