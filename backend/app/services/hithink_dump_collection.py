from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from hashlib import sha256
from json import dumps
from uuid import uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import delete, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.data_sources import HithinkDumpDataSource, HithinkDumpKind
from app.data_sources.base import DailyBarRecord
from app.data_sources.hithink_dump import HithinkDump
from app.models.market import CorporateAction
from app.models.raw import RawCorporateAction
from app.repositories.market import SecurityRepository
from app.repositories.raw import RawDailyBarImportRepository
from app.services.daily_bar_market_builder import DailyBarMarketBuildService
from app.services.hithink_collection import _to_raw_daily_bar

_SHANGHAI = ZoneInfo("Asia/Shanghai")


@dataclass(frozen=True)
class HithinkDumpCollectionResult:
    batch_no: str
    release_tag: str
    file_path: str
    start_date: date
    end_date: date
    row_count: int
    accepted_count: int
    skipped_count: int


@dataclass(frozen=True)
class HithinkAdjustmentCollectionResult:
    batch_no: str
    release_tag: str
    file_path: str
    row_count: int


class HithinkDumpCollectionService:
    def __init__(
        self,
        *,
        data_source: HithinkDumpDataSource | None = None,
        repository: RawDailyBarImportRepository | None = None,
        security_repository: SecurityRepository | None = None,
    ) -> None:
        self.data_source = data_source or HithinkDumpDataSource()
        self.repository = repository or RawDailyBarImportRepository()
        self.security_repository = security_repository or SecurityRepository()

    def collect(
        self, db: Session, *, full: bool = False, dump: HithinkDump | None = None
    ) -> HithinkDumpCollectionResult:
        kind = HithinkDumpKind.FULL_DAILY if full else HithinkDumpKind.RECENT_DAILY
        dump = dump or self.data_source.download(kind)
        if dump.kind != kind:
            raise ValueError("dump kind does not match requested collection")
        start_date, end_date = self.data_source.daily_date_range(dump.path)
        batch_no = f"hithink_dump_{datetime.now(UTC):%Y%m%d%H%M%S}_{uuid4().hex[:8]}"
        batch = self.repository.create_batch(
            db,
            batch_no=batch_no,
            source_name=self.data_source.name,
            source_type=self.data_source.source_type,
            dataset="daily_bar",
            file_path=str(dump.path),
            request_params={
                "kind": kind.value,
                "release_tag": dump.release_tag,
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
            },
        )
        row_count = 0
        accepted_count = 0
        skipped_count = 0
        try:
            duckdb_conn = _duckdb_connection(db)
            if duckdb_conn is not None:
                bulk_result = self.repository.bulk_import_hithink_daily_dump(
                    duckdb_conn,
                    batch_id=batch.id,
                    source_name=self.data_source.name,
                    source_type=self.data_source.source_type,
                    path=dump.path,
                )
                row_count = bulk_result.input_count
                accepted_count = bulk_result.inserted_count
                skipped_count = bulk_result.skipped_count
            else:
                for chunk in self.data_source.iter_daily_rows(dump.path):
                    symbols = {str(item["thscode"]).upper() for item in chunk}
                    security_types = self.security_repository.get_security_types(db, symbols)
                    for item in chunk:
                        row_count += 1
                        row = _to_raw_daily_bar(
                            batch_id=batch.id,
                            source_name=self.data_source.name,
                            source_type=self.data_source.source_type,
                            source_priority=40,
                            record=_to_record(item),
                            security_type=security_types.get(str(item["thscode"]).upper()),
                        )
                        if self.repository.add_daily_bar_if_changed(db, row):
                            accepted_count += 1
                        else:
                            skipped_count += 1
                    db.flush()
            batch.request_params = {
                **batch.request_params,
                "skipped_count": skipped_count,
            }
            self.repository.finish_batch(
                db,
                batch,
                status="success",
                row_count=row_count,
                accepted_count=accepted_count,
                rejected_count=0,
            )
            db.commit()
        except Exception as exc:
            db.rollback()
            failed_batch = self.repository.create_batch(
                db,
                batch_no=f"{batch_no}_failed",
                source_name=self.data_source.name,
                source_type=self.data_source.source_type,
                dataset="daily_bar",
                file_path=str(dump.path),
                request_params={"kind": kind.value, "release_tag": dump.release_tag},
            )
            self.repository.finish_batch(
                db,
                failed_batch,
                status="failed",
                row_count=row_count,
                accepted_count=0,
                rejected_count=row_count,
                error_message=str(exc),
            )
            db.commit()
            raise
        return HithinkDumpCollectionResult(
            batch_no=batch_no,
            release_tag=dump.release_tag,
            file_path=str(dump.path),
            start_date=start_date,
            end_date=end_date,
            row_count=row_count,
            accepted_count=accepted_count,
            skipped_count=skipped_count,
        )

    def collect_adjustments(self, db: Session) -> HithinkAdjustmentCollectionResult:
        dump = self.data_source.download(HithinkDumpKind.ADJUSTMENT_FACTORS)
        batch_no = f"hithink_adjustment_{datetime.now(UTC):%Y%m%d%H%M%S}_{uuid4().hex[:8]}"
        batch = self.repository.create_batch(
            db,
            batch_no=batch_no,
            source_name=self.data_source.name,
            source_type=self.data_source.source_type,
            dataset="corporate_action",
            file_path=str(dump.path),
            request_params={"release_tag": dump.release_tag},
        )
        row_count = 0
        accepted_count = 0
        observed_action_versions: dict[tuple[str, date], str | None] = {}
        try:
            duckdb_conn = _duckdb_connection(db)
            if duckdb_conn is not None:
                row_count = int(
                    duckdb_conn.execute(
                        "SELECT count(*) FROM read_parquet(?)", [str(dump.path)]
                    ).fetchone()[0]
                )
                _bulk_import_adjustments_duckdb(
                    duckdb_conn,
                    batch_id=batch.id,
                    source_name=self.data_source.name,
                    path=dump.path,
                )
                accepted_count = _count_batch_rows_in_table(
                    db,
                    table="raw.corporate_action",
                    batch_id=batch.id,
                )
            else:
                for chunk in self.data_source.iter_adjustment_rows(dump.path, chunk_size=1_000):
                    row_count += len(chunk)
                    raw_rows = [
                        _to_raw_action(batch.id, self.data_source.name, item) for item in chunk
                    ]
                    for row in raw_rows:
                        key = (row.symbol, row.ex_date)
                        previous_hash = observed_action_versions.setdefault(key, row.row_hash)
                        if previous_hash != row.row_hash:
                            raise ValueError(
                                "corporate-action dump contains conflicting business keys: "
                                f"{row.symbol} {row.ex_date}"
                            )
                    existing = {
                        (symbol, ex_date, row_hash)
                        for symbol, ex_date, row_hash in db.execute(
                            select(
                                RawCorporateAction.symbol,
                                RawCorporateAction.ex_date,
                                RawCorporateAction.row_hash,
                            ).where(
                                RawCorporateAction.source_name == self.data_source.name,
                                RawCorporateAction.symbol.in_({row.symbol for row in raw_rows}),
                                RawCorporateAction.ex_date.in_({row.ex_date for row in raw_rows}),
                            )
                        )
                    }
                    new_rows = []
                    for row in raw_rows:
                        key = (row.symbol, row.ex_date, row.row_hash)
                        if key not in existing:
                            existing.add(key)
                            new_rows.append(row)
                    db.add_all(new_rows)
                    accepted_count += len(new_rows)
                    market_by_key = {}
                    for item in chunk:
                        market_row = _to_market_action(self.data_source.name, item)
                        if not _valid_market_action(market_row):
                            continue
                        key = (
                            market_row["symbol"],
                            market_row["ex_date"],
                            market_row["action_type"],
                            market_row["source_name"],
                        )
                        market_by_key[key] = market_row
                    market_rows = list(market_by_key.values())
                    if market_rows:
                        # ``market.corporate_action`` is the current projection
                        # of a source's latest observation.  Revisions remain in
                        # raw, but an action-type change (cash -> mixed) must
                        # replace, not coexist with, the older projection.
                        for row in market_rows:
                            db.execute(
                                delete(CorporateAction).where(
                                    CorporateAction.source_name == self.data_source.name,
                                    CorporateAction.symbol == row["symbol"],
                                    CorporateAction.ex_date == row["ex_date"],
                                    CorporateAction.action_type != row["action_type"],
                                )
                            )
                        statement = insert(CorporateAction).values(market_rows)
                        statement = statement.on_conflict_do_update(
                            index_elements=[
                                CorporateAction.symbol,
                                CorporateAction.ex_date,
                                CorporateAction.action_type,
                                CorporateAction.source_name,
                            ],
                            set_={
                                "cash_dividend": statement.excluded.cash_dividend,
                                "bonus_share_ratio": statement.excluded.bonus_share_ratio,
                                "rights_issue_ratio": statement.excluded.rights_issue_ratio,
                                "rights_issue_price": statement.excluded.rights_issue_price,
                                "raw_payload": statement.excluded.raw_payload,
                            },
                        )
                        db.execute(statement)
                    db.flush()
            batch.request_params = {
                **batch.request_params,
                "skipped_unchanged_count": row_count - accepted_count,
            }
            self.repository.finish_batch(
                db,
                batch,
                status="success",
                row_count=row_count,
                accepted_count=accepted_count,
                rejected_count=0,
            )
            db.commit()
        except Exception:
            db.rollback()
            raise
        return HithinkAdjustmentCollectionResult(
            batch_no=batch_no,
            release_tag=dump.release_tag,
            file_path=str(dump.path),
            row_count=row_count,
        )

    def build_market_daily(
        self, db: Session, *, batch_no: str, end_date: date | None = None
    ) -> int:
        from sqlalchemy import select

        from app.models.raw import ImportBatch

        batch = db.scalar(select(ImportBatch).where(ImportBatch.batch_no == batch_no))
        if batch is None:
            raise ValueError(f"Unknown import batch: {batch_no}")
        request_params = batch.request_params or {}
        params = {
            "batch_id": batch.id,
            "source_name": batch.source_name,
            "start_date": request_params.get("start_date"),
            "end_date": request_params.get("end_date"),
            "cap_date": end_date,
            "today": datetime.now(_SHANGHAI).date(),
        }
        db.execute(text(_ENSURE_DUMP_SECURITIES_SQL), params)
        # The set-based path is needed for years of dump history.  Dates that
        # require provenance, warning, or cross-source decisions are then sent
        # through the shared builder before the transaction commits, so the
        # fast path cannot grant a different trusted status.
        recheck_dates = [row[0] for row in db.execute(text(_RECHECK_DATES_SQL), params)]
        db.execute(text(_BUILD_MARKET_DAILY_SQL), params)
        for trade_date in recheck_dates:
            DailyBarMarketBuildService().build_from_raw(
                db,
                start_date=trade_date,
                end_date=trade_date,
                adjust_type="none",
                source_name=batch.source_name,
                commit=False,
            )
        market_rows = int(db.scalar(text(_COUNT_MARKET_DAILY_SQL), params) or 0)
        db.commit()
        return market_rows


