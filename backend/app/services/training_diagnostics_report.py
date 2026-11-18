from __future__ import annotations

from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.repositories.strategy import BacktestRepository, TrainingArtifactRepository
from app.schemas.training_diagnostics import TrainingDiagnosticsRead

MAX_ARTIFACT_SNAPSHOT_CHARS = 4_000_000


class TrainingDiagnosticsRunNotFound(ValueError):
    pass


class TrainingDiagnosticsReportService:
    def __init__(
        self,
        *,
        backtest_repository: BacktestRepository | None = None,
        artifact_repository: TrainingArtifactRepository | None = None,
    ) -> None:
        self.backtest_repository = backtest_repository or BacktestRepository()
        self.artifact_repository = artifact_repository or TrainingArtifactRepository()

    def get_report(self, db: Session, *, run_id: int) -> TrainingDiagnosticsRead:
        header = self.backtest_repository.training_diagnostics_header(db, run_id=run_id)
        if header is None:
            raise TrainingDiagnosticsRunNotFound("backtest run not found")
        artifact_id = header.get("training_artifact_id")
        if artifact_id is None:
            return self._unavailable(
                run_id=run_id,
                artifact_id=None,
                status="not_recorded",
                reason="run_has_no_training_artifact",
            )
        snapshot_chars = header.get("artifact_snapshot_chars")
        if snapshot_chars is None:
            return self._unavailable(
                run_id=run_id,
                artifact_id=int(artifact_id),
                status="unsupported",
                reason="training_artifact_missing",
            )
        if int(snapshot_chars) > MAX_ARTIFACT_SNAPSHOT_CHARS:
            return self._unavailable(
                run_id=run_id,
                artifact_id=int(artifact_id),
                status="unsupported",
                reason="training_artifact_snapshot_too_large",
            )
        artifact = self.artifact_repository.get_by_id(db, artifact_id=int(artifact_id))
        if artifact is None:
            return self._unavailable(
                run_id=run_id,
                artifact_id=int(artifact_id),
                status="unsupported",
                reason="training_artifact_missing",
            )
        snapshot = artifact.training_input_snapshot
        selection = snapshot.get("selection") if isinstance(snapshot, dict) else None
        diagnostics = selection.get("diagnostics") if isinstance(selection, dict) else None
        if diagnostics is None:
            return self._unavailable(
                run_id=run_id,
                artifact_id=artifact.id,
                artifact_hash=artifact.artifact_hash,
                status="not_recorded",
                reason="insufficient_artifact_evidence",
            )
        if not isinstance(diagnostics, dict):
            return self._unavailable(
                run_id=run_id,
                artifact_id=artifact.id,
                artifact_hash=artifact.artifact_hash,
                status="unsupported",
                reason="invalid_training_diagnostics_contract",
            )
        try:
            return TrainingDiagnosticsRead.model_validate(
                {
                    **diagnostics,
                    "run_id": run_id,
                    "training_artifact_id": artifact.id,
                    "artifact_hash": artifact.artifact_hash,
                }
            )
        except ValidationError:
            return self._unavailable(
                run_id=run_id,
                artifact_id=artifact.id,
                artifact_hash=artifact.artifact_hash,
                status="unsupported",
                reason="invalid_training_diagnostics_contract",
            )

    @staticmethod
    def _unavailable(
        *,
        run_id: int,
        artifact_id: int | None,
        status: str,
        reason: str,
        artifact_hash: str | None = None,
    ) -> TrainingDiagnosticsRead:
        return TrainingDiagnosticsRead(
            run_id=run_id,
            training_artifact_id=artifact_id,
            artifact_hash=artifact_hash,
            status=status,
            reason=reason,
            declarations=[
                "No current factor or price data was used to backfill historical diagnostics.",
                "Training diagnostics never change factor selection, qualification, or execution.",
            ],
        )
