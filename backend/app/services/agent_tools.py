"""Allowlisted business capabilities. The model never supplies SQL or financial facts."""

import json
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from hashlib import sha256
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.config.settings import settings
from app.database.positions_session import PositionsSessionLocal
from app.database.research_session import ResearchSessionLocal
from app.database.session import SessionLocal
from app.portfolio import PortfolioAsset, PortfolioConstraints
from app.repositories.agent_recovery import (
    AGENT_EVIDENCE_KEY,
    AgentBacktestRepository,
    AgentRecoveryRepository,
)
from app.repositories.system import JobLogRepository
from app.scheduler.database_job_lock import database_job_lock
from app.schemas.agent_recovery import AgentActionIdentity
from app.schemas.operations import OperationsActionStartRequest
from app.schemas.strategy import FactorSelectionBacktestRequest
from app.services.agent_recovery import AgentEvidenceRecovery
from app.services.backtest import DailyBacktestService
from app.services.backtest_query import BacktestQueryService
from app.services.data_quality import DataQualityQueryService
from app.services.factor import FactorQueryService
from app.services.job_lease import merge_job_lease_metadata
from app.services.market import MarketQueryService
from app.services.operations import OperationsOverviewService
from app.services.operations_actions import OperationsActionService
from app.services.portfolio import PortfolioConstructionService
from app.services.position_book import PositionBookService
from app.services.price_value_screen import PriceValueScreenService
from app.services.research_preflight import check_factor_selection
from app.services.research_workflow import ResearchWorkflowService
from app.services.strategy_research import StrategyResearchService
from app.strategies import FactorScoreSpec
from app.utils.agent_identity import parameters_hash
from app.utils.duckdb_write_lock import duckdb_write_lock


def json_ready(value):
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    return OperationsActionService._json_ready(value)


class ToolArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class LimitArgs(ToolArgs):
    limit: int = Field(default=10, ge=1, le=30)


class MarketArgs(ToolArgs):
    symbol: str = Field(pattern=r"^\d{6}\.(SH|SZ|BJ)$")


class QualityArgs(LimitArgs):
    symbol: str | None = Field(default=None, pattern=r"^\d{6}\.(SH|SZ|BJ)$")


class RunArgs(ToolArgs):
    run_id: int = Field(gt=0)


class BookArgs(ToolArgs):
    book_id: int = Field(gt=0)


class TaskArgs(ToolArgs):
    task_id: int = Field(gt=0)


class GapArgs(ToolArgs):
    symbols: list[str] = Field(min_length=1, max_length=50)
    start_date: date
    end_date: date


class ProfileArgs(ToolArgs):
    start_date: date
    end_date: date
    categories: list[str] | None = Field(default=None, max_length=10)


class BacktestArgs(ToolArgs):
    name: str = Field(min_length=1, max_length=100)
    factor_name: str = Field(min_length=1, max_length=100)
    factor_version: str = Field(min_length=1, max_length=100)
    start_date: date
    end_date: date
    initial_cash: Decimal = Field(gt=0, le=Decimal("100000000"), max_digits=16, decimal_places=4)
    top_n: int = Field(default=10, ge=1, le=50)
    higher_is_better: bool = True

    @model_validator(mode="after")
    def validate_window(self):
        if not 0 <= (self.end_date - self.start_date).days <= 366:
            raise ValueError("智能体单次回测窗口须在 1 至 367 个自然日内")
        if self.end_date >= datetime.now(ZoneInfo("Asia/Shanghai")).date():
            raise ValueError("回测结束日期必须早于今天")
        return self


class PlanArgs(RunArgs):
    name: str = Field(min_length=1, max_length=100)


@dataclass(frozen=True)
class Tool:
    title: str
    description: str
    arguments: type[ToolArgs]
    writes: bool = False


