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
            release = Release(release_id=release_id, dataset=dataset)
            db.add(release)
        elif release.dataset != dataset or release.checksum != checksum:
            raise ValueError(f"release_id collision: {release_id}")

        release.schema_version = schema_version
        release.min_date = min_date
        release.max_date = max_date
        release.row_count = row_count
        release.checksum = checksum
        release.parent_release_id = parent_release_id
        release.code_version = code_version
        release.metadata_ = metadata
        if not release.is_active:
            release.quality_status = quality_status
        db.flush()
        return release

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