def _to_record(item: dict) -> DailyBarRecord:
    symbol = str(item["thscode"]).upper()
    trade_date = datetime.fromtimestamp(
        int(item["date_ms"]) / 1000,
        tz=_SHANGHAI,
    ).date()
    return DailyBarRecord(
        symbol=symbol,
        trade_date=trade_date,
        adjust_type="none",
        open=Decimal(str(item["open_price"])),
        high=Decimal(str(item["high_price"])),
        low=Decimal(str(item["low_price"])),
        close=Decimal(str(item["close_price"])),
        volume=_decimal(item.get("volume")),
        amount=_decimal(item.get("turnover")),
        raw_payload=item,
    )


def _decimal(value: object) -> Decimal | None:
    return None if value is None else Decimal(str(value))


# Reuse unchanged raw versions after interrupted market builds. Rank before
# validation so a newer invalid revision cannot silently revive an older value.
_DUMP_CANDIDATES_SQL = """
WITH candidates AS (
    SELECT r.*, row_number() OVER (
        PARTITION BY r.symbol, r.trade_date, r.adjust_type ORDER BY r.id DESC
    ) AS version_rank
    FROM raw.daily_bar r
    WHERE r.source_name = :source_name
      AND r.adjust_type = 'none'
      AND (
        (CAST(:start_date AS DATE) IS NOT NULL AND CAST(:end_date AS DATE) IS NOT NULL
         AND r.trade_date BETWEEN CAST(:start_date AS DATE) AND CAST(:end_date AS DATE))
        OR ((CAST(:start_date AS DATE) IS NULL OR CAST(:end_date AS DATE) IS NULL)
            AND r.batch_id = :batch_id)
      )
      AND (CAST(:cap_date AS DATE) IS NULL OR r.trade_date <= CAST(:cap_date AS DATE))
), latest AS (
    SELECT * FROM candidates WHERE version_rank = 1
)
"""