TOOLS = {
    "research_overview": Tool("研究概览", "查询最近回测、组合草案、持仓账本和告警。", ToolArgs),
    "operations_overview": Tool("数据与任务概览", "查询数据覆盖、质量和后台任务状态。", LimitArgs),
    "quality_issues": Tool("质量问题", "查询未解决的质量问题。不能直接关闭问题。", QualityArgs),
    "market_summary": Tool("可信行情", "查询证券可信日线摘要，包含日期和质量状态。", MarketArgs),
    "factor_definitions": Tool("因子目录", "查询因子名称、版本与定义；回测前核对。", LimitArgs),
    "backtest_result": Tool("回测结果", "查询真实 run_id 的结果、资格和期末持仓。", RunArgs),
    "position_holdings": Tool("持仓分析", "只读查询人工账本的持仓和逐股报价日期。", BookArgs),
    "latest_screen": Tool("最新选股报告", "读取现有价值初筛报告；保留日期与资格限制。", ToolArgs),
    "task_status": Tool("任务进度", "查询已确认任务的真实状态；完成后再安排下一步。", TaskArgs),
    "sync_daily_data": Tool("采集可信日线", "预览日线采集，经用户按钮确认才执行。", ToolArgs, True),
    "repair_daily_gaps": Tool(
        "回填日线缺口", "预览最多50证券、32自然日的qfq缺口回填。", GapArgs, True
    ),
    "rebuild_factor_profiles": Tool(
        "重建因子画像", "预览因子画像重建，经用户确认执行。", ProfileArgs, True
    ),
    "run_factor_backtest": Tool(
        "因子探索回测", "预检单因子探索回测，保留费用、次日执行及数据闸门。", BacktestArgs, True
    ),
    "create_portfolio_from_backtest": Tool(
        "生成研究组合草案",
        "仅从成功回测的期末持仓生成草案，零现仓假设；不能模拟执行。",
        PlanArgs,
        True,
    ),
}

MAINTENANCE = {
    "sync_daily_data": "daily_data_sync",
    "repair_daily_gaps": "daily_bar_gap_backfill",
    "rebuild_factor_profiles": "factor_profile_rebuild",
}


def tool_definitions():
    return [
        {
            "type": "function",
            "function": {
                "name": name,
                "description": spec.description,
                "parameters": spec.arguments.model_json_schema(),
            },
        }
        for name, spec in TOOLS.items()
    ]


def default_constraints():
    return PortfolioConstraints(
        max_symbol_weight=settings.portfolio_max_symbol_weight,
        max_industry_weight=settings.portfolio_max_industry_weight,
        cash_reserve_rate=settings.portfolio_cash_reserve_rate,
    )


