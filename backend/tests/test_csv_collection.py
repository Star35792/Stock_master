from pathlib import Path

from app.services.csv_collection import (
    CsvDailyBarCollectionService,
    _normalize_symbol,
    _parse_csv_row,
)


class FakeDb:
    def __init__(self) -> None:
        self.committed = False
        self.rolled_back = False

    def commit(self) -> None:
        self.committed = True

    def rollback(self) -> None:
        self.rolled_back = True


class FakeBatch:
    id = 1


class FakeRepository:
    def __init__(self) -> None:
        self.batch_kwargs: dict | None = None
        self.rows: list[object] = []
        self.finish_kwargs: dict | None = None

    def create_batch(self, db: object, **kwargs: object) -> FakeBatch:
        self.batch_kwargs = kwargs
        return FakeBatch()

    def add_daily_bar(self, db: object, row: object) -> None:
        self.rows.append(row)

    def finish_batch(self, db: object, batch: object, **kwargs: object) -> None:
        self.finish_kwargs = kwargs


def test_csv_collection_imports_daily_bars(tmp_path: Path) -> None:
    csv_path = tmp_path / "daily.csv"
    csv_path.write_text(
        "\n".join(
            [
                "symbol,trade_date,open,high,low,close,pre_close,volume,amount,adjust_type",
                "600519,20260717,1200,1250,1190,1242.47,1230,1000,1242470,none",
                "000001,2026-07-17,10,11,9,10.5,10.1,2000,21000,none",
            ]
        ),
        encoding="utf-8",
    )
    db = FakeDb()
    repository = FakeRepository()

    result = CsvDailyBarCollectionService(repository=repository).collect_daily_bars(
        db,  # type: ignore[arg-type]
        file_path=csv_path,
        source_name="broker_csv",
    )

    assert result.status == "success"
    assert result.accepted_count == 2
    assert result.rejected_count == 0
    assert repository.batch_kwargs is not None
    assert repository.batch_kwargs["source_name"] == "broker_csv"
    assert repository.batch_kwargs["file_path"] == str(csv_path)
    assert repository.rows[0].symbol == "600519.SH"
    assert repository.rows[0].raw_symbol == "600519"
    assert repository.rows[1].symbol == "000001.SZ"
    assert repository.finish_kwargs is not None
    assert repository.finish_kwargs["accepted_count"] == 2
    assert db.committed is True


def test_csv_collection_rejects_invalid_rows(tmp_path: Path) -> None:
    csv_path = tmp_path / "daily.csv"
    csv_path.write_text(
        "\n".join(
            [
                "symbol,trade_date,open,high,low,close",
                "123456,20260717,1,1,1,1",
                "600519,20260717,1200,1250,1190,1242.47",
            ]
        ),
        encoding="utf-8",
    )
    db = FakeDb()
    repository = FakeRepository()

    result = CsvDailyBarCollectionService(repository=repository).collect_daily_bars(
        db,  # type: ignore[arg-type]
        file_path=csv_path,
    )

    assert result.status == "partial"
    assert result.accepted_count == 1
    assert result.rejected_count == 1
    assert "cannot infer exchange" in (result.error_message or "")
    assert len(repository.rows) == 1
    assert db.committed is True
    assert db.rolled_back is False


def test_csv_row_parser_accepts_optional_fields() -> None:
    row = _parse_csv_row(
        {
            "symbol": "159915",
            "trade_date": "2026-07-17",
            "open": "1.00",
            "high": "1.10",
            "low": "0.99",
            "close": "1.05",
            "security_type": "",
        }
    )

    assert row.symbol == "159915.SZ"
    assert row.security_type == "etf"


def test_csv_symbol_normalization_keeps_explicit_exchange() -> None:
    assert _normalize_symbol("600519.sh") == "600519.SH"
