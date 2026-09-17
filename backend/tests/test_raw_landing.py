from datetime import date

from app.config.settings import Settings
from app.services.raw_landing import RawLandingStore


def test_raw_landing_default_root_is_configured() -> None:
    assert Settings().raw_landing_root == "data/raw_landing"


def test_raw_landing_writes_immutable_jsonl_object(tmp_path) -> None:
    result = RawLandingStore(tmp_path).write_jsonl(
        source_name="hithink",
        dataset="daily_bar",
        batch_no="batch-1",
        rows=[{"symbol": "000001.SZ", "close": "10.25"}],
        partition_date=date(2026, 9, 17),
    )

    assert result.path.exists()
    assert result.row_count == 1
    assert result.size_bytes == result.path.stat().st_size
    assert len(result.sha256) == 64
    assert result.path.read_text(encoding="utf-8") == (
        '{"close": "10.25", "symbol": "000001.SZ"}\n'
    )