class AgentTools:
    def __init__(self):
        self.jobs = JobLogRepository()
        self.operations = OperationsActionService()

    @contextmanager
    def context(self):
        # Same OS research lock as the existing request dependency and scripts.
        with duckdb_write_lock("data/research_write.lock", timeout_seconds=3):
            with (
                SessionLocal() as db,
                ResearchSessionLocal() as research,
                PositionsSessionLocal() as positions,
            ):
                yield db, research, positions

    def read(self, name, args):
        if name == "task_status":
            return self.task(args["task_id"])
        with self.context() as (db, research, positions):
            match name:
                case "research_overview":
                    return ResearchWorkflowService().workbench(
                        db, positions_db=positions, research_db=research
                    )
                case "operations_overview":
                    return OperationsOverviewService().get_overview(
                        db, research_db=research, **args
                    )
                case "quality_issues":
                    return DataQualityQueryService().list_issues(
                        db,
                        status="open",
                        severity=None,
                        issue_type=None,
                        start_date=None,
                        end_date=None,
                        offset=0,
                        **args,
                    )
                case "market_summary":
                    return MarketQueryService().get_market_summary(db, adjust_type="none", **args)
                case "factor_definitions":
                    return FactorQueryService().list_definitions(
                        db,
                        name=None,
                        category=None,
                        frequency=None,
                        is_active=True,
                        offset=0,
                        **args,
                    )
                case "backtest_result":
                    return ResearchWorkflowService().pipeline(db, research_db=research, **args)
                case "position_holdings":
                    return PositionBookService().get_holdings(positions, db, **args)
                case "latest_screen":
                    return PriceValueScreenService().latest(db)
        raise ValueError("unknown tool")

    @staticmethod
    def backtest_payload(args):
        return FactorSelectionBacktestRequest(
            name=args["name"],
            factor_specs=[
                {
                    "factor_name": args["factor_name"],
                    "factor_version": args["factor_version"],
                    "higher_is_better": args["higher_is_better"],
                }
            ],
            top_n=args["top_n"],
            start_date=args["start_date"],
            end_date=args["end_date"],
            initial_cash=args["initial_cash"],
            code_version=settings.app_code_version,
            data_version=settings.research_release_tag or "trusted-source-runtime",
            fee_model_version="a_share_cost_v2",
            portfolio_constraints={},
        )

    @staticmethod
    def plan_source(db, research, args):
        report = BacktestQueryService().get_final_holdings(
            db, research_db=research, run_id=args["run_id"]
        )
        if report is None or not report.holdings or report.total_equity <= 0:
            raise ValueError("需要成功且有非空期末持仓、正净值的回测")
        if len(report.holdings) > 50 or any(
            row.close <= 0 or not row.industry or row.security_type not in {"stock", "etf"}
            for row in report.holdings
        ):
            raise ValueError("期末持仓超过50只或缺少可用价格、行业、证券类型")
        source = report.model_dump(mode="json")
        digest = sha256(json.dumps(source, sort_keys=True).encode()).hexdigest()
        return report, digest

    def preview(self, name, args):
        with self.context() as (db, research, _):
            if name in MAINTENANCE:
                return self.operations.preview(
                    db, action_type=MAINTENANCE[name], parameters=args
                ).model_dump(mode="json")
            if name == "run_factor_backtest":
                payload = self.backtest_payload(args)
                checks = check_factor_selection(
                    db,
                    payload=payload,
                    service=StrategyResearchService(),
                    portfolio_constraints=default_constraints(),
                )
                return {
                    "executable": checks["runnable_now"],
                    "summary": "单因子探索性研究，预检可运行不代表证据合格或收益保证。",
                    "checks": checks,
                    "resolved_parameters": payload.model_dump(mode="json"),
                    "cost_note": "沿用 a_share_cost_v2：佣金含最低5元，另计既有税费与2bps滑点。",
                }
            report, digest = self.plan_source(db, research, args)
            return {
                "executable": True,
                "summary": "以历史回测净值为预算、零现仓生成研究草案，不能直接进入模拟执行。",
                "source_run_id": report.run_id,
                "as_of_date": report.as_of_date.isoformat(),
                "budget": str(report.total_equity),
                "current_quantity": 0,
                "source_sha256": digest,
                "holdings": report.model_dump(mode="json")["holdings"],
                "warnings": ["使用历史收盘价与当前行业标签；不代表当前真实持仓或今日可交易组合。"],
            }

    def start(self, name, args, preview, action_id):
        from app.services.agent_permissions import validate_grant

        grant = preview.get("_agent_grant")
        validate_grant(grant, name, args, action_id)
        args = TOOLS[name].arguments.model_validate(args).model_dump(mode="json")
        identity = self.action_identity(name, args, action_id)
        with database_job_lock, SessionLocal() as db:
            previous = AgentRecoveryRepository().jobs(db, action_id)
            if len(previous) > 1:
                raise ValueError("原确认存在重复任务，不能重新创建。")
            if previous:
                metadata = previous[0].metadata_ or {}
                normalized = (
                    self.operations._normalize_parameters(MAINTENANCE[name], args)
                    if name in MAINTENANCE
                    else args
                )
                if (
                    not AgentEvidenceRecovery.identity_matches(
                        metadata.get(AGENT_EVIDENCE_KEY), identity
                    )
                    or metadata.get("parameters") != normalized
                ):
                    raise ValueError("原任务身份冲突，不能重新创建。")
                return previous[0].id
            if name in MAINTENANCE:
                action = self.operations.start(
                    db,
                    request=OperationsActionStartRequest(
                        action_type=MAINTENANCE[name],
                        parameters=args,
                        idempotency_key=f"agent:{action_id}",
                    ),
                    agent_identity=identity.model_dump(mode="json"),
                )
                # A failure here leaves a known starting intent and no automatic replay.
                with duckdb_write_lock("data/duckdb_write.lock", timeout_seconds=3):
                    job = self.jobs.get(db, action.action_id)
                    job.metadata_ = {
                        **(job.metadata_ or {}),
                        "agent_authorization": grant,
                        "parameters_hash": parameters_hash(name, args),
                        "code_version": settings.app_code_version,
                    }
                    db.commit()
                return action.action_id
            if name == "run_factor_backtest":
                with duckdb_write_lock("data/research_write.lock", timeout_seconds=3):
                    with ResearchSessionLocal() as research:
                        if AgentRecoveryRepository().backtests(research, action_id):
                            raise ValueError("原确认已有研究产物，需核验原任务，不能重新创建。")
            elif name == "create_portfolio_from_backtest":
                if AgentRecoveryRepository().portfolios(db, action_id):
                    raise ValueError("原确认已有组合草案，需核验原任务，不能重新创建。")
            else:
                raise ValueError("工具不在允许的写动作清单中。")
            job = self.jobs.create(
                db,
                job_name="agent_action",
                job_type="schedule",
                metadata=merge_job_lease_metadata(
                    db,
                    {
                        "tool": name,
                        "parameters": args,
                        "preview": preview,
                        "confirmation_id": action_id,
                        "phase": "queued",
                        "code_version": settings.app_code_version,
                        "parameters_hash": identity.parameters_hash,
                        AGENT_EVIDENCE_KEY: identity.model_dump(mode="json"),
                        "agent_authorization": grant,
                    },
                    repository=self.jobs,
                ),
            )
            db.commit()
            return job.id

    @staticmethod
    def action_identity(name, args, action_id, code_version=None):
        return AgentActionIdentity(
            confirmation_id=action_id,
            tool=name,
            parameters_hash=parameters_hash(name, args),
            code_version=code_version or settings.app_code_version,
        )

    def recover_action(self, name, args, action_id, *, code_version, task_id=None):
        args = TOOLS[name].arguments.model_validate(args).model_dump(mode="json")
        identity = self.action_identity(name, args, action_id, code_version)
        normalized = (
            self.operations._normalize_parameters(MAINTENANCE[name], args)
            if name in MAINTENANCE
            else args
        )
        with duckdb_write_lock("data/research_write.lock", timeout_seconds=3):
            with SessionLocal() as db, ResearchSessionLocal() as research:
                return AgentEvidenceRecovery().inspect(
                    db,
                    research,
                    identity=identity,
                    parameters=args,
                    normalized_parameters=normalized,
                    task_id=task_id,
                )

    def task(self, task_id):
        with SessionLocal() as db:
            job = self.jobs.get(db, task_id)
            if job is None or job.job_name not in {"agent_action", "operator_action"}:
                raise ValueError("智能体任务不存在")
            if job.job_name == "operator_action":
                result = self.operations.to_read(job).model_dump(mode="json")
                if result.get("error_message"):
                    result["error_message"] = "任务未完成，请到运维页面核对来源与前置条件。"
                return result
            return {
                "task_id": job.id,
                "status": job.status,
                "phase": (job.metadata_ or {}).get("phase"),
                "result": (job.metadata_ or {}).get("result"),
                "error_message": job.error_message,
            }

    def task_for_action(self, task_id, confirmation_id, tool, parameters):
        from app.services.agent_execution_contract import parameters_hash

        with SessionLocal() as db:
            job = self.jobs.get(db, task_id)
            if job is None:
                raise ValueError("任务不存在。")
            metadata = job.metadata_ or {}
            if job.job_name == "operator_action" and tool in MAINTENANCE:
                matches = (
                    metadata.get("idempotency_key") == f"agent:{confirmation_id}"
                    and metadata.get("action_type") == MAINTENANCE[tool]
                    and metadata.get("parameters")
                    == self.operations._normalize_parameters(MAINTENANCE[tool], parameters)
                )
            else:
                matches = (
                    job.job_name == "agent_action"
                    and metadata.get("tool") == tool
                    and metadata.get("confirmation_id") == confirmation_id
                    and parameters_hash(tool, metadata.get("parameters", {}))
                    == parameters_hash(tool, parameters)
                )
            if not matches or metadata.get("parameters_hash") != parameters_hash(tool, parameters):
                raise ValueError("任务确认、工具或参数身份不匹配。")
            if metadata.get("completion_status") == "unknown":
                return {"task_id": task_id, "status": "unknown", "result": None}
            identity = AgentActionIdentity.model_validate(metadata.get(AGENT_EVIDENCE_KEY))
        report = self.recover_action(
            tool, parameters, confirmation_id, code_version=identity.code_version, task_id=task_id
        )
        return {
            "task_id": task_id,
            "status": "success" if report.status == "verified" else report.status,
            "result": report.outputs if report.status == "verified" else None,
            "recovery": report.model_dump(mode="json"),
        }

    @staticmethod
    def _validate_execution_grant(job):
        from app.services.agent_execution_contract import parameters_hash
        from app.services.agent_permissions import validate_grant

        metadata = job.metadata_ or {}
        grant = metadata.get("agent_authorization") or {}
        if job.job_name == "operator_action":
            name = next(
                (
                    name
                    for name, action in MAINTENANCE.items()
                    if action == metadata.get("action_type")
                ),
                None,
            )
            # Approved original tool parameters are stored in the server session.
            from app.services.agent_state import AgentStateStore

            saved = AgentStateStore().load(grant.get("session_id")) or {}
            action = next(
                (
                    v["view"]
                    for v in saved.get("actions", {}).values()
                    if v["view"].get("task_id") == job.id
                ),
                {},
            )
            args, confirmation_id = action.get("parameters", {}), action.get("action_id")
            if (
                metadata.get("idempotency_key") != f"agent:{confirmation_id}"
                or name is None
                or metadata.get("parameters")
                != OperationsActionService()._normalize_parameters(MAINTENANCE[name], args)
            ):
                raise ValueError("维护任务参数超出授权范围。")
        else:
            name, args = metadata.get("tool"), metadata.get("parameters", {})
            confirmation_id = metadata.get("confirmation_id")
        if metadata.get("parameters_hash") != parameters_hash(name, args):
            raise ValueError("任务参数哈希不匹配。")
        validate_grant(grant, name, args, confirmation_id, task_id=job.id)

    def execute(self, task_id):
        with SessionLocal() as db:
            job = self.jobs.get(db, task_id)
            if job is None:
                return
            operator = job.job_name == "operator_action"
        try:
            self._validate_execution_grant(job)
        except Exception:
            with database_job_lock, SessionLocal() as db:
                current = self.jobs.get(db, task_id)
                if current is not None and current.status == "running":
                    self.jobs.finish(
                        db,
                        job_log=current,
                        status="failed",
                        error_message="计划授权、代码或期限已失效，未启动业务执行。",
                        metadata={"phase": "authorization_blocked"},
                    )
                    db.commit()
            return
        if operator:
            self.operations.execute(task_id)
            return
        try:
            with database_job_lock, self.context() as (db, research, _):
                job = self.jobs.get(db, task_id)
                if job is None or job.job_name != "agent_action" or job.status != "running":
                    return
                metadata = job.metadata_
                name, args = metadata["tool"], metadata["parameters"]
                identity = self.action_identity(name, args, metadata["confirmation_id"])
                if not AgentEvidenceRecovery.identity_matches(
                    metadata.get(AGENT_EVIDENCE_KEY), identity
                ):
                    raise ValueError("执行身份缺失或代码/参数变化，请核验原任务。")
                if name == "run_factor_backtest":
                    if AgentRecoveryRepository().backtests(research, identity.confirmation_id):
                        raise ValueError("原确认已有研究产物，不能重复执行。")
                    payload = self.backtest_payload(args)
                    service = StrategyResearchService(
                        backtest_service=DailyBacktestService(
                            backtest_repository=AgentBacktestRepository(identity)
                        )
                    )
                    checks = check_factor_selection(
                        db,
                        payload=payload,
                        service=service,
                        portfolio_constraints=default_constraints(),
                    )
                    if not checks["runnable_now"]:
                        raise ValueError("执行时研究预检未通过")
                    options = payload.model_dump(exclude={"factor_specs", "portfolio_constraints"})
                    result = service.run_factor_selection(
                        db,
                        research_db=research,
                        factor_specs=[
                            FactorScoreSpec(**row.model_dump()) for row in payload.factor_specs
                        ],
                        portfolio_constraints=default_constraints(),
                        **options,
                    )
                    result = {
                        "run_id": result.run_id,
                        "research_only": True,
                        "evidence_qualified": False,
                    }
                    research.commit()
                elif name == "create_portfolio_from_backtest":
                    report, digest = self.plan_source(db, research, args)
                    if digest != metadata["preview"]["source_sha256"]:
                        raise ValueError("回测来源已变化，请重新生成并确认提案")
                    plan = PortfolioConstructionService().build(
                        db,
                        name=args["name"],
                        as_of_date=report.as_of_date,
                        assets=[
                            PortfolioAsset(
                                symbol=row.symbol,
                                security_type=row.security_type,
                                industry=row.industry,
                                price=row.close,
                                current_quantity=0,
                                requested_weight=row.weight,
                            )
                            for row in report.holdings
                        ],
                        cash=report.total_equity,
                        constraints=default_constraints(),
                        code_version=settings.app_code_version,
                        idempotency_key=f"agent:{metadata['confirmation_id']}",
                        input_snapshot={
                            "input_source": "agent_backtest_prefill",
                            "source_run_id": report.run_id,
                            "source_sha256": digest,
                            "research_only": True,
                            "execution_eligible": False,
                            "name": args["name"],
                            "as_of_date": report.as_of_date.isoformat(),
                            "cash": str(report.total_equity),
                            "current_quantity": 0,
                            AGENT_EVIDENCE_KEY: identity.model_dump(mode="json"),
                        },
                    )
                    result = plan.model_dump(mode="json")
                else:
                    raise ValueError("unknown agent action")
                self.jobs.finish(
                    db,
                    job_log=job,
                    status="success",
                    metadata={"phase": "completed", "result": result},
                )
                db.commit()
        except Exception as exc:
            # The original exception may contain SQL, provider bodies or local paths.
            with database_job_lock, SessionLocal() as db:
                job = self.jobs.get(db, task_id)
                if job is not None and job.status == "running":
                    self.jobs.finish(
                        db,
                        job_log=job,
                        status="failed",
                        error_message=f"智能体任务失败（{type(exc).__name__}），请核对数据与研究前置条件。",
                        metadata={"phase": "failed"},
                    )
                    db.commit()
