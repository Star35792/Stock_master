from __future__ import annotations

import json
import logging
from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from hashlib import sha256
from pathlib import Path
from threading import Lock
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session, sessionmaker

from app.config.settings import settings
from app.database.session import SessionLocal
from app.repositories.factor.recovery import FactorRecoveryRepository
from app.repositories.strategy.price_value_screen import PriceValueScreenRepository
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
from app.services.factor_recovery import FactorRecoveryService
from app.services.gap_backfill import MissingDailyBarBackfillService
from app.services.job_lease import merge_job_lease_metadata
from app.services.job_logging import logged_job
from app.services.price_value_input_prep import PriceValueInputPrepService
from app.services.price_value_screen import PriceValueScreenService
from app.utils.duckdb_write_lock import duckdb_write_lock

logger = logging.getLogger(__name__)

ACTION_JOB_NAME = "operator_action"
TERMINAL_STATUSES = {"success", "partial", "failed"}
_start_lock = Lock()


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
        self.write_lock_path = Path(settings.metadata_db_path).parent / "duckdb_write.lock"
        self.factor_recovery = FactorRecoveryService()

    def preview(
        self,
        db: Session,
        *,
        action_type: OperationsActionType,
        parameters: dict[str, Any],
    ) -> OperationsActionPreview:
        normalized = self._normalize_parameters(action_type, parameters)
        if action_type == "factor_values_rebuild":
            try:
                _context, checks = self.factor_recovery.inspect(db, normalized["original_run_id"])
                executable = not checks["reason_codes"]
                summary = (
                    f"定向重算 {checks['factor_name']}@{checks['factor_version']}；"
                    f"目标窗口 {checks['target_start_date']} 至 {checks['target_end_date']}。"
                )
            except (ValueError, TypeError) as exc:
                executable = False
                summary = str(exc)
                checks = {"reason_codes": ["ORIGINAL_RUN_UNSUPPORTED"], "detail": str(exc)}
            return OperationsActionPreview(
                action_type=action_type,
                executable=executable,
                summary=summary,
                parameters=normalized,
                checks=checks,
                warnings=[
                    "只重算原记录指定的单个因子，新建运行并记录当前代码和实际输入。",
                    "旧失败和空结果保留；重算不改变公式、方向、PIT 或策略资格。",
                ],
            )
        if action_type == "alternative_data_sync":
            configured = "hithink" not in normalized["sources"] or bool(
                settings.hithink_finance_api_key
            )
            return OperationsActionPreview(
                action_type=action_type,
                executable=configured,
                parameters=normalized,
                summary="重试选定来源的当前情绪与资金流采集，保留原始证据并复查结果。",
                checks={"sources": normalized["sources"], "configured": configured},
                warnings=[
                    "只运行现有来源；不补造历史快照，也不保证宏观、政策和盈利预期有上游。",
                    "空数据或部分来源失败不会标记为问题已解决。",
                ],
            )
        if action_type == "price_value_input_prepare":
            repository = PriceValueScreenRepository()
            latest = repository.latest_open_date(
                db,
                before=datetime.now(ZoneInfo("Asia/Shanghai")).date(),
            )
            requested = normalized.get("price_date")
            matches = latest is not None and (requested is None or requested == latest.isoformat())
            sync = repository.completed_daily_sync(db, trade_date=latest) if latest else None
            if latest is not None and requested is None:
                normalized["price_date"] = latest.isoformat()
            normalized["observation_mode"] = "current_research_catchup"
            return OperationsActionPreview(
                action_type=action_type,
                executable=matches and sync is not None,
                parameters=normalized,
                summary="为最新已完成交易日补齐价格价值研究输入，并重新生成当前研究报告。",
                checks={
                    "latest_completed_trade_date": latest.isoformat() if latest else None,
                    "requested_date_is_latest": matches,
                    "daily_sync_ready": sync is not None,
                    "pit_verified": False,
                    "research_only": True,
                },
                warnings=[
                    "使用真实采集时刻的当前研究补采，不认证旧日历史 PIT。",
                    "价格匹配、分所覆盖和披露 ROE 闸门保持，报告可能继续阻断。",
                ],
            )
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

        profile_checks = FactorRecoveryRepository().profile_input_summary(
            db,
            start_date=date.fromisoformat(normalized["start_date"]),
            end_date=date.fromisoformat(normalized["end_date"]),
            categories=normalized.get("categories"),
            excluded_categories=normalized.get("excluded_categories"),
        )
        return OperationsActionPreview(
            action_type=action_type,
            executable=profile_checks["available_count"] > 0,
            summary=(
                f"重建 {normalized['start_date']} 至 {normalized['end_date']} 的活跃因子画像。"
            ),
            parameters=normalized,
            warnings=[
                "画像用于研究诊断，不会自动改变因子方向或策略资格。",
                "任务可能耗时较长，失败因子会逐项记录并以 partial 状态返回。",
            ],
            checks={
                **profile_checks,
                "date_range_days": (
                    date.fromisoformat(normalized["end_date"])
                    - date.fromisoformat(normalized["start_date"])
                ).days
                + 1,
            },
        )

    def start(
        self,
        db: Session,
        *,
        request: OperationsActionStartRequest,
    ) -> OperationsActionRead:
        # Single API worker, plus the shared OS writer guard for maintenance jobs.
        with _start_lock, duckdb_write_lock(self.write_lock_path):
            return self._start_locked(db, request=request)

    def _start_locked(
        self,
        db: Session,
        *,
        request: OperationsActionStartRequest,
    ) -> OperationsActionRead:
        normalized = self._normalize_parameters(request.action_type, request.parameters)
        request_hash = self._request_hash(request.action_type, normalized)
        existing = self.job_repository.get_operator_request(
            db,
            idempotency_key=request.idempotency_key,
        )
        if existing is not None:
            if (existing.metadata_ or {}).get("request_hash") != request_hash:
                raise OperationsActionConflictError(
                    "idempotency_key was already used for a different action request"
                )
            return self.to_read(existing)
        preview = self.preview(
            db,
            action_type=request.action_type,
            parameters=request.parameters,
        )
        if not preview.executable:
            raise ValueError(preview.summary)

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
        with self.job_lock:
            try:
                with duckdb_write_lock(self.write_lock_path):
                    self._execute_locked(action_id)
            except BaseException as exc:  # preserve an auditable terminal state
                logger.exception("Operator action failed: id=%s", action_id)
                with duckdb_write_lock(self.write_lock_path), self.session_factory() as db:
                    job = self.job_repository.get(db, action_id)
                    if (
                        job is not None
                        and job.job_name == ACTION_JOB_NAME
                        and job.status == "running"
                    ):
                        self.job_repository.finish(
                            db,
                            job_log=job,
                            status="failed",
                            error_message=f"{type(exc).__name__}: {exc}",
                            metadata={"phase": "failed"},
                        )
                        db.commit()

    def _execute_locked(self, action_id: int) -> None:
        with self.session_factory() as db:
            job = self.job_repository.get(db, action_id)
            if job is None or job.job_name != ACTION_JOB_NAME or job.status != "running":
                return
            self._set_phase(db, job, "running")
            metadata = job.metadata_ or {}
            action_type = metadata["action_type"]
            parameters = dict(metadata.get("parameters") or {})
            checked = self.preview(db, action_type=action_type, parameters=parameters)
            if not checked.executable:
                raise ValueError(f"执行前复查被阻断：{checked.summary}; {checked.checks}")
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
            return {
                "sync": self._json_ready(result),
                "problem_closed": True,
                "recheck": {"coverage": result.coverage, "scope": "unadjusted_daily_prices_only"},
            }, "success"

        if action_type == "factor_values_rebuild":
            return self.factor_recovery.execute(
                db,
                original_run_id=parameters["original_run_id"],
                action_id=action_id,
            )

        if action_type == "alternative_data_sync":
            # Reuse source services without re-entering a scheduler's writer lock.
            from app.services.eastmoney_collection import EastmoneyFlowCollectionService
            from app.services.hithink_alternative_collection import (
                HithinkAlternativeCollectionService,
            )

            outcomes = {}
            factories = {
                "hithink": HithinkAlternativeCollectionService,
                "eastmoney": EastmoneyFlowCollectionService,
            }
            with logged_job(
                db,
                job_name="alternative_data_collection",
                job_type="schedule",
                batch_id=action_id,
                metadata={
                    "operator_action_id": action_id,
                    "requested_sources": parameters["sources"],
                },
            ) as collection_job:
                for source in parameters["sources"]:
                    try:
                        with self.session_factory() as source_db:
                            collected = factories[source]().collect_current(source_db)
                        outcomes[source] = self._json_ready(collected)
                    except Exception as exc:
                        outcomes[source] = {"error": f"{type(exc).__name__}: {exc}"}
                successful = [
                    source
                    for source, value in outcomes.items()
                    if not value.get("error") and not value.get("empty_datasets")
                ]
                closed = len(successful) == len(outcomes)
                status = "success" if closed else "partial" if successful else "failed"
                # A partial-source retry must not certify the other source as recovered.
                collection_job.status = status if len(outcomes) == 2 else "partial"
                collection_job.rows_inserted = sum(
                    value.get("normalized_rows", 0) for value in outcomes.values()
                )
                collection_job.metadata_ = {
                    **(collection_job.metadata_ or {}),
                    "sources": outcomes,
                    "unchecked_sources": sorted({"hithink", "eastmoney"} - set(outcomes)),
                    "failures": {
                        source: value["error"]
                        for source, value in outcomes.items()
                        if value.get("error")
                    },
                    "empty_datasets": [
                        dataset
                        for value in outcomes.values()
                        for dataset in value.get("empty_datasets", [])
                    ],
                }
            return {
                "collection_job_id": collection_job.id,
                "sources": outcomes,
                "problem_closed": closed,
                "recheck": {
                    "successful_sources": successful,
                    "requested_sources": parameters["sources"],
                    "unchecked_sources": sorted({"hithink", "eastmoney"} - set(outcomes)),
                },
            }, status

        if action_type == "price_value_input_prepare":
            prepared = PriceValueInputPrepService().run(
                db,
                observation_mode="current_research_catchup",
                price_date=date.fromisoformat(parameters["price_date"]),
            )
            report = PriceValueScreenService().run(db)
            closed = report.get("status") != "blocked"
            return {
                "input_preparation": self._json_ready(prepared),
                "problem_closed": closed,
                "recheck": {
                    "report_status": report.get("status"),
                    "global_reason_codes": report.get("global_reason_codes", []),
                    "target_trade_date": report.get("target_trade_date"),
                    "pit_verified": False,
                },
            }, "success" if closed else "partial"

        if action_type == "daily_bar_gap_backfill":
            call_parameters = self._typed_gap_parameters(parameters)
            backfill = MissingDailyBarBackfillService().backfill(db, **call_parameters)
            market_build = None
            if backfill.batch_no:
                market_build = DailyBarMarketBuildService().build_from_raw(db, **call_parameters)
            recheck = MissingDailyBarBackfillService().plan(db, **call_parameters)
            closed = len(recheck.candidates) == 0 and recheck.unresolved_rows == 0
            issue_id = parameters.get("issue_id")
            issue_updated = False
            if closed and isinstance(issue_id, int):
                issue_updated = (
                    self.issue_repository.update_status(
                        db,
                        issue_id=issue_id,
                        status="fixed",
                        resolved_by=f"operator_action:{action_id}",
                    )
                    is not None
                )
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
        empty = [
            f"{item.factor_name}@{item.factor_version}"
            for item in result.results
            if not item.profile_rows
        ]
        closed = (
            result.success_count > 0
            and not result.failed_count
            and result.total_profile_rows > 0
            and not empty
        )
        return {
            "profiles": self._json_ready(result),
            "problem_closed": closed,
            "recheck": {
                "profile_rows": result.total_profile_rows,
                "failed_count": result.failed_count,
                "empty_factor_count": len(empty),
                "empty_factor_examples": empty[:20],
            },
        }, "success" if closed else "partial"

    def _normalize_parameters(
        self,
        action_type: OperationsActionType,
        parameters: dict[str, Any],
    ) -> dict[str, Any]:
        if action_type == "factor_values_rebuild":
            if set(parameters) != {"original_run_id"}:
                raise ValueError("factor_values_rebuild only accepts original_run_id")
            run_id = parameters["original_run_id"]
            if type(run_id) is not int or run_id <= 0:
                raise ValueError("original_run_id must be a positive integer")
            return {"original_run_id": run_id}
        if action_type == "alternative_data_sync":
            if set(parameters) - {"sources"}:
                raise ValueError("alternative_data_sync only accepts sources")
            sources = parameters.get("sources", ["hithink", "eastmoney"])
            if (
                not isinstance(sources, list)
                or not sources
                or any(source not in {"hithink", "eastmoney"} for source in sources)
            ):
                raise ValueError("sources must select hithink and/or eastmoney")
            return {"sources": sorted(set(sources))}
        if action_type == "price_value_input_prepare":
            if set(parameters) - {"price_date", "observation_mode"}:
                raise ValueError("unsupported price/value preparation parameters")
            if parameters.get("observation_mode", "current_research_catchup") != (
                "current_research_catchup"
            ):
                raise ValueError("only current_research_catchup is supported")
            normalized = {"observation_mode": "current_research_catchup"}
            if parameters.get("price_date") is not None:
                normalized["price_date"] = date.fromisoformat(parameters["price_date"]).isoformat()
            return normalized
        if action_type == "daily_data_sync":
            if parameters:
                raise ValueError("daily_data_sync does not accept parameters")
            return {}
        if action_type == "daily_bar_gap_backfill":
            if not isinstance(parameters.get("symbols"), list):
                raise ValueError("symbols must be an array")
            symbols = sorted(
                {
                    str(symbol).strip().upper()
                    for symbol in parameters.get("symbols", [])
                    if str(symbol).strip()
                }
            )
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
            "excluded_categories": self._string_list(parameters.get("excluded_categories")),
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
                str(key): OperationsActionService._json_ready(item) for key, item in value.items()
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
