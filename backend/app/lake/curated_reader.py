"""Drop-in curated reader for factor computation.

``CuratedDailyBarReader`` exposes the same method surface that factor services
use on ``DailyBarRepository`` — ``list_for_factor_calculation`` and
``count_trusted_by_date`` — but reads the materialized ``curated/prices_qfq``
Parquet instead of the ORM table. It returns lightweight ``CuratedBar`` objects
carrying exactly the attributes factor code touches (symbol, trade_date,
security_type, close, volume, amount, turnover_rate), so the services are
untouched: they duck-type the bar and never see the difference.

The ``db`` first argument is accepted and ignored to keep the call sites
identical to the repository; the reader is stateless over DuckDB.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path

import duckdb

from app.lake.data_lake import DataLake

# adjust_type is folded into curated releases, not stored per row: prices_qfq is
# forward-adjusted. Only 'qfq' is served; other types fall back to the ORM path.
_SUPPORTED_ADJUST = "qfq"


@dataclass(frozen=True)
class CuratedBar:
    symbol: str
    trade_date: date
    security_type: str
    close: Decimal | None
    volume: Decimal | None
    amount: Decimal | None
    turnover_rate: Decimal | None


class CuratedDailyBarReader:
    """Reads forward-adjusted bars from the curated lake for factor runs."""

    def __init__(self, lake: DataLake | None = None) -> None:
        self.lake = lake or DataLake()

    def _glob(self) -> str:
        target = self.lake.root / "curated" / "prices_qfq"
        if not target.exists():
            raise LookupError(
                "curated prices_qfq not built; run build_curated_layer prices_qfq"
            )
        return str(target / "**" / "*.parquet")

    def list_for_factor_calculation(
        self,
        db: object,
        *,
        start_date: date,
        end_date: date,
        adjust_type: str,
        symbols: list[str] | None = None,
        quality_status: str = "trusted",
    ) -> list[CuratedBar]:
        if adjust_type != _SUPPORTED_ADJUST:
            raise ValueError(
                f"curated reader only serves adjust_type='{_SUPPORTED_ADJUST}', "
                f"got '{adjust_type}'"
            )
        filters = ["trade_date >= ?", "trade_date <= ?"]
        params: list[object] = [self._glob(), start_date, end_date]
        if symbols:
            placeholders = ",".join("?" for _ in symbols)
            filters.append(f"symbol IN ({placeholders})")
            params.extend(symbol.upper() for symbol in symbols)
        connection = duckdb.connect()
        try:
            cursor = connection.execute(
                f"""
                SELECT symbol, trade_date, security_type, close, volume,
                       amount, turnover_rate
                FROM read_parquet(?, hive_partitioning=true)
                WHERE {" AND ".join(filters)}
                ORDER BY symbol, trade_date
                """,
                params,
            )
            return [
                CuratedBar(
                    symbol=row[0],
                    trade_date=row[1],
                    security_type=row[2],
                    close=row[3],
                    volume=row[4],
                    amount=row[5],
                    turnover_rate=row[6],
                )
                for row in cursor.fetchall()
            ]
        finally:
            connection.close()

    def count_trusted_by_date(
        self,
        db: object,
        *,
        start_date: date,
        end_date: date,
        adjust_type: str,
        symbols: list[str] | None = None,
    ) -> dict[date, int]:
        if adjust_type != _SUPPORTED_ADJUST:
            raise ValueError(
                f"curated reader only serves adjust_type='{_SUPPORTED_ADJUST}', "
                f"got '{adjust_type}'"
            )
        filters = ["trade_date >= ?", "trade_date <= ?"]
        params: list[object] = [self._glob(), start_date, end_date]
        if symbols:
            placeholders = ",".join("?" for _ in symbols)
            filters.append(f"symbol IN ({placeholders})")
            params.extend(symbol.upper() for symbol in symbols)
        connection = duckdb.connect()
        try:
            rows = connection.execute(
                f"""
                SELECT trade_date, count(*)
                FROM read_parquet(?, hive_partitioning=true)
                WHERE {" AND ".join(filters)}
                GROUP BY trade_date
                """,
                params,
            ).fetchall()
            return {row[0]: int(row[1]) for row in rows}
        finally:
            connection.close()
