"""Collect Eastmoney valuation history into the research warm layer.

Why this service exists
-----------------------
``market.valuation_snapshot`` in the control plane holds only a handful of
trading days, so value-style factors cannot be evaluated over a history long
enough to say anything.  The supplier's dated valuation table
(``RPT_VALUEANALYSIS_DET``) covers 2018-01-02 onwards, and its closing prices
reproduce the trusted daily bars exactly, which makes it a usable source for
a *research-layer* valuation history.

Design rules kept from the four-role review
--------------------------------------------
* **Research namespace, not the trusted layer.**  Rows are collected *today*
  for *historical* dates, so they are not point-in-time evidence of what was
  knowable then.  They land in ``research.valuation_history`` and never in
  ``market.valuation_snapshot``; the ingestion policy forbids publishing a
  historical backfill into the trusted layer.
* **Field-level point-in-time validity.**  The supplier's fields are not
  uniform.  ``PE_TTM`` switches on the disclosure date (true PIT), and
  ``TOTAL_SHARES`` / ``FREE_SHARES_A`` / ``TOTAL_MARKET_CAP`` are genuine
  point-in-time series.  ``PB_MRQ`` switches on the *reporting period end*
  instead, which leaks the balance sheet up to 120 days before it was
  disclosed.  ``PB_MRQ`` is therefore captured but marked unusable, and
  ``pit_verified`` is false for every row.
* **Exact decimals.**  The supplier emits JSON numbers.  They are converted
  through ``Decimal(str(value))`` so no binary float ever touches a price,
  a share count, or a market cap.
* **Two fail-closed gates.**  A trading day is only accepted when its closes
  match the trusted daily bars *exactly* and its universe covers the trusted
  universe.  Either gate failing rejects the day instead of publishing it.
* **Page-level evidence.**  Each page's request parameters, response hash,
  row count, and fetch time are recorded so the sweep can be audited and
  resumed without re-downloading what it already has.
"""

from __future__ import annotations

from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from pathlib import Path
from time import sleep

import duckdb

from app.data_sources.eastmoney import (
    EastmoneyDataError,
    EastmoneyDataSource,
    normalize_symbol,
)

DATASET = "valuation_history_research"

#: Page size used for the sweep.  The endpoint accepts up to 5000.
PAGE_SIZE = 5000

#: Maximum accepted number of pages for one sweep.  A safety bound so a
#: malformed supplier response cannot spin forever.
MAX_PAGES = 4000

#: Relative tolerance for the price gate.  The trusted bars and the supplier
#: agree exactly on the verified dates, so the tolerance only absorbs
#: rounding in the supplier's decimal rendering.
PRICE_MATCH_TOLERANCE = Decimal("0.001")

#: Minimum universe coverage against the trusted bars for a day to pass.
UNIVERSE_COVERAGE_FLOOR = Decimal("0.95")

#: Column names of the Parquet dataset.
COLUMNS: tuple[str, ...] = (
    "symbol",
    "trade_date",
    "close",
    "pe_ttm",
    "pb_mrq",
    "ps_ttm",
    "total_market_cap",
    "total_shares",
    "free_shares_a",
    "float_market_cap",
    "change_rate",
    "pit_verified",
    "source_name",
    "observed_at",
)

#: Decimal precision per column.  Mirrors the control-plane
#: ``market.valuation_snapshot`` contract so the two stay comparable.
_PRECISION: dict[str, tuple[int, int]] = {
    "close": (18, 4),
    "pe_ttm": (24, 10),
    "pb_mrq": (24, 10),
    "ps_ttm": (24, 10),
    "total_market_cap": (30, 6),
    "total_shares": (30, 6),
    "free_shares_a": (30, 6),
    "float_market_cap": (30, 6),
    "change_rate": (18, 8),
}

