from datetime import date
from pathlib import Path

import duckdb
import pytest

from app.data_sources.hithink_dump import HithinkDump, HithinkDumpKind
from app.repositories.raw import RawDailyBarImportRepository
from app.services.hithink_dump_collection import HithinkDumpCollectionService


class FakeBatch:
    id = 7

    def __init__(self) -> None:
        self.request_params: dict[str, object] = {}


class FakeRepository:
    def __init__(self) -> None:
        self.finished: dict | None = None

    def create_batch(self, db: object, **kwargs: object) -> FakeBatch:
        batch = FakeBatch()
        batch.request_params = kwargs["request_params"]  # type: ignore[assignment]
        return batch

    def add_daily_bar_if_changed(self, db: object, row: object) -> bool:
        db.add_all([row])  # type: ignore[attr-defined]
        return True

    def finish_batch(self, db: object, batch: object, **kwargs: object) -> None:
        self.finished = kwargs


class FakeSecurityRepository:
    def get_security_types(self, db: object, symbols: set[str]) -> dict[str, str]:
        return {symbol: "stock" for symbol in symbols}


class FakeDumpDataSource:
    name = "hithink_finance_dump"
    source_type = "vendor"

    def download(self, kind: HithinkDumpKind) -> HithinkDump:
        assert kind is HithinkDumpKind.RECENT_DAILY
        return HithinkDump(kind=kind, path=Path("daily.parquet"), release_tag="release-1")

    def daily_date_range(self, path: Path) -> tuple[date, date]:
        return date(2026, 7, 16), date(2026, 7, 17)

    def iter_daily_rows(self, path: Path):
        yield [
            {
                "thscode": "600519.SH",
                "date_ms": 1784217600000,
                "open_price": 1400,
                "high_price": 1420,
                "low_price": 1390,
                "close_price": 1410,
                "volume": 100,
                "turnover": 141000,
            }
        ]


class FakeDb:
    def __init__(self) -> None:
        self.rows: list[object] = []
        self.committed = False

    def add_all(self, rows: list[object]) -> None:
        self.rows.extend(rows)

    def flush(self) -> None:
        pass

    def commit(self) -> None:
        self.committed = True

    def rollback(self) -> None:
        pass


def test_dump_collection_imports_unadjusted_rows() -> None:
    repository = FakeRepository()
    db = FakeDb()
    service = HithinkDumpCollectionService(
        data_source=FakeDumpDataSource(),  # type: ignore[arg-type]
        repository=repository,  # type: ignore[arg-type]
        security_repository=FakeSecurityRepository(),  # type: ignore[arg-type]
    )

    result = service.collect(db)  # type: ignore[arg-type]

    assert result.row_count == 1
    assert result.accepted_count == 1
    assert result.skipped_count == 0
    assert result.release_tag == "release-1"
    assert db.rows[0].symbol == "600519.SH"
    assert db.rows[0].adjust_type == "none"
    assert repository.finished == {
        "status": "success",
        "row_count": 1,
        "accepted_count": 1,
        "rejected_count": 0,
    }
    assert db.committed is True


def _create_raw_table(connection: duckdb.DuckDBPyConnection) -> None:
    connection.execute("CREATE SCHEMA raw")
    connection.execute(
        """
        CREATE TABLE raw.daily_bar (
            batch_id BIGINT,
            source_name VARCHAR,
            source_type VARCHAR,
            source_priority INTEGER,
            symbol VARCHAR,
            raw_symbol VARCHAR,
            security_type VARCHAR,
            trade_date DATE,
            adjust_type VARCHAR,
            open DECIMAL(18, 4),
            high DECIMAL(18, 4),
            low DECIMAL(18, 4),
            close DECIMAL(18, 4),
            volume DECIMAL(24, 4),
            amount DECIMAL(24, 4),
            raw_payload JSON,
            row_hash VARCHAR
        )
        """
    )


def _write_daily_dump(
    connection: duckdb.DuckDBPyConnection,
    path: Path,
    *,
    close_prices: list[float],
) -> None:
    connection.execute("DROP TABLE IF EXISTS dump_source")
    connection.execute(
        """
        CREATE TABLE dump_source (
            thscode VARCHAR,
            currency VARCHAR,
            interval VARCHAR,
            adjusted VARCHAR,
            date_ms BIGINT,
            open_price DOUBLE,
            high_price DOUBLE,
            low_price DOUBLE,
            close_price DOUBLE,
            volume DOUBLE,
            turnover DOUBLE
        )
        """
    )
    connection.executemany(
        "INSERT INTO dump_source VALUES (?, 'CNY', '1d', 'none', ?, 10, 12, 9, ?, 100, 1000)",
        [("600000.SH", 1784217600000, close_price) for close_price in close_prices],
    )
    connection.execute("COPY dump_source TO ? (FORMAT PARQUET)", [str(path)])


def test_bulk_dump_import_is_idempotent_and_preserves_revisions(tmp_path: Path) -> None:
    connection = duckdb.connect()
    _create_raw_table(connection)
    path = tmp_path / "daily.parquet"
    repository = RawDailyBarImportRepository()
    _write_daily_dump(connection, path, close_prices=[10.5, 10.5])

    first = repository.bulk_import_hithink_daily_dump(
        connection,
        batch_id=1,
        source_name="hithink_finance_dump",
        source_type="vendor",
        path=path,
    )
    assert first.input_count == 2
    assert first.inserted_count == 1
    assert first.skipped_count == 1
    assert connection.execute(
        "SELECT raw_payload IS NOT NULL, length(row_hash) FROM raw.daily_bar"
    ).fetchone() == (True, 64)

    # Old bulk imports did not populate row_hash. Business-field comparison
    # keeps those rows idempotent during the migration period.
    connection.execute("UPDATE raw.daily_bar SET row_hash = NULL")
    second = repository.bulk_import_hithink_daily_dump(
        connection,
        batch_id=2,
        source_name="hithink_finance_dump",
        source_type="vendor",
        path=path,
    )
    assert second.inserted_count == 0
    assert second.skipped_count == 2

    _write_daily_dump(connection, path, close_prices=[11.0])
    revised = repository.bulk_import_hithink_daily_dump(
        connection,
        batch_id=3,
        source_name="hithink_finance_dump",
        source_type="vendor",
        path=path,
    )
    assert revised.inserted_count == 1
    assert connection.execute(
        "SELECT count(*), count(DISTINCT close) FROM raw.daily_bar"
    ).fetchone() == (2, 2)


def test_bulk_dump_import_rejects_conflicting_rows_in_one_release(tmp_path: Path) -> None:
    connection = duckdb.connect()
    _create_raw_table(connection)
    path = tmp_path / "conflicting.parquet"
    _write_daily_dump(connection, path, close_prices=[10.5, 11.0])

    with pytest.raises(ValueError, match="conflicting business keys"):
        RawDailyBarImportRepository().bulk_import_hithink_daily_dump(
            connection,
            batch_id=1,
            source_name="hithink_finance_dump",
            source_type="vendor",
            path=path,
        )
    assert connection.execute("SELECT count(*) FROM raw.daily_bar").fetchone()[0] == 0
