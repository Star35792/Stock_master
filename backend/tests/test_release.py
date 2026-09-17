from datetime import date
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker

from app.database.base import Base
from app.models.lake import Release
from app.repositories.lake import ReleaseRepository
from app.services.release import ReleaseService


def _session_factory():
    engine = create_engine("duckdb:///:memory:")
    with engine.begin() as connection:
        connection.execute(text("CREATE SCHEMA lake"))
    Base.metadata.create_all(engine, tables=[Release.__table__])
    return sessionmaker(bind=engine, expire_on_commit=False)


def _result() -> SimpleNamespace:
    return SimpleNamespace(
        row_count=3,
        min_date="2026-01-05",
        max_date="2026-01-07",
    )


def test_release_idempotent() -> None:
    factory = _session_factory()
    service = ReleaseService()
    with factory() as db:
        first = service.build_release(
            db,
            dataset="curated.prices_qfq",
            curate_result=_result(),
            checksum="a" * 64,
            parent_release_id="parent",
            code_version="test",
        )
        second = service.build_release(
            db,
            dataset="curated.prices_qfq",
            curate_result=_result(),
            checksum="a" * 64,
            parent_release_id="parent",
            code_version="test",
        )
        db.commit()

        assert first.release_id == second.release_id
        assert first.release_id == ReleaseService.release_id_hash(
            "curated.prices_qfq", "a" * 64
        )
        assert len(list(db.scalars(select(Release)))) == 1
        assert first.min_date == date(2026, 1, 5)


def test_activate_preserves_one_active_release_per_dataset() -> None:
    factory = _session_factory()
    service = ReleaseService()
    repository = ReleaseRepository()
    with factory() as db:
        first = service.build_release(
            db,
            dataset="curated.prices_qfq",
            curate_result=_result(),
            checksum="a" * 64,
            parent_release_id=None,
            code_version="one",
        )
        second = service.build_release(
            db,
            dataset="curated.prices_qfq",
            curate_result=_result(),
            checksum="b" * 64,
            parent_release_id=None,
            code_version="two",
        )
        repository.activate(db, release_id=first.release_id)
        repository.activate(db, release_id=second.release_id)
        db.commit()

        active = repository.get_active(db, dataset="curated.prices_qfq")
        assert active is not None
        assert active.release_id == second.release_id
        assert active.quality_status == "trusted"
        assert first.is_active is False


def test_compute_parquet_checksum_is_path_order_independent(tmp_path: Path) -> None:
    root = tmp_path / "dataset"
    (root / "year=2026").mkdir(parents=True)
    (root / "year=2025").mkdir(parents=True)
    (root / "year=2026" / "part.parquet").write_bytes(b"later")
    (root / "year=2025" / "part.parquet").write_bytes(b"earlier")
    (root / "ignored.txt").write_text("ignored")

    checksum = ReleaseService.compute_parquet_checksum(root)
    manifest = ReleaseService.build_parquet_manifest(root)

    assert checksum == ReleaseService.compute_parquet_checksum(root)
    assert len(checksum) == 64
    assert [item["path"] for item in manifest] == [
        "year=2025/part.parquet",
        "year=2026/part.parquet",
    ]
    assert all(len(item["sha256"]) == 64 for item in manifest)


def test_compute_parquet_checksum_includes_relative_paths(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    (first / "a").mkdir(parents=True)
    (second / "b").mkdir(parents=True)
    (first / "a" / "part.parquet").write_bytes(b"same")
    (second / "b" / "part.parquet").write_bytes(b"same")

    assert ReleaseService.compute_parquet_checksum(first) != (
        ReleaseService.compute_parquet_checksum(second)
    )