_BUILD_MARKET_DAILY_SQL = (
    _DUMP_CANDIDATES_SQL
    + """
INSERT INTO market.daily_bar (
    symbol, security_type, trade_date, adjust_type, open, high, low, close,
    pre_close, change_amount, pct_change, volume, amount, turnover_rate,
    amplitude, source_name, source_count, quality_score, quality_status,
    is_suspect, special_treatment_status, selected_raw_id
)
SELECT
    r.symbol, r.security_type, r.trade_date, r.adjust_type,
    r.open, r.high, r.low, r.close,
    r.pre_close, r.change_amount, r.pct_change, r.volume, r.amount,
    r.turnover_rate, r.amplitude, r.source_name,
    1, 100.00, 'trusted', false, r.special_treatment_status, r.id
FROM latest r
JOIN market.security s ON s.symbol = r.symbol
JOIN market.trading_calendar c
  ON c.exchange = split_part(r.symbol, '.', 2)
 AND c.trade_date = r.trade_date
 AND c.is_open = true
WHERE r.adjust_type = 'none'
  AND NOT EXISTS (
      SELECT 1 FROM market.daily_bar_source_compare cmp
      WHERE cmp.symbol = r.symbol
        AND cmp.trade_date = r.trade_date
        AND cmp.adjust_type = r.adjust_type
        AND cmp.is_conflict = true
  )
  AND r.open > 0 AND r.high > 0 AND r.low > 0 AND r.close > 0
  AND r.is_suspended = false
  AND r.high >= r.open AND r.high >= r.close AND r.high >= r.low
  AND r.low <= r.open AND r.low <= r.close AND r.low <= r.high
  AND (r.volume IS NULL OR r.volume >= 0)
  AND (r.amount IS NULL OR r.amount >= 0)
ON CONFLICT (symbol, trade_date, adjust_type) DO UPDATE SET
    security_type = EXCLUDED.security_type,
    open = EXCLUDED.open,
    high = EXCLUDED.high,
    low = EXCLUDED.low,
    close = EXCLUDED.close,
    pre_close = EXCLUDED.pre_close,
    change_amount = EXCLUDED.change_amount,
    pct_change = EXCLUDED.pct_change,
    volume = EXCLUDED.volume,
    amount = EXCLUDED.amount,
    turnover_rate = EXCLUDED.turnover_rate,
    amplitude = EXCLUDED.amplitude,
    source_name = EXCLUDED.source_name,
    source_count = EXCLUDED.source_count,
    quality_score = EXCLUDED.quality_score,
    quality_status = EXCLUDED.quality_status,
    is_suspect = EXCLUDED.is_suspect,
    special_treatment_status = EXCLUDED.special_treatment_status,
    selected_raw_id = EXCLUDED.selected_raw_id
WHERE market.daily_bar.quality_status <> 'manual_fixed'
  AND market.daily_bar.selected_raw_id IS DISTINCT FROM EXCLUDED.selected_raw_id
  AND (market.daily_bar.selected_raw_id IS NULL
       OR market.daily_bar.selected_raw_id < EXCLUDED.selected_raw_id)
"""
)

