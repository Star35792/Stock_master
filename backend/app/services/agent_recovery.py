"""Bounded read retries and read-only reconciliation of original write evidence."""

import errno
import sqlite3
from collections.abc import Callable
from decimal import Decimal, InvalidOperation
from time import sleep, time
from urllib.error import HTTPError, URLError

from pydantic import ValidationError
from sqlalchemy.exc import OperationalError

from app.repositories.agent_recovery import AGENT_EVIDENCE_KEY, AgentRecoveryRepository
from app.schemas.agent_recovery import (
    AgentActionIdentity,
    AgentReadCheckpoint,
    AgentRecoveryRead,
)
from app.utils.agent_identity import parameters_hash
from app.utils.duckdb_write_lock import DuckDbWriteLockTimeout

MAINTENANCE_TOOLS = {
    "sync_daily_data": "daily_data_sync",
    "repair_daily_gaps": "daily_bar_gap_backfill",
    "rebuild_factor_profiles": "factor_profile_rebuild",
}


def read_failure_kind(exc):
    """Recognize typed transient failures; never infer safety from an error body."""
    if isinstance(exc, HTTPError):
        if exc.code == 429:
            return "rate_limit"
        return "upstream" if exc.code in {502, 503, 504} else "permanent"
    if isinstance(exc, DuckDbWriteLockTimeout):
        return "temporary_lock"
    if isinstance(exc, OperationalError):
        original = exc.orig
        if isinstance(original, sqlite3.OperationalError) and getattr(
            original, "sqlite_errorcode", None
        ) in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}:
            return "temporary_lock"
        return "permanent"
    if isinstance(exc, (TimeoutError, ConnectionError)):
        return "network"
    if isinstance(exc, URLError):
        return read_failure_kind(exc.reason) if isinstance(exc.reason, Exception) else "permanent"
    if isinstance(exc, OSError) and exc.errno in {
        errno.ETIMEDOUT,
        errno.ECONNRESET,
        errno.ECONNREFUSED,
        errno.ECONNABORTED,
        errno.ENETUNREACH,
        errno.EHOSTUNREACH,
    }:
        return "network"
    return "permanent"


class ReadRecoveryStopped(ValueError):
    pass


class ReadRecoveryPolicy:
    """Persist every reservation and result. Save must perform the session CAS.

    The caller owns authorization and merges checkpoints without overwriting
    cancellation. A restored `calling` checkpoint is unknown, never replayed.
    No callback executes while a SQLite transaction remains open.
    """

    def __init__(self, *, clock=time, wait=sleep):
        self.clock, self.wait = clock, wait

    def run(
        self,
        checkpoint: AgentReadCheckpoint,
        *,
        operation_hash: str,
        read: Callable,
        save: Callable,
        cancelled: Callable = lambda: False,
    ):
        state = checkpoint.model_copy(deep=True)
        if state.operation_hash != operation_hash:
            raise ReadRecoveryStopped("查询输入已变化，请重新规划。")
        if state.status == "calling":
            state.status = "unknown"
            save(state.model_copy(deep=True))
        if state.status not in {"ready", "retryable"}:
            raise ReadRecoveryStopped("原查询不可自动重放，请核对状态或重新规划。")
        while True:
            if cancelled():
                state.status = "cancelled"
                save(state.model_copy(deep=True))
                raise ReadRecoveryStopped("已取消后续查询；已开始的调用不保证回滚。")
            now = self.clock()
            if state.attempts >= 3 or now >= state.deadline:
                state.status = "exhausted"
                save(state.model_copy(deep=True))
                raise ReadRecoveryStopped("只读查询重试次数或时间已达上限，请重新规划。")
            if state.next_retry_at > now:
                # A future or corrupted timestamp cannot create an unbounded wait.
                delay = min(state.next_retry_at - now, state.deadline - now, 2.0)
                self.wait(delay)
                if self.clock() < state.next_retry_at:
                    raise ReadRecoveryStopped("查询尚在重试等待期，请稍后核验。")
                continue
            state.attempts += 1
            state.status = "calling"
            save(state.model_copy(deep=True))
            try:
                result = read()
            except Exception as exc:
                state.error_kind = read_failure_kind(exc)
                if cancelled():
                    state.status = "cancelled"
                elif state.error_kind == "permanent":
                    state.status = "failed"
                elif state.attempts >= 3 or self.clock() >= state.deadline:
                    state.status = "exhausted"
                else:
                    state.status = "retryable"
                    state.next_retry_at = self.clock() + min(state.attempts, 2)
                save(state.model_copy(deep=True))
                if state.status == "retryable":
                    continue
                raise ReadRecoveryStopped("查询未完成，请核对输入、数据资格或服务状态。") from None
            # Even a successful result is not usable until its checkpoint is saved.
            state.status = (
                "cancelled"
                if cancelled()
                else "exhausted"
                if self.clock() >= state.deadline
                else "success"
            )
            save(state.model_copy(deep=True))
            if state.status == "cancelled":
                raise ReadRecoveryStopped("已取消后续步骤，原查询结果不推进计划。")
            if state.status == "exhausted":
                raise ReadRecoveryStopped("查询超过本轮时间上限，结果不推进计划。")
            return result


