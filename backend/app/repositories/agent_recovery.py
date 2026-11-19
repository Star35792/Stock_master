"""Exact identity queries. Two matches are enough to detect ambiguous evidence."""

from sqlalchemy import and_, or_, select

from app.models.portfolio import PortfolioPlanRecord
from app.models.strategy import BacktestRun
from app.models.system import JobLog
from app.repositories.strategy.backtest import BacktestRepository
from app.schemas.agent_recovery import AgentActionIdentity

AGENT_EVIDENCE_KEY = "_agent_action"


class AgentRecoveryRepository:
    def jobs(self, db, confirmation_id):
        return list(
            db.scalars(
                select(JobLog)
                .where(
                    or_(
                        and_(
                            JobLog.job_name == "agent_action",
                            JobLog.metadata_["confirmation_id"].as_string() == confirmation_id,
                        ),
                        and_(
                            JobLog.job_name == "operator_action",
                            JobLog.metadata_["idempotency_key"].as_string()
                            == f"agent:{confirmation_id}",
                        ),
                    )
                )
                .order_by(JobLog.id)
                .limit(2)
            )
        )

    def backtests(self, db, confirmation_id):
        return list(
            db.scalars(
                select(BacktestRun)
                .where(
                    BacktestRun.parameters[AGENT_EVIDENCE_KEY]["confirmation_id"].as_string()
                    == confirmation_id
                )
                .order_by(BacktestRun.id)
                .limit(2)
            )
        )

    def portfolios(self, db, confirmation_id):
        return list(
            db.scalars(
                select(PortfolioPlanRecord)
                .where(PortfolioPlanRecord.idempotency_key == f"agent:{confirmation_id}")
                .order_by(PortfolioPlanRecord.id)
                .limit(2)
            )
        )


class AgentBacktestRepository(BacktestRepository):
    """Bind before the first flush, including a backtest's internal failure commit.

    AgentTools holds the shared research writer lock throughout this call. A
    prior artifact, including a failed run, is evidence to inspect, never replay.
    """

    def __init__(self, identity: AgentActionIdentity):
        self.identity = identity

    def create_run(self, db, **kwargs):
        if kwargs["code_version"] != self.identity.code_version:
            raise ValueError("研究执行代码版本与原确认不一致。")
        if AgentRecoveryRepository().backtests(db, self.identity.confirmation_id):
            raise ValueError("原确认已有研究产物，请核验原运行，不能重复执行。")
        parameters = dict(kwargs["parameters"])
        if AGENT_EVIDENCE_KEY in parameters:
            raise ValueError("研究合同中的智能体身份只能由服务器注入。")
        parameters[AGENT_EVIDENCE_KEY] = self.identity.model_dump(mode="json")
        return super().create_run(db, **{**kwargs, "parameters": parameters})