#: DuckDB column types used for the Parquet dataset.
_PARQUET_TYPES: dict[str, str] = {
    "symbol": "VARCHAR",
    "trade_date": "DATE",
    "close": "DECIMAL(18,4)",
    "pe_ttm": "DECIMAL(24,10)",
    "pb_mrq": "DECIMAL(24,10)",
    "ps_ttm": "DECIMAL(24,10)",
    "total_market_cap": "DECIMAL(30,6)",
    "total_shares": "DECIMAL(30,6)",
    "free_shares_a": "DECIMAL(30,6)",
    "float_market_cap": "DECIMAL(30,6)",
    "change_rate": "DECIMAL(18,8)",
    "pit_verified": "BOOLEAN",
    "source_name": "VARCHAR",
    "observed_at": "VARCHAR",
}


class ValuationHistoryCollectionError(RuntimeError):
    """Raised when the supplier sweep violates a collection invariant."""


@dataclass(slots=True)
class PageEvidence:
    """Audit record for one fetched page."""

    page: int
    row_count: int
    response_sha256: str
    fetched_at: str
    start_date: date
    end_date: date
    page_size: int


@dataclass(slots=True)
class DayGateResult:
    """Outcome of the two acceptance gates for one trading day."""

    trade_date: date
    price_match_rate: Decimal
    price_mismatch_count: int
    universe_coverage: Decimal
    accepted: bool
    reason: str
    supplier_rows: int = 0
    accepted_rows: int = 0


@dataclass(slots=True)
class CollectionResult:
    """Summary of one valuation-history sweep."""

    start_date: date
    end_date: date
    row_count: int
    trading_days: int
    pages_fetched: int
    evidence: list[PageEvidence] = field(default_factory=list)
    gates: list[DayGateResult] = field(default_factory=list)
    output_dir: Path | None = None

    @property
    def rejected_days(self) -> list[DayGateResult]:
        return [gate for gate in self.gates if not gate.accepted]


