import hashlib
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest
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


def _legacy_id(dataset: str, checksum: str) -> str:
    return hashlib.sha256(f"{dataset}|{checksum}".encode()).hexdigest()[:16]


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
            dataset="curated.prices_qfq",
            checksum="a" * 64,
            schema_version="v1",
            min_date=date(2026, 1, 5),
            max_date=date(2026, 1, 7),
            row_count=3,
            parent_release_id="parent",
            code_version="test",
        )
        assert len(list(db.scalars(select(Release)))) == 1
        assert first.min_date == date(2026, 1, 5)


def test_same_content_with_changed_lineage_keeps_both_release_facts() -> None:
    factory = _session_factory()
    service = ReleaseService()
    with factory() as db:
        first = service.build_release(
            db,
            dataset="curated.prices_qfq",
            curate_result=_result(),
            checksum="a" * 64,
            parent_release_id="parent-one",
            code_version="one",
            metadata={"files": ["one"]},
        )
        first_id = first.release_id
        second = service.build_release(
            db,
            dataset="curated.prices_qfq",
            curate_result=_result(),
            checksum="a" * 64,
            parent_release_id="parent-two",
            code_version="two",
            metadata={"files": ["one"]},
        )
        db.commit()

        assert second.release_id != first_id
        assert first.parent_release_id == "parent-one"
        assert first.code_version == "one"
        assert len(list(db.scalars(select(Release)))) == 2


def test_existing_release_cannot_be_rewritten_or_revived_after_rejection() -> None:
    factory = _session_factory()
    repository = ReleaseRepository()
    with factory() as db:
        release = ReleaseService().build_release(
            db,
            dataset="curated.prices_qfq",
            curate_result=_result(),
            checksum="a" * 64,
            parent_release_id="parent",
            code_version="one",
        )
        db.commit()
        with pytest.raises(ValueError, match="immutable fact mismatch"):
            repository.create(
                db,
                release_id=release.release_id,
                dataset="curated.prices_qfq",
                schema_version="v1",
                min_date=date(2026, 1, 5),
                max_date=date(2026, 1, 7),
                row_count=3,
                checksum="a" * 64,
                parent_release_id="different-parent",
                code_version="one",
                quality_status="pending",
            )
        release.quality_status = "rejected"
        db.commit()
        with pytest.raises(ValueError, match="rejected release cannot be reused"):
            ReleaseService().build_release(
                db,
                dataset="curated.prices_qfq",
                curate_result=_result(),
                checksum="a" * 64,
                parent_release_id="parent",
                code_version="one",
            )
        with pytest.raises(ValueError, match="rejected release cannot be activated"):
            repository.activate(db, release_id=release.release_id)


def test_legacy_short_id_remains_referenced_when_new_identity_is_published() -> None:
    factory = _session_factory()
    with factory() as db:
        legacy = Release(
            release_id=_legacy_id("curated.prices_qfq", "a" * 64),
            dataset="curated.prices_qfq",
            schema_version="v1",
            min_date=date(2026, 1, 5),
            max_date=date(2026, 1, 7),
            row_count=3,
            checksum="a" * 64,
            parent_release_id="old-parent",
            code_version="old-code",
            quality_status="trusted",
            is_active=True,
            metadata_={"files": []},
        )
        db.add(legacy)
        db.commit()

        new = ReleaseService().build_release(
            db,
            dataset="curated.prices_qfq",
            curate_result=_result(),
            checksum="a" * 64,
            parent_release_id="new-parent",
            code_version="new-code",
            metadata={"files": []},
        )
        db.commit()

        assert len(new.release_id) == 64
        assert new.release_id != legacy.release_id
        assert legacy.parent_release_id == "old-parent"
        assert legacy.code_version == "old-code"
        assert legacy.is_active is True
        assert len(list(db.scalars(select(Release)))) == 2


def test_exact_legacy_release_is_reused_after_upgrade() -> None:
    factory = _session_factory()
    with factory() as db:
        legacy = Release(
            release_id=_legacy_id("curated.prices_qfq", "a" * 64),
            dataset="curated.prices_qfq",
            schema_version="v1",
            min_date=date(2026, 1, 5),
            max_date=date(2026, 1, 7),
            row_count=3,
            checksum="a" * 64,
            parent_release_id="same-parent",
            code_version="same-code",
            quality_status="trusted",
            is_active=True,
            metadata_={"files": []},
        )
        db.add(legacy)
        db.commit()

        rerun = ReleaseService().build_release(
            db,
            dataset="curated.prices_qfq",
            curate_result=_result(),
            checksum="a" * 64,
            parent_release_id="same-parent",
            code_version="same-code",
            metadata={"files": []},
        )
        db.commit()

        assert rerun.release_id == legacy.release_id
        assert rerun.quality_status == "trusted"
        assert len(list(db.scalars(select(Release)))) == 1


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