_COUNT_MARKET_DAILY_SQL = (
    _DUMP_CANDIDATES_SQL
    + """
SELECT COUNT(*)
FROM market.daily_bar d
JOIN latest r ON r.id = d.selected_raw_id
WHERE d.adjust_type = 'none'
  AND d.quality_status IN ('trusted', 'manual_fixed')
  AND d.is_suspect = false
"""
)

_RECHECK_DATES_SQL = (
    _DUMP_CANDIDATES_SQL
    + """
SELECT DISTINCT r.trade_date
FROM latest r
WHERE r.volume IS NULL
   OR r.amount IS NULL
   OR (r.volume = 0 AND r.amount > 0)
   OR (r.amount = 0 AND r.volume > 0)
   OR r.is_suspended = true
   OR r.open IS NULL OR r.high IS NULL OR r.low IS NULL OR r.close IS NULL
   OR r.open <= 0 OR r.high <= 0 OR r.low <= 0 OR r.close <= 0
   OR r.high < r.open OR r.high < r.close OR r.high < r.low
   OR r.low > r.open OR r.low > r.close OR r.low > r.high
   OR r.volume < 0 OR r.amount < 0
   OR NOT EXISTS (
       SELECT 1
       FROM market.trading_calendar c
       WHERE c.exchange = split_part(r.symbol, '.', 2)
         AND c.trade_date = r.trade_date
         AND c.is_open = true
   )
   OR (
       r.pre_close IS NOT NULL AND r.pre_close <> 0
       AND r.change_amount IS NOT NULL
       AND abs((r.close - r.pre_close) - r.change_amount) > 0.01
   )
   OR (
       r.pre_close IS NOT NULL AND r.pre_close > 0
       AND abs((r.close - r.pre_close) / r.pre_close) > 0.30
   )
   OR r.imported_at < (
       (CAST(r.trade_date AS TIMESTAMP) + INTERVAL '15 hours 30 minutes')
       AT TIME ZONE 'Asia/Shanghai'
   )
   OR r.trade_date = CAST(:today AS DATE)
   OR EXISTS (
       SELECT 1
       FROM raw.daily_bar other
       WHERE other.symbol = r.symbol
         AND other.trade_date = r.trade_date
         AND other.adjust_type = r.adjust_type
         AND other.source_name <> r.source_name
   )
ORDER BY trade_date
"""
)

