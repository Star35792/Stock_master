from __future__ import annotations

import json
import logging
from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from hashlib import sha256
from threading import Lock
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from app.config.settings import settings
from app.database.session import SessionLocal
from app.repositories.system import DataQualityIssueRepository, JobLogRepository
from app.scheduler.database_job_lock import database_job_lock
from app.schemas.operations import (
    OperationsActionPreview,
    OperationsActionRead,
    OperationsActionStartRequest,
    OperationsActionType,
)
from app.services.auto_data_sync import AutoDataSyncService, completed_daily_cutoff
from app.services.daily_bar_market_builder import DailyBarMarketBuildService
from app.services.factor_batch_profiler import FactorBatchProfilerService
from app.services.gap_backfill import MissingDailyBarBackfillService
from app.services.job_lease import merge_job_lease_metadata

logger = logging.getLogger(__name__)

ACTION_JOB_NAME = "operator_action"
TERMINAL_STATUSES = {"success", "partial", "failed"}


class OperationsActionConflictError(ValueError):
    pass


class OperationsActionService:
    """Plan and execute operator-requested maintenance through existing services."""

    def __init__(
        self,
        *,
        session_factory: sessionmaker[Session] = SessionLocal,
        job_repository: JobLogRepository | None = None,
        issue_repository: DataQualityIssueRepository | None = None,
        job_lock: Lock = database_job_lock,
    ) -> None:
        self.session_factory = session_factory
        self.job_repository = job_repository or JobLogRepository()
        self.issue_repository = issue_repository or DataQualityIssueRepository()
        self.job_lock = job_lock

    def preview(
        self,
        db: Session,
        *,
        action_type: OperationsActionType,
        parameters: dict[str, Any],
    ) -> OperationsActionPreview:
        normalized = self._normalize_parameters(action_type, parameters)
        if action_type == "daily_data_sync":
            cutoff = completed_daily_cutoff()
            previous = AutoDataSyncService().repository.completed_through(db)
            configured = bool(settings.hithink_finance_api_key)
            return OperationsActionPreview(
                action_type=action_type,
                executable=configured,
                summary=(
                    f"同步可信日线至 {cutoff.isoformat()}；当前完成至 "
                    f"{previous.isoformat() if previous else '尚无成功水位'}。"
                ),
                parameters=normalized,
                warnings=[
                    "同步会写入 raw 并经质量校验发布到 market；不会绕过可信数据链路。",
                    "因子画像不会随本动作自动重建，可在同步成功后单独触发。",
                ],
                checks={
                    "api_key_configured": configured,
                    "completed_through": previous.isoformat() if previous else None,
                    "safe_cutoff": cutoff.isoformat(),
                },
            )

        if action_type == "daily_bar_gap_backfill":
            plan = MissingDailyBarBackfillService().plan(
                db, **self._typed_gap_parameters(normalized)
            )
            actionable = len(plan.candidates)
            return OperationsActionPreview(
                action_type=action_type,
                executable=actionable > 0 or plan.unresolved_rows == 0,
                summary=(
                    f"发现 {actionable} 条可回填记录；另有 {plan.unresolved_rows} 条需人工诊断。"
                ),
                parameters=normalized,
                warnings=[
                    "只回填计划中缺失或必要字段不完整的 qfq 日线，并在 raw 留存来源批次。",
                    "回填后会重建对应 market 行并再次检查缺口；来源冲突不会被强行覆盖。",
                ],
                checks=self._json_ready(plan),
            )

        return OperationsActionPreview(
            action_type=action_type,
            executable=True,
            summary=(
                f"重建 {normalized['start_date']} 至 {normalized['end_date']} 的活跃因子画像。"
            ),
            parameters=normalized,
            warnings=[
                "画像用于研究诊断，不会自动改变因子方向或策略资格。",
                "任务可能耗时较长，失败因子会逐项记录并以 partial 状态返回。",
            ],
            checks={"date_range_days": (
                date.fromisoformat(normalized["end_date"])
                - date.fromisoformat(normalized["start_date"])
            ).days + 1},
        )

    def start(
        self,
        db: Session,
        *,
        request: OperationsActionStartRequest,
    ) -> OperationsActionRead:
        preview = self.preview(
            db,
            action_type=request.action_type,
            parameters=request.parameters,
        )
        if not preview.executable:
            raise ValueError(preview.summary)

        request_hash = self._request_hash(request.action_type, preview.parameters)
        for existing in self.job_repository.list_recent_by_name(
            db, job_name=ACTION_JOB_NAME
        ):
            metadata = existing.metadata_ or {}
            if metadata.get("idempotency_key") != request.idempotency_key:
                continue
            if metadata.get("request_hash") != request_hash:
                raise OperationsActionConflictError(
                    "idempotency_key was already used for a different action request"
                )
            return self.to_read(existing)

        metadata = merge_job_lease_metadata(
            db,
            {
                "action_type": request.action_type,
                "parameters": preview.parameters,
                "idempotency_key": request.idempotency_key,
                "request_hash": request_hash,
                "phase": "queued",
                "preview": preview.model_dump(mode="json"),
            },
            repository=self.job_repository,
        )
        job = self.job_repository.create(
            db,
            job_name=ACTION_JOB_NAME,
            job_type="repair",
            metadata=metadata,
        )
        db.commit()
        db.refresh(job)
        return self.to_read(job)

    def get(self, db: Session, *, action_id: int) -> OperationsActionRead | None:
        job = self.job_repository.get(db, action_id)
        if job is None or job.job_name != ACTION_JOB_NAME:
            return None
        return self.to_read(job)

    def execute(self, action_id: int) -> None:
        with self.session_factory() as db:
            job = self.job_repository.get(db, action_id)
            if job is None or job.job_name != ACTION_JOB_NAME or job.status != "running":
                return
            self._set_phase(db, job, "waiting_for_write_slot")

        try:
            with self.job_lock:
                with self.session_factory() as db:
                    job = self.job_repository.get(db, action_id)
                    if job is None or job.status != "running":
                        return
                    self._set_phase(db, job, "running")
                    metadata = job.metadata_ or {}
                    action_type = metadata["action_type"]
                    parameters = dict(metadata.get("parameters") or {})
                    result, completion_status = self._execute_action(
                        db,
                        action_id=action_id,
                        action_type=action_type,
                        parameters=parameters,
                    )
                    self.job_repository.finish(
                        db,
                        job_log=job,
                        status=completion_status,
                        metadata={"phase": "completed", "result": result},
                    )
                    db.commit()
        except BaseException as exc:  # preserve an auditable terminal state
            logger.exception("Operator action failed: id=%s", action_id)
            with self.session_factory() as db:
                job = self.job_repository.get(db, action_id)
                if job is not None and job.status == "running":
                    self.job_repository.finish(
                        db,
                        job_log=job,
                        status="failed",
                        error_message=f"{type(exc).__name__}: {exc}",
                        metadata={"phase": "failed"},
                    )
                    db.commit()

    def _execute_action(
        self,
        db: Session,
        *,
        action_id: int,
        action_type: OperationsActionType,
        parameters: dict[str, Any],
    ) -> tuple[dict[str, Any], str]:
        if action_type == "daily_data_sync":
            result = AutoDataSyncService().sync(db)
            return self._json_ready(result), "success"

        if action_type == "daily_bar_gap_backfill":
            call_parameters = self._typed_gap_parameters(parameters)
            backfill = MissingDailyBarBackfillService().backfill(db, **call_parameters)
            market_build = None
            if backfill.batch_no:
                market_build = DailyBarMarketBuildService().build_from_raw(
                    db, **call_parameters
                )
            recheck = MissingDailyBarBackfillService().plan(db, **call_parameters)
            closed = len(recheck.candidates) == 0 and recheck.unresolved_rows == 0
            issue_id = parameters.get("issue_id")
            issue_updated = False
            if closed and isinstance(issue_id, int):
                issue_updated = self.issue_repository.update_status(
                    db,
                    issue_id=issue_id,
                    status="fixed",
                    resolved_by=f"operator_action:{action_id}",
                ) is not None
            payload = {
                "backfill": self._json_ready(backfill),
                "market_build": self._json_ready(market_build),
                "recheck": self._json_ready(recheck),
                "problem_closed": closed,
                "quality_issue_updated": issue_updated,
            }
            status = "success" if closed and backfill.status == "success" else "partial"
            return payload, status

        result = FactorBatchProfilerService().run(
            db,
            start_date=date.fromisoformat(parameters["start_date"]),
            end_date=date.fromisoformat(parameters["end_date"]),
            adjust_type=parameters["adjust_type"],
            categories=set(parameters["categories"]) if parameters.get("categories") else None,
            excluded_categories=(
                set(parameters["excluded_categories"])
                if parameters.get("excluded_categories")
                else None
            ),
            parent_job_id=action_id,
        )
        return self._json_ready(result), "partial" if result.failed_count else "success"

    def _normalize_parameters(
        self,
        action_type: OperationsActionType,
        parameters: dict[str, Any],
    ) -> dict[str, Any]:
        if action_type == "daily_data_sync":
            if parameters:
                raise ValueError("daily_data_sync does not accept parameters")
            return {}
        if action_type == "daily_bar_gap_backfill":
            symbols = sorted({
                str(symbol).strip().upper()
                for symbol in parameters.get("symbols", [])
                if str(symbol).strip()
            })
            if not symbols:
                raise ValueError("symbols must not be empty")
            if len(symbols) > 50:
                raise ValueError("at most 50 symbols may be repaired in one action")
            start = date.fromisoformat(str(parameters.get("start_date", "")))
            end = date.fromisoformat(str(parameters.get("end_date", "")))
            if start > end:
                raise ValueError("start_date must be earlier than or equal to end_date")
            if (end - start).days > 31:
                raise ValueError("gap backfill range must not exceed 32 calendar days")
            if parameters.get("adjust_type", "qfq") != "qfq":
                raise ValueError("daily bar gap backfill currently supports qfq only")
            normalized: dict[str, Any] = {
                "symbols": symbols,
                "start_date": start.isoformat(),
                "end_date": end.isoformat(),
                "adjust_type": "qfq",
            }
            issue_id = parameters.get("issue_id")
            if issue_id is not None:
                normalized["issue_id"] = int(issue_id)
            return normalized

        start = date.fromisoformat(str(parameters.get("start_date", "")))
        end = date.fromisoformat(str(parameters.get("end_date", "")))
        if start > end:
            raise ValueError("start_date must be earlier than or equal to end_date")
        if (end - start).days > 366:
            raise ValueError("factor profile rebuild range must not exceed 367 calendar days")
        adjust_type = str(parameters.get("adjust_type", "qfq"))
        if adjust_type not in {"none", "qfq", "hfq"}:
            raise ValueError("adjust_type must be one of: none, qfq, hfq")
        return {
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "adjust_type": adjust_type,
            "categories": self._string_list(parameters.get("categories")),
            "excluded_categories": self._string_list(
                parameters.get("excluded_categories")
            ),
        }

    @staticmethod
    def _typed_gap_parameters(parameters: dict[str, Any]) -> dict[str, Any]:
        return {
            "symbols": list(parameters["symbols"]),
            "start_date": date.fromisoformat(parameters["start_date"]),
            "end_date": date.fromisoformat(parameters["end_date"]),
            "adjust_type": parameters["adjust_type"],
        }

    @staticmethod
    def _string_list(value: Any) -> list[str] | None:
        if value is None:
            return None
        if not isinstance(value, list):
            raise ValueError("category filters must be arrays")
        items = sorted({str(item).strip() for item in value if str(item).strip()})
        return items or None

    @staticmethod
    def _request_hash(action_type: str, parameters: dict[str, Any]) -> str:
        raw = json.dumps(
            {"action_type": action_type, "parameters": parameters},
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
        return sha256(raw.encode("utf-8")).hexdigest()

    @staticmethod
    def _json_ready(value: Any) -> Any:
        if value is None:
            return None
        if is_dataclass(value) and not isinstance(value, type):
            value = asdict(value)
        if isinstance(value, dict):
            return {
                str(key): OperationsActionService._json_ready(item)
                for key, item in value.items()
            }
        if isinstance(value, (list, tuple, set)):
            return [OperationsActionService._json_ready(item) for item in value]
        if isinstance(value, (date, datetime)):
            return value.isoformat()
        if isinstance(value, (str, int, float, bool)):
            return value
        return str(value)

    @staticmethod
    def _set_phase(db: Session, job: Any, phase: str) -> None:
        job.metadata_ = {**(job.metadata_ or {}), "phase": phase}
        db.add(job)
        db.commit()

    @staticmethod
    def to_read(job: Any) -> OperationsActionRead:
        metadata = job.metadata_ or {}
        phase = str(metadata.get("phase") or "running")
        status = job.status
        if status == "running" and phase == "queued":
            status = "queued"
        return OperationsActionRead(
            action_id=job.id,
            action_type=metadata["action_type"],
            status=status,
            phase=phase,
            parameters=dict(metadata.get("parameters") or {}),
            result=metadata.get("result"),
            error_message=job.error_message,
            created_at=job.created_at,
            started_at=job.started_at,
            finished_at=job.finished_at,
        )