class AgentEvidenceRecovery:
    def __init__(self, repository=None):
        self.repository = repository or AgentRecoveryRepository()

    @staticmethod
    def identity_matches(raw, expected):
        try:
            return AgentActionIdentity.model_validate(raw) == expected
        except (ValidationError, TypeError):
            return False

    @staticmethod
    def artifact_matches_parameters(artifact, tool, parameters):
        """Compare original requests to persisted inputs, without reading current data."""
        if tool == "create_portfolio_from_backtest":
            snapshot = artifact.input_snapshot or {}
            return all(
                snapshot.get(key) == parameters[argument]
                for key, argument in (("name", "name"), ("source_run_id", "run_id"))
                if argument in parameters
            )
        if tool != "run_factor_backtest":
            return False
        contract = artifact.parameters or {}
        for field in ("name", "start_date", "end_date"):
            if field in parameters:
                actual = getattr(artifact, field)
                if field.endswith("date"):
                    actual = actual.isoformat()
                if actual != parameters[field]:
                    return False
        try:
            if "initial_cash" in parameters and artifact.initial_cash != Decimal(
                parameters["initial_cash"]
            ):
                return False
        except (InvalidOperation, TypeError, ValueError):
            return False
        if "top_n" in parameters and contract.get("top_n") != parameters["top_n"]:
            return False
        if "factor_name" in parameters:
            specs = contract.get("factor_specs")
            if not isinstance(specs, list) or len(specs) != 1 or not isinstance(specs[0], dict):
                return False
            if any(
                specs[0].get(key) != parameters[key]
                for key in ("factor_name", "factor_version", "higher_is_better")
            ):
                return False
        return True

    def inspect(
        self,
        db,
        research_db,
        *,
        identity: AgentActionIdentity,
        parameters: dict,
        task_id=None,
        normalized_parameters=None,
    ):
        """Inspect original facts. Never start a job or rewrite its audit status."""
        report = AgentRecoveryRead(
            status="unknown",
            reason="未找到可核验的原任务，不能重放写入。",
            confirmation_id=identity.confirmation_id,
        )
        if parameters_hash(identity.tool, parameters) != identity.parameters_hash:
            report.status, report.reason = "conflict", "冻结参数与确认哈希不一致，不能核验。"
            return report
        if identity.tool not in {
            *MAINTENANCE_TOOLS,
            "run_factor_backtest",
            "create_portfolio_from_backtest",
        }:
            report.status, report.reason = "conflict", "工具不在可恢复的写动作清单中。"
            return report
        jobs = self.repository.jobs(db, identity.confirmation_id)
        runs = (
            self.repository.backtests(research_db, identity.confirmation_id)
            if identity.tool == "run_factor_backtest"
            else []
        )
        plans = (
            self.repository.portfolios(db, identity.confirmation_id)
            if identity.tool == "create_portfolio_from_backtest"
            else []
        )
        if len(jobs) > 1 or len(runs) > 1 or len(plans) > 1:
            report.status, report.reason = "conflict", "原确认存在重复任务或产物，需人工诊断。"
            return report
        artifact = (runs or plans or [None])[0]
        if artifact is not None:
            report.artifact_type = "backtest" if runs else "portfolio"
            report.artifact_id = artifact.id
            report.artifact_status = artifact.status if runs else "persisted"
            data = artifact.parameters if runs else artifact.input_snapshot
            report.artifact_verified = (
                self.identity_matches((data or {}).get(AGENT_EVIDENCE_KEY), identity)
                and artifact.code_version == identity.code_version
                and self.artifact_matches_parameters(artifact, identity.tool, parameters)
            )
            if not report.artifact_verified:
                report.status, report.reason = "conflict", "原产物身份不一致或缺失，不能补造血缘。"
                return report
        if not jobs:
            if artifact is not None:
                report.reason = "原产物存在，但主库任务审计缺失；保留产物并阻断后续。"
            return report
        job = jobs[0]
        report.task_id = job.id
        metadata = job.metadata_ or {}
        operator = identity.tool in MAINTENANCE_TOOLS
        if (
            (task_id is not None and job.id != task_id)
            or not self.identity_matches(metadata.get(AGENT_EVIDENCE_KEY), identity)
            or metadata.get("parameters")
            != (normalized_parameters if normalized_parameters is not None else parameters)
            or metadata.get("code_version", identity.code_version) != identity.code_version
            or (
                operator
                and (
                    job.job_name != "operator_action"
                    or metadata.get("action_type") != MAINTENANCE_TOOLS[identity.tool]
                )
            )
            or (
                not operator
                and (job.job_name != "agent_action" or metadata.get("tool") != identity.tool)
            )
        ):
            report.status, report.reason = "conflict", "原任务身份不一致或缺失，不能据此恢复。"
            return report
        completion = metadata.get("completion_status")
        if completion == "unknown":
            report.reason = "原任务完成状态未知；已存在产物保留，后续保持阻断。"
            return report
        if job.status in {"failed", "partial"}:
            report.status, report.reason = job.status, "原任务未完整成功；保留已有产物并重新规划。"
            return report
        if job.status == "running":
            report.status, report.reason = "running", "原任务仍在运行；只核查进度，不重新执行。"
            return report
        if job.status != "success" or completion not in {None, "success"}:
            report.reason = "原任务完成证据不明确，后续保持阻断。"
            return report
        if identity.tool in {"run_factor_backtest", "create_portfolio_from_backtest"}:
            result = metadata.get("result") or {}
            key = "run_id" if runs else "plan_id"
            if artifact is None or (runs and artifact.status != "success"):
                report.reason = "任务成功但原产物缺失或未成功，后续保持阻断。"
                return report
            if not isinstance(result, dict) or result.get(key) != artifact.id:
                report.status, report.reason = "conflict", "任务结果与原产物ID不一致，需人工诊断。"
                return report
            if runs:
                report.outputs["run_id"] = artifact.id
        report.outputs["task_id"] = job.id
        report.status, report.reason = "verified", "原任务及其产物身份核验通过，不提升研究资格。"
        return report