_ENSURE_DUMP_SECURITIES_SQL = (
    _DUMP_CANDIDATES_SQL
    + """
INSERT INTO market.security (
    symbol, raw_symbol, name, security_type, exchange, currency,
    list_date, is_active, source_name
)
SELECT
    r.symbol,
    split_part(r.symbol, '.', 1),
    r.symbol,
    CASE
        WHEN substr(split_part(r.symbol, '.', 1), 1, 2)
          IN ('15', '16', '18', '51', '56', '58')
            THEN 'etf'
        WHEN substr(split_part(r.symbol, '.', 1), 1, 3) = '399'
          OR (
              split_part(r.symbol, '.', 2) = 'SH'
              AND substr(split_part(r.symbol, '.', 1), 1, 3) = '000'
          ) THEN 'index'
        WHEN substr(split_part(r.symbol, '.', 1), 1, 2)
          IN ('00', '30', '60', '68', '83', '87', '92')
            THEN 'stock'
        ELSE 'unknown'
    END,
    CASE
        WHEN split_part(r.symbol, '.', 2) IN ('SH', 'SZ', 'BJ', 'HK')
            THEN split_part(r.symbol, '.', 2)
        ELSE 'UNKNOWN'
    END,
    'CNY',
    min(r.trade_date),
    false,
    'hithink_finance_dump'
FROM latest r
GROUP BY r.symbol
ON CONFLICT (symbol) DO NOTHING
"""
)


def _duckdb_connection(db: Session):
    try:
        conn = db.connection().connection.driver_connection
        if type(conn).__module__.startswith("duckdb"):
            return conn
    except AttributeError:
        pass
    return None


def _count_batch_rows_in_table(db: Session, *, table: str, batch_id: int) -> int:
    if table not in {"raw.daily_bar", "raw.corporate_action"}:
        raise ValueError(f"unsupported raw table: {table}")
    return int(
        db.scalar(
            text(f"SELECT count(*) FROM {table} WHERE batch_id = :batch_id"),
            {"batch_id": batch_id},
        )
        or 0
    )


