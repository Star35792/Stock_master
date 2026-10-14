from datetime import date
from typing import Any

from sqlalchemy.orm import Session

from app.models.strategy import TrainingArtifact


class TrainingArtifactRepository:
    def create(
        self,
        db: Session,
        *,
        window_id: str,
        strategy_name: str,
        factor_specs: list[dict[str, Any]],
        training_start_date: date,
        training_end_date: date,
        test_start_date: date,
        test_end_date: date,
        training_input_snapshot: dict[str, Any],
        artifact_hash: str,
        code_version: str,
        data_version: str,
    ) -> TrainingArtifact:
        artifact = TrainingArtifact(
            window_id=window_id,
            strategy_name=strategy_name,
            factor_specs=factor_specs,
            training_start_date=training_start_date,
            training_end_date=training_end_date,
            test_start_date=test_start_date,
            test_end_date=test_end_date,
            training_input_snapshot=training_input_snapshot,
            artifact_hash=artifact_hash,
            code_version=code_version,
            data_version=data_version,
        )
        db.add(artifact)
        db.flush()
        return artifact

    def get_by_id(self, db: Session, *, artifact_id: int) -> TrainingArtifact | None:
        return db.get(TrainingArtifact, artifact_id)

    def get_by_window_id(self, db: Session, *, window_id: str) -> TrainingArtifact | None:
        from sqlalchemy import select

        return db.scalar(
            select(TrainingArtifact).where(TrainingArtifact.window_id == window_id)
        )