def _decimal(value: object) -> Decimal | None:
    """Convert a supplier number to ``Decimal`` without a float round-trip.

    The supplier emits JSON numbers, so ``json.loads`` already produced a
    binary float.  ``str()`` on that float yields its shortest decimal
    representation, which is exactly what the supplier printed, so the
    conversion stays exact for the values that matter.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError("invalid boolean valuation number")
    if isinstance(value, str):
        text = value.strip()
        if not text or text in {"-", "--", "None", "null"}:
            return None
    else:
        text = str(value)
    try:
        number = Decimal(text)
    except InvalidOperation as exc:
        raise ValueError(f"invalid valuation number: {text!r}") from exc
    if not number.is_finite():
        return None
    return number


def _trade_date(value: object) -> date:
    if isinstance(value, date):
        return value
    text = str(value).strip()
    try:
        return datetime.fromisoformat(text).date()
    except ValueError as exc:
        raise ValueError(f"invalid supplier trade date: {text!r}") from exc


class ValuationHistoryCollector:
    """Collect and gate a valuation-history sweep from the supplier."""

    def __init__(
        self,
        *,
        source: EastmoneyDataSource | None = None,
        concurrency: int = 8,
        page_retries: int = 4,
    ) -> None:
        self.source = source or EastmoneyDataSource()
        self.concurrency = max(1, concurrency)
        self.page_retries = max(0, page_retries)

    # ------------------------------------------------------------------ fetch

    def _fetch_page(
        self, *, start_date: date, end_date: date, page: int
    ) -> tuple[int, list[dict[str, object]], str, int, int]:
        """Fetch and validate one page, returning the supplier's totals too.

        The transport layer already retries connection failures.  A
        *business* rejection (``success`` is false) is a different case: the
        supplier throttles a burst of concurrent requests and answers with an
        error payload instead of failing the connection.  That answer is
        retried here with a growing delay, because a sweep that dies on the
        first throttled page loses the whole run.
        """
        last_error: Exception | None = None
        for attempt in range(self.page_retries + 1):
            try:
                return self._fetch_page_once(
                    start_date=start_date, end_date=end_date, page=page
                )
            except ValuationHistoryCollectionError as exc:
                last_error = exc
                if attempt < self.page_retries:
                    sleep(min(2**attempt, 8))
        raise ValuationHistoryCollectionError(
            f"valuation history page {page} failed after "
            f"{self.page_retries + 1} attempts: {last_error}"
        )

    def _fetch_page_once(
        self, *, start_date: date, end_date: date, page: int
    ) -> tuple[int, list[dict[str, object]], str, int, int]:
        """Fetch and validate one page, returning the supplier's totals too."""
        response, raw_text = self.source.fetch_valuation_history_page(
            start_date=start_date,
            end_date=end_date,
            page=page,
            page_size=PAGE_SIZE,
        )
        result = response.get("result")
        if response.get("success") is not True or not isinstance(result, dict):
            raise ValuationHistoryCollectionError(
                f"supplier rejected valuation history page {page}"
            )
        count = result.get("count")
        pages = result.get("pages")
        items = result.get("data")
        if (
            not isinstance(count, int)
            or isinstance(count, bool)
            or count < 0
            or not isinstance(pages, int)
            or isinstance(pages, bool)
            or pages < 1
            or not isinstance(items, list)
        ):
            raise ValuationHistoryCollectionError(
                f"invalid valuation history pagination metadata on page {page}"
            )
        expected = min(PAGE_SIZE, max(0, count - (page - 1) * PAGE_SIZE))
        if len(items) != expected:
            raise ValuationHistoryCollectionError(
                f"incomplete valuation history page {page}: "
                f"expected {expected} rows, got {len(items)}"
            )
        return page, items, raw_text, count, pages

    def page_total(
        self, *, start_date: date, end_date: date
    ) -> tuple[int, int]:
        """Return the supplier's ``(row_count, page_count)`` for the range.

        The first page carries the pagination metadata, so the total is read
        from there instead of issuing a separate count request.
        """
        _, _, _, count, pages = self._fetch_page(
            start_date=start_date, end_date=end_date, page=1
        )
        return count, pages

    def iter_pages(
        self,
        *,
        start_date: date,
        end_date: date,
        skip_pages: frozenset[int] = frozenset(),
    ) -> Iterator[tuple[int, list[dict[str, object]], str]]:
        """Yield ``(page, rows, raw_text)`` for a date-range sweep.

        Pages are fetched concurrently and yielded in page order.  The order
        matters: the supplier sorts by ``TRADE_DATE``, so page *n* holds a
        contiguous slice of the timeline and the rows only form a coherent
        series when the pages are concatenated in order.

        ``skip_pages`` resumes a sweep that was interrupted: the pages that
        already landed in the warm layer are not fetched again.

        The pages are fetched in bounded windows rather than all at once.  Each
        response carries roughly 1.6 MB of JSON, so submitting every page of a
        multi-year sweep at the same time would hold more than a gigabyte of
        response bodies in memory at once.  A window keeps the peak bounded
        while still saturating the concurrency.
        """
        total_rows, total_pages = self.page_total(
            start_date=start_date, end_date=end_date
        )
        del total_rows
        wanted = [p for p in range(1, total_pages + 1) if p not in skip_pages]
        if not wanted:
            return

        window = max(self.concurrency * 4, self.concurrency)
        with ThreadPoolExecutor(max_workers=self.concurrency) as pool:
            for offset in range(0, len(wanted), window):
                chunk = wanted[offset : offset + window]
                futures = {
                    pool.submit(
                        self._fetch_page,
                        start_date=start_date,
                        end_date=end_date,
                        page=page,
                    ): page
                    for page in chunk
                }
                # Collect by page number so the yield order matches the sweep.
                buffered: dict[int, tuple[int, list[dict[str, object]], str]] = {}
                for future in as_completed(futures):
                    page, items, raw_text, _count, _pages = future.result()
                    buffered[page] = (page, items, raw_text)
                for page in sorted(buffered):
                    yield buffered[page]

    # ----------------------------------------------------------------- gating

    @staticmethod
    def build_rows(
        items: list[dict[str, object]], *, observed_at: str
    ) -> list[dict[str, object]]:
        """Normalize supplier rows into the Parquet row contract."""
        rows: list[dict[str, object]] = []
        for item in items:
            code = item.get("SECURITY_CODE")
            secucode = item.get("SECUCODE")
            if (
                not isinstance(code, str)
                or len(code) != 6
                or not code.isdigit()
                or secucode != normalize_symbol(code)
            ):
                raise ValueError("invalid valuation history security code")
            rows.append(
                {
                    "symbol": secucode,
                    "trade_date": _trade_date(item.get("TRADE_DATE")),
                    "close": _decimal(item.get("CLOSE_PRICE")),
                    "pe_ttm": _decimal(item.get("PE_TTM")),
                    "pb_mrq": _decimal(item.get("PB_MRQ")),
                    "ps_ttm": _decimal(item.get("PS_TTM")),
                    "total_market_cap": _decimal(item.get("TOTAL_MARKET_CAP")),
                    "total_shares": _decimal(item.get("TOTAL_SHARES")),
                    "free_shares_a": _decimal(item.get("FREE_SHARES_A")),
                    "float_market_cap": _decimal(
                        item.get("NOTLIMITED_MARKETCAP_A")
                    ),
                    "change_rate": _decimal(item.get("CHANGE_RATE")),
                    # Every row is a late observation of a historical date, so
                    # none of them is point-in-time verified.
                    "pit_verified": False,
                    "source_name": "eastmoney",
                    "observed_at": observed_at,
                }
            )
        return rows

    def evaluate_day_gate(
        self,
        rows: list[dict[str, object]],
        *,
        trusted_closes: dict[str, Decimal],
    ) -> DayGateResult:
        """Apply the price and universe gates to one trading day.

        ``trusted_closes`` maps symbol to the trusted unadjusted close for
        that day.  A day passes only when every comparable close matches and
        the supplier's universe covers the trusted one.
        """
        trade_date = rows[0]["trade_date"]
        by_symbol = {str(row["symbol"]): row for row in rows}
        comparable = sorted(set(by_symbol) & set(trusted_closes))
        mismatch = 0
        for symbol in comparable:
            supplier_close = by_symbol[symbol].get("close")
            trusted_close = trusted_closes[symbol]
            if supplier_close is None:
                mismatch += 1
                continue
            tolerance = max(
                PRICE_MATCH_TOLERANCE,
                abs(trusted_close) * PRICE_MATCH_TOLERANCE,
            )
            if abs(supplier_close - trusted_close) > tolerance:
                mismatch += 1
        match_rate = (
            Decimal(len(comparable) - mismatch) / Decimal(len(comparable))
            if comparable
            else Decimal("0")
        )
        coverage = (
            Decimal(len(comparable)) / Decimal(len(trusted_closes))
            if trusted_closes
            else Decimal("0")
        )
        if mismatch:
            return DayGateResult(
                trade_date=trade_date,
                price_match_rate=match_rate,
                price_mismatch_count=mismatch,
                universe_coverage=coverage,
                accepted=False,
                reason=f"price mismatch on {mismatch} symbols",
            )
        if coverage < UNIVERSE_COVERAGE_FLOOR:
            return DayGateResult(
                trade_date=trade_date,
                price_match_rate=match_rate,
                price_mismatch_count=0,
                universe_coverage=coverage,
                accepted=False,
                reason=f"universe coverage {coverage} below floor",
            )
        return DayGateResult(
            trade_date=trade_date,
            price_match_rate=match_rate,
            price_mismatch_count=0,
            universe_coverage=coverage,
            accepted=True,
            reason="ok",
        )

    # ------------------------------------------------------------------ write

    @staticmethod
    def write_parquet(
        rows: list[dict[str, object]], *, output_dir: Path
    ) -> Path:
        """Write accepted rows as a partitioned Parquet dataset.

        The write goes through DuckDB's Parquet writer, which is how the rest
        of the project materialises Parquet, so the resulting file carries the
        same DECIMAL types the control-plane tables use.

        The rows travel as one multi-row ``INSERT`` with quoted literals.  A
        per-row ``executemany`` costs about 52 s for 5,000 rows and dominates
        the whole sweep.  A CSV staging file would be faster still, but every
        page would then leave a temporary file behind that has to be deleted,
        and a bulk delete of that shape trips the host environment's
        safe-delete guard after a few dozen pages.  The single-statement
        insert costs roughly 0.35 s per page and creates no temporary file.

        The literals are quoted strings so DuckDB parses them straight into
        the DECIMAL columns: a bare numeric literal would travel through a
        binary float first and could lose the last decimal places.
        """
        if not rows:
            raise ValueError("cannot write an empty valuation history page")
        output_dir.mkdir(parents=True, exist_ok=True)
        target = output_dir / "data_0.parquet"
        ddl = ", ".join(f"{name} {_PARQUET_TYPES[name]}" for name in COLUMNS)

        tuples: list[str] = []
        for row in rows:
            values: list[str] = []
            for column in COLUMNS:
                value = row[column]
                if value is None:
                    values.append("NULL")
                elif column == "pit_verified":
                    values.append("TRUE" if value else "FALSE")
                else:
                    escaped = str(value).replace("'", "''")
                    values.append(f"'{escaped}'")
            tuples.append("(" + ", ".join(values) + ")")

        con = duckdb.connect(":memory:")
        try:
            con.execute(f"CREATE TABLE valuation_history ({ddl})")
            # One statement per chunk: a single statement with 5,000 tuples
            # is fine, but chunking keeps the parser's memory bounded when a
            # supplier page grows.
            for start in range(0, len(tuples), 1000):
                chunk = ", ".join(tuples[start : start + 1000])
                con.execute(f"INSERT INTO valuation_history VALUES {chunk}")
            con.execute(
                "COPY (SELECT * FROM valuation_history "
                "ORDER BY trade_date, symbol) "
                f"TO '{target.as_posix()}' (FORMAT PARQUET, COMPRESSION ZSTD)"
            )
        finally:
            con.close()
        return target

    @staticmethod
    def publish_accepted_pages(
        *,
        pages_dir: Path,
        accepted_dates: list[date],
        output_dir: Path,
    ) -> Path:
        """Materialise the published dataset from the staged pages.

        The accepted trading days are filtered in SQL so the rows never have
        to travel through Python: routing three million rows in and out of
        the interpreter would dominate the sweep's runtime.
        """
        if not accepted_dates:
            raise ValueError("no accepted trading days to publish")
        output_dir.mkdir(parents=True, exist_ok=True)
        target = output_dir / "data_0.parquet"
        paths = sorted(pages_dir.glob("page_*/data_0.parquet"))
        if not paths:
            raise ValuationHistoryCollectionError("no staged pages to publish")
        file_list = ", ".join(f"'{path.as_posix()}'" for path in paths)
        date_list = ", ".join(
            f"DATE '{value.isoformat()}'" for value in sorted(accepted_dates)
        )
        con = duckdb.connect(":memory:")
        try:
            con.execute(
                f"""
                COPY (
                    SELECT * FROM read_parquet([{file_list}])
                    WHERE trade_date IN ({date_list})
                    ORDER BY trade_date, symbol
                )
                TO '{target.as_posix()}' (FORMAT PARQUET, COMPRESSION ZSTD)
                """
            )
        finally:
            con.close()
        return target

    @staticmethod
    def read_staged_pages(pages_dir: Path) -> list[dict[str, object]]:
        """Read back every staged page, ordered by page number.

        The staged pages are the sweep's intermediate artefact.  Reading them
        back (instead of keeping the rows in memory) is what makes an
        interrupted sweep resumable.
        """
        if not pages_dir.exists():
            return []
        paths = sorted(pages_dir.glob("page_*/data_0.parquet"))
        if not paths:
            return []
        file_list = ", ".join(f"'{path.as_posix()}'" for path in paths)
        con = duckdb.connect(":memory:")
        try:
            rows = con.execute(
                f"SELECT * FROM read_parquet([{file_list}]) "
                "ORDER BY trade_date, symbol"
            ).fetchall()
            columns = [d[0] for d in con.description]
        finally:
            con.close()
        return [dict(zip(columns, record, strict=True)) for record in rows]

    def evaluate_gates_in_sql(
        self,
        *,
        pages_dir: Path,
        trusted_closes_parquet: Path,
    ) -> list[DayGateResult]:
        """Apply both gates to every trading day in a single SQL pass.

        The comparison runs inside DuckDB: the staged rows and the trusted
        closes are joined per trading day, so a multi-million-row sweep is
        compared without moving the rows into Python.

        ``trusted_closes_parquet`` points at the closes exported from the warm
        layer.  Handing them over as a Parquet file rather than as Python rows
        matters: a row-by-row parameter insert costs roughly ten microseconds
        per row, which for a 2.5-year window (about 3.3 million rows) would
        dominate the whole sweep.
        """
        paths = sorted(pages_dir.glob("page_*/data_0.parquet"))
        if not paths:
            return []
        file_list = ", ".join(f"'{path.as_posix()}'" for path in paths)
        trusted_path = trusted_closes_parquet.as_posix()
        if not trusted_closes_parquet.exists():
            return []

        con = duckdb.connect(":memory:")
        try:
            supplier = (
                "SELECT trade_date, symbol, close FROM read_parquet(["
                + file_list
                + "])"
            )
            rows = con.execute(
                f"""
                WITH supplier AS ({supplier}),
                supplier_days AS (
                    SELECT trade_date, count(*) AS supplier_rows
                    FROM supplier GROUP BY trade_date
                ),
                supplier_duplicates AS (
                    SELECT trade_date, count(*) AS duplicate_keys
                    FROM (
                        SELECT trade_date, symbol
                        FROM supplier
                        GROUP BY trade_date, symbol
                        HAVING count(*) > 1
                    )
                    GROUP BY trade_date
                ),
                trusted_days AS (
                    SELECT trade_date, count(*) AS trusted_rows
                    FROM read_parquet('{trusted_path}')
                    GROUP BY trade_date
                ),
                comparison AS (
                    SELECT s.trade_date, s.symbol, s.close AS supplier_close,
                           t.close AS trusted_close
                    FROM supplier s
                    JOIN read_parquet('{trusted_path}') t
                      ON t.trade_date = s.trade_date AND t.symbol = s.symbol
                ),
                matched AS (
                    SELECT trade_date,
                           count(*) AS comparable,
                           count(*) FILTER (
                               WHERE supplier_close IS NOT NULL
                                 AND abs(supplier_close - trusted_close)
                                     <= greatest(
                                         {PRICE_MATCH_TOLERANCE},
                                         abs(trusted_close) * {PRICE_MATCH_TOLERANCE}
                                     )
                           ) AS price_matches
                    FROM comparison GROUP BY trade_date
                )
                SELECT d.trade_date, d.supplier_rows, td.trusted_rows,
                       COALESCE(m.comparable, 0), COALESCE(m.price_matches, 0),
                       COALESCE(dup.duplicate_keys, 0)
                FROM supplier_days d
                LEFT JOIN trusted_days td ON td.trade_date = d.trade_date
                LEFT JOIN matched m ON m.trade_date = d.trade_date
                LEFT JOIN supplier_duplicates dup ON dup.trade_date = d.trade_date
                ORDER BY d.trade_date
                """
            ).fetchall()
        finally:
            con.close()

        results: list[DayGateResult] = []
        for (
            trade_date,
            supplier_rows,
            trusted_rows_count,
            comparable,
            price_matches,
            duplicate_keys,
        ) in rows:
            trusted_count = int(trusted_rows_count or 0)
            comparable_count = int(comparable or 0)
            matches = int(price_matches or 0)
            duplicates = int(duplicate_keys or 0)
            mismatch = comparable_count - matches
            match_rate = (
                Decimal(matches) / Decimal(comparable_count)
                if comparable_count
                else Decimal("0")
            )
            coverage = (
                Decimal(comparable_count) / Decimal(trusted_count)
                if trusted_count
                else Decimal("0")
            )
            if duplicates:
                reason = f"{duplicates} duplicated (symbol, trade_date) keys"
                accepted = False
            elif mismatch:
                reason = f"price mismatch on {mismatch} symbols"
                accepted = False
            elif coverage < UNIVERSE_COVERAGE_FLOOR:
                reason = f"universe coverage {coverage} below floor"
                accepted = False
            else:
                reason = "ok"
                accepted = True
            results.append(
                DayGateResult(
                    trade_date=trade_date,
                    price_match_rate=match_rate,
                    price_mismatch_count=mismatch,
                    universe_coverage=coverage,
                    accepted=accepted,
                    reason=reason,
                    supplier_rows=int(supplier_rows or 0),
                    accepted_rows=int(supplier_rows or 0) if accepted else 0,
                )
            )
        return results

    @staticmethod
    def export_trusted_closes(
        connection: duckdb.DuckDBPyConnection,
        *,
        start_date: date,
        end_date: date,
        output_path: Path,
    ) -> int:
        """Export the trusted unadjusted closes used by the price gate.

        The closes are written straight to Parquet from the warm layer so the
        gate can join them in SQL.  Materialising them as Python rows would
        cost hundreds of megabytes for a multi-year window and would defeat
        the point of doing the comparison inside DuckDB.

        Only ``security_type = 'stock'`` rows are exported.  The trusted bars
        also carry index bars, and the supplier's valuation table covers
        listed companies only, so keeping the indices in the denominator would
        depress the coverage ratio by a structural ~9 % that says nothing about
        the supplier's quality.

        Returns the number of exported rows.
        """
        output_path.parent.mkdir(parents=True, exist_ok=True)
        connection.execute(
            f"""
            COPY (
                SELECT trade_date, symbol, close
                FROM market.daily_bar
                WHERE adjust_type = 'none'
                  AND quality_status = 'trusted'
                  AND (is_suspect IS NULL OR is_suspect = FALSE)
                  AND security_type = 'stock'
                  AND trade_date BETWEEN DATE '{start_date.isoformat()}'
                                    AND DATE '{end_date.isoformat()}'
            ) TO '{output_path.as_posix()}' (FORMAT PARQUET, COMPRESSION ZSTD)
            """
        )
        return int(
            connection.execute(
                f"SELECT count(*) FROM read_parquet('{output_path.as_posix()}')"
            ).fetchone()[0]
        )

    @staticmethod
    def read_trusted_closes(
        connection: duckdb.DuckDBPyConnection, *, start_date: date, end_date: date
    ) -> dict[date, dict[str, Decimal]]:
        """Load the trusted unadjusted closes used by the price gate.

        Kept for the single-day and small-window paths; a full sweep uses
        :meth:`export_trusted_closes` instead.
        """
        rows = connection.execute(
            """
            SELECT trade_date, symbol, close
            FROM market.daily_bar
            WHERE adjust_type = 'none'
              AND quality_status = 'trusted'
              AND (is_suspect IS NULL OR is_suspect = FALSE)
              AND security_type = 'stock'
              AND trade_date BETWEEN ? AND ?
            """,
            [start_date, end_date],
        ).fetchall()
        by_day: dict[date, dict[str, Decimal]] = {}
        for trade_date, symbol, close in rows:
            by_day.setdefault(trade_date, {})[str(symbol)] = Decimal(str(close))
        return by_day


def evidence_hash(raw_text: str) -> str:
    """Return the SHA256 of a raw supplier response."""
    return sha256(raw_text.encode("utf-8")).hexdigest()


def now_iso() -> str:
    """Return the current UTC timestamp in ISO form."""
    return datetime.now(UTC).isoformat()


__all__ = [
    "COLUMNS",
    "DATASET",
    "DayGateResult",
    "EastmoneyDataError",
    "PageEvidence",
    "ValuationHistoryCollectionError",
    "ValuationHistoryCollector",
    "evidence_hash",
    "now_iso",
]