def _bulk_import_adjustments_duckdb(
    connection,
    *,
    batch_id: int,
    source_name: str,
    path,
) -> None:
    conflicting_keys = connection.execute(
        """
        WITH normalized AS (
            SELECT upper(thscode) AS symbol,
                epoch_ms(ex_date_ms + 28800000)::DATE AS ex_date,
                sha256(to_json(struct_pack(
                    thscode := thscode,
                    ticker := ticker,
                    ex_date_ms := ex_date_ms,
                    dividend_per_share := dividend_per_share,
                    per_share_bonus := per_share_bonus,
                    allotment_ratio := allotment_ratio,
                    allotment_price := allotment_price,
                    currency := currency
                ))) AS row_hash
            FROM read_parquet(?)
        )
        SELECT symbol, ex_date
        FROM normalized
        GROUP BY symbol, ex_date
        HAVING count(DISTINCT row_hash) > 1
        LIMIT 10
        """,
        [str(path)],
    ).fetchall()
    if conflicting_keys:
        preview = ", ".join(f"{symbol} {ex_date}" for symbol, ex_date in conflicting_keys)
        raise ValueError(
            "corporate-action dump contains conflicting business keys: " + preview
        )
    connection.execute(
        """
        DELETE FROM market.corporate_action AS existing
        USING (
            SELECT DISTINCT upper(thscode) AS symbol,
                epoch_ms(ex_date_ms + 28800000)::DATE AS ex_date,
                CASE
                    WHEN ((coalesce(dividend_per_share, 0) <> 0)::INT
                        + (coalesce(per_share_bonus, 0) <> 0)::INT
                        + (coalesce(allotment_ratio, 0) <> 0)::INT) > 1 THEN 'mixed'
                    WHEN coalesce(dividend_per_share, 0) <> 0 THEN 'cash_dividend'
                    WHEN coalesce(per_share_bonus, 0) <> 0 THEN 'bonus_share'
                    ELSE 'rights_issue'
                END AS action_type
            FROM read_parquet($1)
        ) AS incoming
        WHERE existing.source_name = $2
          AND existing.symbol = incoming.symbol
          AND existing.ex_date = incoming.ex_date
          AND existing.action_type <> incoming.action_type
        """,
        [str(path), source_name],
    )
    connection.execute(
        """
        INSERT INTO raw.corporate_action (
            batch_id, source_name, symbol, ex_date,
            dividend_per_share, per_share_bonus,
            allotment_ratio, allotment_price, raw_payload, row_hash
        )
        WITH normalized AS (
            SELECT
                upper(thscode) AS symbol,
                epoch_ms(ex_date_ms + 28800000)::DATE AS ex_date,
                dividend_per_share, per_share_bonus,
                allotment_ratio, allotment_price,
                to_json(struct_pack(
                    thscode := thscode,
                    ticker := ticker,
                    ex_date_ms := ex_date_ms,
                    dividend_per_share := dividend_per_share,
                    per_share_bonus := per_share_bonus,
                    allotment_ratio := allotment_ratio,
                    allotment_price := allotment_price,
                    currency := currency
                )) AS raw_payload
            FROM read_parquet(?)
        ), content_versions AS (
            SELECT DISTINCT *, sha256(raw_payload) AS row_hash
            FROM normalized
        )
        SELECT
            ?, ?, d.symbol, d.ex_date,
            d.dividend_per_share, d.per_share_bonus,
            d.allotment_ratio, d.allotment_price,
            d.raw_payload, d.row_hash
        FROM content_versions d
        WHERE NOT EXISTS (
            SELECT 1 FROM raw.corporate_action r
            WHERE r.source_name = ?
              AND r.symbol = d.symbol
              AND r.ex_date = d.ex_date
              AND (
                  r.row_hash = d.row_hash
                  OR (
                      r.row_hash IS NULL
                      AND r.dividend_per_share IS NOT DISTINCT FROM d.dividend_per_share
                      AND r.per_share_bonus IS NOT DISTINCT FROM d.per_share_bonus
                      AND r.allotment_ratio IS NOT DISTINCT FROM d.allotment_ratio
                      AND r.allotment_price IS NOT DISTINCT FROM d.allotment_price
                  )
              )
        )
        """,
        [str(path), batch_id, source_name, source_name],
    )
    connection.execute(
        """
        INSERT INTO market.corporate_action (
            symbol, ex_date, action_type,
            cash_dividend, bonus_share_ratio,
            rights_issue_ratio, rights_issue_price,
            source_name, raw_payload
        )
        SELECT
            upper(thscode),
            epoch_ms(ex_date_ms + 28800000)::DATE,
            CASE
                WHEN ((coalesce(dividend_per_share, 0) <> 0)::INT
                    + (coalesce(per_share_bonus, 0) <> 0)::INT
                    + (coalesce(allotment_ratio, 0) <> 0)::INT) > 1 THEN 'mixed'
                WHEN coalesce(dividend_per_share, 0) <> 0 THEN 'cash_dividend'
                WHEN coalesce(per_share_bonus, 0) <> 0 THEN 'bonus_share'
                ELSE 'rights_issue'
            END,
            dividend_per_share,
            per_share_bonus,
            allotment_ratio,
            allotment_price,
            ?,
            to_json(struct_pack(
                thscode := thscode,
                ticker := ticker,
                ex_date_ms := ex_date_ms,
                dividend_per_share := dividend_per_share,
                per_share_bonus := per_share_bonus,
                allotment_ratio := allotment_ratio,
                allotment_price := allotment_price,
                currency := currency
            ))
        FROM read_parquet(?)
        WHERE coalesce(dividend_per_share, 0) >= 0
          AND coalesce(per_share_bonus, 0) >= 0
          AND coalesce(allotment_ratio, 0) >= 0
          AND coalesce(allotment_price, 0) >= 0
          AND (
              coalesce(dividend_per_share, 0) > 0
              OR coalesce(per_share_bonus, 0) > 0
              OR coalesce(allotment_ratio, 0) > 0
          )
        ON CONFLICT (symbol, ex_date, action_type, source_name) DO UPDATE SET
            cash_dividend = EXCLUDED.cash_dividend,
            bonus_share_ratio = EXCLUDED.bonus_share_ratio,
            rights_issue_ratio = EXCLUDED.rights_issue_ratio,
            rights_issue_price = EXCLUDED.rights_issue_price,
            raw_payload = EXCLUDED.raw_payload
        """,
        [source_name, str(path)],
    )


