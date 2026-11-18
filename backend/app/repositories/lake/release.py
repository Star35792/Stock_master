from datetime import date
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.models.lake import Release


class ReleaseRepository:
    def create(
        self,
        db: Session,
        *,
        release_id: str,
        dataset: str,
        schema_version: str,
        min_date: date | None,
        max_date: date | None,
        row_count: int,
        checksum: str,
        parent_release_id: str | None,
        code_version: str,
        quality_status: str,
        metadata: dict[str, Any] | None = None,
    ) -> Release:
        release = self.get_by_release_id(db, release_id=release_id)
        if release is None:
            release = Release(
                release_id=release_id,
                dataset=dataset,
                schema_version=schema_version,
                min_date=min_date,
                max_date=max_date,
                row_count=row_count,
                checksum=checksum,
                parent_release_id=parent_release_id,
                code_version=code_version,
                quality_status=quality_status,
                metadata_=metadata,
            )
            db.add(release)
        else:
            if not self.matches_immutable_facts(
                release,
                dataset=dataset,
                schema_version=schema_version,
                min_date=min_date,
                max_date=max_date,
                row_count=row_count,
                checksum=checksum,
                parent_release_id=parent_release_id,
                code_version=code_version,
                metadata=metadata,
            ):
                raise ValueError(f"release_id collision or immutable fact mismatch: {release_id}")
            if release.quality_status == "rejected":
                raise ValueError(f"rejected release cannot be reused: {release_id}")
        db.flush()
        return release

    @staticmethod
    def matches_immutable_facts(
        release: Release,
        *,
        dataset: str,
        schema_version: str,
        min_date: date | None,
        max_date: date | None,
        row_count: int,
        checksum: str,
        parent_release_id: str | None,
        code_version: str,
        metadata: dict[str, Any] | None,
    ) -> bool:
        return (
            release.dataset == dataset
            and release.schema_version == schema_version
            and release.min_date == min_date
            and release.max_date == max_date
            and release.row_count == row_count
            and release.checksum == checksum
            and release.parent_release_id == parent_release_id
            and release.code_version == code_version
            and release.metadata_ == metadata
        )

    def get_active(self, db: Session, *, dataset: str) -> Release | None:
        return db.scalar(
            select(Release)
            .where(Release.dataset == dataset, Release.is_active.is_(True))
            .order_by(Release.created_at.desc(), Release.id.desc())
            .limit(1)
        )

    def get_by_release_id(self, db: Session, *, release_id: str) -> Release | None:
        return db.scalar(select(Release).where(Release.release_id == release_id))

    def activate(self, db: Session, *, release_id: str) -> Release:
        release = self.get_by_release_id(db, release_id=release_id)
        if release is None:
            raise ValueError(f"release not found: {release_id}")
        if release.quality_status == "rejected":
            raise ValueError(f"rejected release cannot be activated: {release_id}")
        db.execute(
            update(Release)
            .where(Release.dataset == release.dataset, Release.is_active.is_(True))
            .values(is_active=False)
        )
        release.is_active = True
        release.quality_status = "trusted"
        db.add(release)
        db.flush()
        return release

    def list_by_dataset(
        self,
        db: Session,
        *,
        dataset: str,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[Release], int]:
        filters = (Release.dataset == dataset,)
        total = db.scalar(select(func.count()).select_from(Release).where(*filters)) or 0
        items = list(
            db.scalars(
                select(Release)
                .where(*filters)
                .order_by(Release.created_at.desc(), Release.id.desc())
                .limit(limit)
                .offset(offset)
            )
        )
        return items, int(total)
