"""Read-only research reads against the warm-layer (Parquet) connection.

Why this exists
---------------

The control-plane DuckDB holds only a hot window of ``market.daily_bar``
(59 trading days at the time of writing) after the storage split.  Factor
services and the backtest service go through SQLAlchemy ORM, so without an
explicit alternative they can never see the ~2,485 trading days that live in
the Parquet warm layer.  ``WarmLayerStore.connect()`` already exposes those
Parquet partitions under the same ``schema.table`` names; this repository is
the read-only, DuckDB-backed twin of the ORM repositories used by the research
path.

Design rules kept from the four-role review:

* Reads only.  Writes (``backtest_run``, fills, snapshots) keep going through
  the ORM / control plane, so the control plane remains the single write source.
* Every query filters ``quality_status`` and ``is_suspect`` exactly like the ORM
  counterpart does, so mixing historical Parquet rows with the control-plane
  hot window cannot silently let untrusted rows into a research run.
* ``limit_up`` / ``limit_down`` are enriched with :func:`derive_price_limits`
  when the underlying row has none.  The control plane never populated those
  columns, which made ``AShareTradabilityPolicy`` behave as "everything is
  tradable" and systematically overstated backtest returns.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import duckdb

from app.market.price_limit import derive_price_limits
from app.utils.symbol import symbol_exchange

_BAR_COLUMNS: tuple[str, ...] = (
    "symbol",
    "trade_date",
    "security_type",
    "adjust_type",
    "open",
    "high",
    "low",
    "close",
    "pre_close",
    "volume",
    "amount",
    "turnover_rate",
    "limit_up",
    "limit_down",
    "is_suspended",
    "special_treatment_status",
    "source_name",
    "source_count",
    "quality_score",
    "quality_status",
    "is_suspect",
    "selected_raw_id",
)

_ACTION_COLUMNS: tuple[str, ...] = (
    "symbol",
    "ex_date",
    "action_type",
    "cash_dividend",
    "bonus_share_ratio",
    "transfer_share_ratio",
    "rights_issue_ratio",
    "rights_issue_price",
    "split_ratio",
    "record_date",
    "payment_date",
    "source_name",
    "source_event_id",
    "note",
)


class WarmResearchReadRepository:
    """Read-only research reads over a ``WarmLayerStore`` DuckDB connection."""

    def __init__(self, connection: duckdb.DuckDBPyConnection) -> None:
        self._con = connection

    # ------------------------------------------------------------------ bars

    def list_daily_bars(
        self,
        *,
        start_date: date,
        end_date: date,
        adjust_type: str,
        symbols: Sequence[str] | None = None,
        quality_status: str = "trusted",
        enrich_price_limits: bool = True,
    ) -> list[SimpleNamespace]:
        """Return trusted daily bars shaped like the ORM ``DailyBar`` rows."""
        col_sql = ", ".join(_BAR_COLUMNS)
        sql = (
            f"SELECT {col_sql} FROM market.daily_bar "
            "WHERE adjust_type = ? AND quality_status = ? "
            "AND (is_suspect IS NULL OR is_suspect = FALSE) "
            "AND trade_date BETWEEN ? AND ?"
        )
        params: list[Any] = [adjust_type, quality_status, start_date, end_date]
        if symbols:
            placeholders = ", ".join("?" for _ in symbols)
            sql += f" AND symbol IN ({placeholders})"
            params.extend(symbols)
        sql += " ORDER BY symbol, trade_date"

        rows = self._con.execute(sql, params).fetchall()
        return [self._to_bar(record, enrich_price_limits=enrich_price_limits) for record in rows]

    @staticmethod
    def _to_bar(record: tuple[Any, ...], *, enrich_price_limits: bool) -> SimpleNamespace:
        values = dict(zip(_BAR_COLUMNS, record, strict=True))
        if enrich_price_limits and values.get("limit_up") is None:
            limit_up, limit_down = derive_price_limits(
                values.get("pre_close"),
                symbol=values["symbol"],
                security_type=values.get("security_type"),
                special_treatment_status=values.get("special_treatment_status"),
            )
            if limit_up is not None and limit_down is not None:
                values["limit_up"] = limit_up
                values["limit_down"] = limit_down
        if values.get("is_suspended") is None:
            values["is_suspended"] = False
        return SimpleNamespace(**values)

    # ------------------------------------------------------ corporate actions

    def list_corporate_actions(
        self,
        *,
        symbols: Iterable[str],
        start_date: date,
        end_date: date,
    ) -> list[SimpleNamespace]:
        """Return corporate actions overlapping the interval.

        This must read through the *same* connection as the bars.  Reading
        corporate actions from the control plane while bars come from Parquet
        would let the corporate-action ``reject`` gate miss historical events.
        """
        symbol_list = sorted(set(symbols))
        col_sql = ", ".join(_ACTION_COLUMNS)
        sql = f"SELECT {col_sql} FROM market.corporate_action WHERE ex_date BETWEEN ? AND ?"
        params: list[Any] = [start_date, end_date]
        if symbol_list:
            placeholders = ", ".join("?" for _ in symbol_list)
            sql += f" AND symbol IN ({placeholders})"
            params.extend(symbol_list)
        sql += " ORDER BY symbol, ex_date"
        rows = self._con.execute(sql, params).fetchall()
        return [
            SimpleNamespace(**dict(zip(_ACTION_COLUMNS, record, strict=True))) for record in rows
        ]

    # ------------------------------------------------------ execution calendar

    def load_execution_calendar(
        self,
        *,
        symbols: Iterable[str],
        start_date: date,
        end_date: date,
    ) -> dict[str, list[date]]:
        """Mirror ``DailyBacktestService._load_execution_calendar`` semantics."""
        symbols_by_exchange: dict[str, list[str]] = {}
        for symbol in sorted(set(symbols)):
            exchange = symbol_exchange(symbol)
            if exchange is None:
                raise ValueError(
                    f"cannot resolve exchange from symbol for execution calendar: {symbol}"
                )
            symbols_by_exchange.setdefault(exchange, []).append(symbol)

        result: dict[str, list[date]] = {}
        for exchange, exchange_symbols in symbols_by_exchange.items():
            dates = [
                row[0]
                for row in self._con.execute(
                    "SELECT trade_date FROM market.trading_calendar "
                    "WHERE exchange = ? AND trade_date BETWEEN ? AND ? "
                    "AND is_open = TRUE "
                    "ORDER BY trade_date",
                    [exchange, start_date, end_date],
                ).fetchall()
            ]
            if not dates:
                raise ValueError(
                    "trusted trading calendar is unavailable for exchange "
                    f"{exchange} in the backtest interval"
                )
            for symbol in exchange_symbols:
                result[symbol] = list(dates)
        return result

    # ----------------------------------------------------------- factor values

    def list_factor_values(
        self,
        *,
        factor_name: str,
        factor_version: str,
        start_date: date,
        end_date: date,
    ) -> list[SimpleNamespace]:
        """Return factor rows shaped like ``FactorValueRepository`` output.

        Ordering matches the ORM counterpart (``trade_date, symbol``) because
        the research contract hashes a normalized slice in which row order is
        part of reproducibility.
        """
        rows = self._con.execute(
            "SELECT trade_date, symbol, value, run_id FROM factor.factor_value "
            "WHERE factor_name = ? AND factor_version = ? "
            "AND trade_date BETWEEN ? AND ? "
            "ORDER BY trade_date, symbol",
            [factor_name, factor_version, start_date, end_date],
        ).fetchall()
        return [
            SimpleNamespace(
                trade_date=record[0],
                symbol=record[1].upper(),
                value=record[2],
                run_id=record[3],
            )
            for record in rows
        ]

    def list_qfq_closes(
        self,
        *,
        start_date: date,
        end_date: date,
    ) -> list[SimpleNamespace]:
        """Return curated forward-adjusted closes for IC forward returns.

        Reads the ``market.prices_qfq`` convenience view mounted by
        ``WarmLayerStore.connect``; a missing view raises so callers can fail
        closed instead of silently substituting another price vintage.
        """
        try:
            rows = self._con.execute(
                "SELECT symbol, trade_date, close FROM market.prices_qfq "
                "WHERE trade_date BETWEEN ? AND ? ORDER BY symbol, trade_date",
                [start_date, end_date],
            ).fetchall()
        except Exception as exc:  # noqa: BLE001 - view may not exist at all
            raise ValueError(
                "market.prices_qfq is not available in the bound warm-layer "
                "release; training-window selection requires the curated "
                "forward-adjusted price layer"
            ) from exc
        return [
            SimpleNamespace(
                symbol=record[0].upper(),
                trade_date=record[1],
                close=record[2],
            )
            for record in rows
        ]

    # ------------------------------------------------------- valuation history

    def list_valuation_history(
        self,
        *,
        start_date: date,
        end_date: date,
        symbols: Sequence[str] | None = None,
    ) -> list[SimpleNamespace]:
        """Return research-layer valuation rows for the requested window.

        The rows come from ``research.valuation_history``, the Parquet-only
        warm-layer table produced by the valuation-history sweep.  The table
        does not exist in the control plane, so the read raises when the bound
        release does not carry it: the caller must fail closed instead of
        silently reading an empty set and reporting a zero-information
        factor.

        ``pit_verified`` is carried through unchanged.  Every row of the
        current dataset is a late observation of a historical date, so the
        flag is false throughout; a consumer that needs point-in-time
        evidence must check it rather than assume the column is absent.
        """
        params: list[Any] = [start_date, end_date]
        symbol_filter = ""
        if symbols:
            placeholders = ", ".join("?" for _ in symbols)
            symbol_filter = f" AND symbol IN ({placeholders})"
            params.extend(symbol.upper() for symbol in symbols)
        try:
            rows = self._con.execute(
                "SELECT symbol, trade_date, close, pe_ttm, pb_mrq, ps_ttm, "
                "total_market_cap, total_shares, free_shares_a, "
                "float_market_cap, change_rate, pit_verified "
                "FROM research.valuation_history "
                "WHERE trade_date BETWEEN ? AND ?"
                f"{symbol_filter} "
                "ORDER BY trade_date, symbol",
                params,
            ).fetchall()
        except Exception as exc:  # noqa: BLE001 - the table may not exist
            raise ValueError(
                "research.valuation_history is not available in the bound "
                "warm-layer release; a value factor needs the valuation "
                "history dataset to be published first"
            ) from exc
        return [
            SimpleNamespace(
                symbol=record[0].upper(),
                trade_date=record[1],
                close=record[2],
                pe_ttm=record[3],
                pb_mrq=record[4],
                ps_ttm=record[5],
                total_market_cap=record[6],
                total_shares=record[7],
                free_shares_a=record[8],
                float_market_cap=record[9],
                change_rate=record[10],
                pit_verified=bool(record[11]),
            )
            for record in rows
        ]

    def valuation_history_coverage(
        self,
        *,
        start_date: date,
        end_date: date,
    ) -> dict[str, Any]:
        """Return the coverage facts of the valuation-history dataset.

        A value factor needs enough rows to rank the cross-section.  The
        caller uses the counts to fail closed when the dataset is too thin
        to say anything.
        """
        try:
            row = self._con.execute(
                "SELECT count(*), count(DISTINCT trade_date), count(DISTINCT symbol), "
                "min(trade_date), max(trade_date) "
                "FROM research.valuation_history "
                "WHERE trade_date BETWEEN ? AND ?",
                [start_date, end_date],
            ).fetchone()
        except Exception as exc:  # noqa: BLE001 - the table may not exist
            raise ValueError(
                "research.valuation_history is not available in the bound "
                "warm-layer release"
            ) from exc
        return {
            "row_count": int(row[0] or 0),
            "trading_days": int(row[1] or 0),
            "symbol_count": int(row[2] or 0),
            "min_date": row[3],
            "max_date": row[4],
        }

    # ------------------------------------------------------------- diagnostics

    def coverage(
        self,
        *,
        start_date: date,
        end_date: date,
        adjust_type: str = "none",
        quality_status: str = "trusted",
    ) -> dict[str, Any]:
        """Return coverage facts for the requested window (used for fail-closed guards)."""
        row = self._con.execute(
            "SELECT count(*), count(DISTINCT trade_date), count(DISTINCT symbol), "
            "min(trade_date), max(trade_date) FROM market.daily_bar "
            "WHERE adjust_type = ? AND quality_status = ? "
            "AND trade_date BETWEEN ? AND ?",
            [adjust_type, quality_status, start_date, end_date],
        ).fetchone()
        return {
            "row_count": row[0],
            "trading_days": row[1],
            "symbol_count": row[2],
            "min_trade_date": row[3],
            "max_trade_date": row[4],
        }

    def max_exported_date(self, *, adjust_type: str = "none") -> date | None:
        """Newest date available in the Parquet warm layer for ``adjust_type``.

        In parquet-only mode the control-plane hot window is invisible, so a
        research window reaching past this date would silently run against a
        truncated series.  Callers must fail-closed on that condition.
        """
        row = self._con.execute(
            "SELECT MAX(trade_date) FROM market.daily_bar WHERE adjust_type = ?",
            [adjust_type],
        ).fetchone()
        return row[0]

    def total_trading_days(self, *, adjust_type: str = "none") -> int:
        """Total trading days exposed by the bound release (not the window).

        Used as a fail-closed guard: a warm-layer release that lost its history
        (e.g. because the export script ran against the slimmed control plane)
        exposes only a few dozen days and must not be used for research.
        """
        row = self._con.execute(
            "SELECT count(DISTINCT trade_date) FROM market.daily_bar WHERE adjust_type = ?",
            [adjust_type],
        ).fetchone()
        return int(row[0] or 0)

    def close(self) -> None:
        """Close the underlying DuckDB connection."""
        self._con.close()

    def tradability_coverage(
        self,
        *,
        start_date: date,
        end_date: date,
        adjust_type: str = "none",
    ) -> dict[str, Any]:
        """How many bars have a usable prev-close, i.e. can carry a price band."""
        row = self._con.execute(
            "SELECT count(*), count(pre_close) FROM market.daily_bar "
            "WHERE adjust_type = ? AND trade_date BETWEEN ? AND ?",
            [adjust_type, start_date, end_date],
        ).fetchone()
        total, with_pre_close = row
        return {
            "row_count": total,
            "pre_close_count": with_pre_close,
            "coverage_rate": Decimal(with_pre_close) / Decimal(total) if total else Decimal("0"),
        }