def _to_raw_action(batch_id: int, source_name: str, item: dict) -> RawCorporateAction:
    payload = {key: _json_value(value) for key, value in item.items()}
    return RawCorporateAction(
        batch_id=batch_id,
        source_name=source_name,
        symbol=str(item["thscode"]).upper(),
        ex_date=_action_date(item),
        dividend_per_share=_decimal(item.get("dividend_per_share")),
        per_share_bonus=_decimal(item.get("per_share_bonus")),
        allotment_ratio=_decimal(item.get("allotment_ratio")),
        allotment_price=_decimal(item.get("allotment_price")),
        raw_payload=payload,
        row_hash=sha256(dumps(payload, sort_keys=True).encode("utf-8")).hexdigest(),
    )


def _to_market_action(source_name: str, item: dict) -> dict:
    cash = _decimal(item.get("dividend_per_share"))
    bonus = _decimal(item.get("per_share_bonus"))
    rights = _decimal(item.get("allotment_ratio"))
    kinds = sum(value not in (None, Decimal("0")) for value in (cash, bonus, rights))
    if kinds > 1:
        action_type = "mixed"
    elif cash not in (None, Decimal("0")):
        action_type = "cash_dividend"
    elif bonus not in (None, Decimal("0")):
        action_type = "bonus_share"
    else:
        action_type = "rights_issue"
    return {
        "symbol": str(item["thscode"]).upper(),
        "ex_date": _action_date(item),
        "action_type": action_type,
        "cash_dividend": cash,
        "bonus_share_ratio": bonus,
        "rights_issue_ratio": rights,
        "rights_issue_price": _decimal(item.get("allotment_price")),
        "source_name": source_name,
        "raw_payload": {key: _json_value(value) for key, value in item.items()},
    }


def _action_date(item: dict) -> date:
    return datetime.fromtimestamp(int(item["ex_date_ms"]) / 1000, tz=_SHANGHAI).date()


def _valid_market_action(row: dict) -> bool:
    values = (
        row["cash_dividend"],
        row["bonus_share_ratio"],
        row["rights_issue_ratio"],
        row["rights_issue_price"],
    )
    return all(value is None or value >= 0 for value in values) and any(
        value is not None and value > 0 for value in values[:3]
    )


def _json_value(value: object) -> object:
    if isinstance(value, Decimal):
        return str(value)
    return value
