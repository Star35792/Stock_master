from datetime import date
from pathlib import Path

from app.data_sources.hithink_dump import HithinkDump, HithinkDumpKind
from app.services.hithink_dump_collection import HithinkDumpCollectionService


class FakeBatch:
    id = 7


class FakeRepository:
    def __init__(self) -> None:
        self.finished: dict | None = None

    def create_batch(self, db: object, **kwargs: object) -> FakeBatch:
        return FakeBatch()

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
