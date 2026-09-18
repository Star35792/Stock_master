from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from types import MappingProxyType
from typing import Any


class StrategyInputError(ValueError):
    """Raised when strategy inputs are incomplete, duplicated, or malformed."""


class FutureStrategyDataError(StrategyInputError):
    """Raised when a strategy input contains observations after its as-of date."""


@dataclass(frozen=True)
class StrategyFactorObservation:
    symbol: str
    trade_date: date
    value: Decimal
    factor_run_id: int | None = None


@dataclass(frozen=True)
class StrategyFactorFrame:
    """Immutable, point-in-time-bounded factor rows supplied to a strategy."""

    as_of_date: date
    rows_by_factor: Mapping[Any, tuple[StrategyFactorObservation, ...]]

    def __post_init__(self) -> None:
        normalized: dict[Any, tuple[StrategyFactorObservation, ...]] = {}
        seen: set[tuple[Any, str, date]] = set()
        for factor_spec, source_rows in self.rows_by_factor.items():
            rows = []
            for source_row in source_rows:
                row = StrategyFactorObservation(
                    symbol=str(getattr(source_row, "symbol", "")).strip().upper(),
                    trade_date=getattr(source_row, "trade_date", None),
                    value=getattr(source_row, "value", None),
                    factor_run_id=getattr(
                        source_row,
                        "factor_run_id",
                        getattr(source_row, "run_id", None),
                    ),
                )
                symbol = str(getattr(row, "symbol", "")).strip().upper()
                trade_date = getattr(row, "trade_date", None)
                if not symbol:
                    raise StrategyInputError("strategy factor row symbol must not be empty")
                if not isinstance(trade_date, date):
                    raise StrategyInputError("strategy factor row trade_date must be a date")
                if trade_date > self.as_of_date:
                    raise FutureStrategyDataError(
                        "strategy factor frame contains future data: "
                        f"{symbol} {trade_date.isoformat()} > {self.as_of_date.isoformat()}"
                    )
                if not isinstance(row.value, Decimal):
                    raise StrategyInputError(
                        "strategy factor row value must be a Decimal"
                    )
                key = (factor_spec, symbol, trade_date)
                if key in seen:
                    raise StrategyInputError(
                        "duplicate strategy factor observation: "
                        f"{symbol} {trade_date.isoformat()}"
                    )
                seen.add(key)
                rows.append(row)
            normalized[factor_spec] = tuple(rows)
        object.__setattr__(self, "rows_by_factor", MappingProxyType(normalized))

    @classmethod
    def from_rows(
        cls,
        *,
        as_of_date: date,
        rows_by_factor: Mapping[Any, Iterable[Any]],
    ) -> StrategyFactorFrame:
        return cls(
            as_of_date=as_of_date,
            rows_by_factor={key: tuple(rows) for key, rows in rows_by_factor.items()},
        )

    def snapshot(self) -> dict[str, Any]:
        """Return a stable content digest for the exact factor observations consumed."""

        records = []
        for factor_spec, rows in self.rows_by_factor.items():
            factor_name = str(getattr(factor_spec, "factor_name", ""))
            factor_version = str(getattr(factor_spec, "factor_version", ""))
            for row in rows:
                records.append(
                    {
                        "factor_name": factor_name,
                        "factor_version": factor_version,
                        "symbol": str(row.symbol).upper(),
                        "trade_date": row.trade_date.isoformat(),
                        "value": _canonical_value(row.value),
                        "factor_run_id": row.factor_run_id,
                    }
                )
        records.sort(
            key=lambda item: (
                item["factor_name"],
                item["factor_version"],
                item["trade_date"],
                item["symbol"],
            )
        )
        content = json.dumps(
            records,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return {
            "contract_version": "strategy_factor_frame_v1",
            "as_of_date": self.as_of_date.isoformat(),
            "row_count": len(records),
            "factor_run_ids": sorted(
                {
                    row.factor_run_id
                    for rows in self.rows_by_factor.values()
                    for row in rows
                    if row.factor_run_id is not None
                }
            ),
            "sha256": hashlib.sha256(content).hexdigest(),
        }

    @property
    def observation_dates(self) -> tuple[date, ...]:
        return tuple(
            sorted(
                {
                    row.trade_date
                    for rows in self.rows_by_factor.values()
                    for row in rows
                }
            )
        )

    def for_date(self, as_of_date: date) -> StrategyFactorFrame:
        if as_of_date > self.as_of_date:
            raise FutureStrategyDataError(
                f"cannot build decision frame after {self.as_of_date.isoformat()}"
            )
        return StrategyFactorFrame(
            as_of_date=as_of_date,
            rows_by_factor={
                factor_spec: tuple(
                    row for row in rows if row.trade_date == as_of_date
                )
                for factor_spec, rows in self.rows_by_factor.items()
            },
        )

    def require_exact_as_of(self) -> None:
        non_exact = sorted(
            {
                row.trade_date
                for rows in self.rows_by_factor.values()
                for row in rows
                if row.trade_date != self.as_of_date
            }
        )
        if non_exact:
            raise StrategyInputError(
                "strategy decision frame must contain only its exact as_of_date"
            )


def _canonical_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, date):
        return value.isoformat()
    return value
