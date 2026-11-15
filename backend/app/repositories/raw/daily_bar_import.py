from dataclasses import dataclass
from datetime import date
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.lake.security_type import security_type_case_sql
from app.models.raw import ImportBatch, RawDailyBar


@dataclass(frozen=True)
class BulkDailyBarImportResult:
    input_count: int
    inserted_count: int
    skipped_count: int


@dataclass
class DailyBarContentIndex:
    hashes: set[tuple]
    legacy_values: set[tuple]


class RawDailyBarImportRepository:
    def create_batch(
        self,
        db: Session,
        *,
        batch_no: str,
        source_name: str,
        source_type: str,
        dataset: str,
        request_params: dict,
        file_path: str | None = None,
    ) -> ImportBatch:
        batch = ImportBatch(
            batch_no=batch_no,
            source_name=source_name,
            source_type=source_type,
            dataset=dataset,
            file_path=file_path,
            request_params=request_params,
            status="running",
        )
        db.add(batch)
        db.flush()
        return batch

    def add_daily_bar(self, db: Session, row: RawDailyBar) -> None:
        db.add(row)

    def add_daily_bar_if_changed(self, db: Session, row: RawDailyBar) -> bool:
        filters = [
            RawDailyBar.source_name == row.source_name,
            RawDailyBar.symbol == row.symbol,
            RawDailyBar.trade_date == row.trade_date,
            RawDailyBar.adjust_type == row.adjust_type,
            RawDailyBar.security_type == row.security_type,
        ]
        for field_name in ("open", "high", "low", "close", "volume", "amount"):
            column = getattr(RawDailyBar, field_name)
            value = getattr(row, field_name)
            filters.append(column.is_(None) if value is None else column == value)
        if db.scalar(select(RawDailyBar.id).where(*filters).limit(1)) is not None:
            return False
        db.add(row)
        return True

    def load_daily_bar_content_index(
        self,
        db: Session,
        *,
        source_name: str,
        start_date: date,
        end_date: date,
        adjust_type: str,
    ) -> DailyBarContentIndex:
        """Read the requested source/window once before a repeated sync."""
        columns = (
            RawDailyBar.source_name,
            RawDailyBar.symbol,
            RawDailyBar.trade_date,
            RawDailyBar.adjust_type,
            RawDailyBar.security_type,
            RawDailyBar.row_hash,
            RawDailyBar.open,
            RawDailyBar.high,
            RawDailyBar.low,
            RawDailyBar.close,
            RawDailyBar.volume,
            RawDailyBar.amount,
        )
        existing = db.execute(
            select(*columns).distinct().where(
                RawDailyBar.source_name == source_name,
                RawDailyBar.adjust_type == adjust_type,
                RawDailyBar.trade_date >= start_date,
                RawDailyBar.trade_date <= end_date,
            )
        ).all()
        hashes: set[tuple] = set()
        legacy_values: set[tuple] = set()
        for record in existing:
            key = tuple(record[:5])
            if record[5]:
                hashes.add((*key, record[5]))
            else:
                legacy_values.add((*key, *record[6:]))

        return DailyBarContentIndex(hashes=hashes, legacy_values=legacy_values)

    def add_daily_bars_if_changed(
        self,
        db: Session,
        rows: list[RawDailyBar],
        *,
        content_index: DailyBarContentIndex,
    ) -> int:
        """Insert distinct content versions without a query per vendor row."""
        pending: list[RawDailyBar] = []
        for row in rows:
            key = (
                row.source_name, row.symbol, row.trade_date,
                row.adjust_type, row.security_type,
            )
            content_hash = (*key, row.row_hash)
            values = (
                *key, row.open, row.high, row.low, row.close,
                row.volume, row.amount,
            )
            if (
                (row.row_hash and content_hash in content_index.hashes)
                or values in content_index.legacy_values
            ):
                continue
            pending.append(row)
            if row.row_hash:
                content_index.hashes.add(content_hash)
            else:
                content_index.legacy_values.add(values)
        db.add_all(pending)
        return len(pending)

    def bulk_import_hithink_daily_dump(
        self,
        connection,
        *,
        batch_id: int,
        source_name: str,
        source_type: str,
        path: str | Path,
    ) -> BulkDailyBarImportResult:
        """Insert new HiThink dump content versions without duplicating identical rows."""
        input_count = int(
            connection.execute(
                "SELECT count(*) FROM read_parquet(?) WHERE adjusted = 'none'",
                [str(path)],
            ).fetchone()[0]
        )
        conflict_count = int(
            connection.execute(
                """
                SELECT count(*)
                FROM (
                    SELECT upper(thscode), epoch_ms(date_ms + 28800000)::DATE
                    FROM read_parquet(?)
                    WHERE adjusted = 'none'
                    GROUP BY 1, 2
                    HAVING count(DISTINCT sha256(CAST(json_object(
                        'thscode', thscode,
                        'currency', currency,
                        'interval', interval,
                        'adjusted', adjusted,
                        'date_ms', date_ms,
                        'open_price', open_price,
                        'high_price', high_price,
                        'low_price', low_price,
                        'close_price', close_price,
                        'volume', volume,
                        'turnover', turnover
                    ) AS VARCHAR))) > 1
                ) conflicts
                """,
                [str(path)],
            ).fetchone()[0]
        )
        if conflict_count:
            raise ValueError(
                f"HiThink daily dump contains {conflict_count} conflicting business keys"
            )

        symbol_expr = "upper(thscode)"
        connection.execute(
            f"""
            INSERT INTO raw.daily_bar (
                batch_id, source_name, source_type, source_priority,
                symbol, raw_symbol, security_type, trade_date, adjust_type,
                open, high, low, close, volume, amount, raw_payload, row_hash
            )
            WITH normalized AS (
                SELECT
                    {symbol_expr} AS symbol,
                    {security_type_case_sql(symbol_expr)} AS security_type,
                    epoch_ms(date_ms + 28800000)::DATE AS trade_date,
                    CAST(open_price AS DECIMAL(18, 4)) AS open,
                    CAST(high_price AS DECIMAL(18, 4)) AS high,
                    CAST(low_price AS DECIMAL(18, 4)) AS low,
                    CAST(close_price AS DECIMAL(18, 4)) AS close,
                    CAST(volume AS DECIMAL(24, 4)) AS volume,
                    CAST(turnover AS DECIMAL(24, 4)) AS amount,
                    json_object(
                        'thscode', thscode,
                        'currency', currency,
                        'interval', interval,
                        'adjusted', adjusted,
                        'date_ms', date_ms,
                        'open_price', open_price,
                        'high_price', high_price,
                        'low_price', low_price,
                        'close_price', close_price,
                        'volume', volume,
                        'turnover', turnover
                    ) AS raw_payload
                FROM read_parquet(?)
                WHERE adjusted = 'none'
            ), content_versions AS (
                SELECT DISTINCT *, sha256(raw_payload) AS row_hash
                FROM normalized
            )
            SELECT
                ?, ?, ?, 40,
                d.symbol, d.symbol, d.security_type, d.trade_date, 'none',
                d.open, d.high, d.low, d.close, d.volume, d.amount,
                d.raw_payload, d.row_hash
            FROM content_versions d
            WHERE NOT EXISTS (
                SELECT 1
                FROM raw.daily_bar r
                WHERE r.source_name = ?
                  AND r.symbol = d.symbol
                  AND r.trade_date = d.trade_date
                  AND r.adjust_type = 'none'
                  AND (
                      r.row_hash = d.row_hash
                      OR (
                          r.row_hash IS NULL
                          AND r.security_type = d.security_type
                          AND r.open IS NOT DISTINCT FROM d.open
                          AND r.high IS NOT DISTINCT FROM d.high
                          AND r.low IS NOT DISTINCT FROM d.low
                          AND r.close IS NOT DISTINCT FROM d.close
                          AND r.volume IS NOT DISTINCT FROM d.volume
                          AND r.amount IS NOT DISTINCT FROM d.amount
                      )
                  )
            )
            """,
            [
                str(path),
                batch_id,
                source_name,
                source_type,
                source_name,
            ],
        )
        inserted_count = int(
            connection.execute(
                "SELECT count(*) FROM raw.daily_bar WHERE batch_id = ?",
                [batch_id],
            ).fetchone()[0]
        )
        return BulkDailyBarImportResult(
            input_count=input_count,
            inserted_count=inserted_count,
            skipped_count=input_count - inserted_count,
        )

    def finish_batch(
        self,
        db: Session,
        batch: ImportBatch,
        *,
        status: str,
        row_count: int,
        accepted_count: int,
        rejected_count: int,
        error_message: str | None = None,
    ) -> None:
        batch.status = status
        batch.row_count = row_count
        batch.accepted_count = accepted_count
        batch.rejected_count = rejected_count
        batch.error_message = error_message
        batch.finished_at = db.scalar(select(func.now()))
