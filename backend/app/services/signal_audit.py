"""Read-only run adapter for bounded factor-frame-to-signal diagnostics."""

from collections.abc import Callable
from copy import deepcopy
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from threading import Lock
from time import monotonic
from typing import Any

from sqlalchemy.orm import Session

from app.backtest import TargetWeightSignal
from app.backtest.rebalance import select_rebalance_dates
from app.config.settings import source_code_version
from app.database.research_session import ResearchSessionLocal
from app.repositories.market.security import SecurityRepository
from app.repositories.market.trading_calendar import TradingCalendarRepository
from app.repositories.strategy.backtest import BacktestRepository
from app.schemas.signal_audit import SignalAuditRead
from app.services.strategy_research import StrategyResearchService
from app.strategies.inputs import StrategyFactorFrame, StrategyFactorObservation
from app.strategies.signal_audit import (
    MAX_DATES,
    MAX_EXAMPLES,
    MAX_FEATURES,
    MAX_ROWS,
    MAX_SECONDS,
    MAX_SIGNALS,
    AuditObservation,
    audit_signals,
    canonical_signals,
)
from app.strategies.templates import (
    FactorScoreSpec,
    MinimumScorePolicy,
    MultiFactorStrategy,
    WeightingMode,
)
from app.utils.duckdb_write_lock import duckdb_write_lock

MAX_PARAMETER_CHARS = 1_000_000
MAX_CALENDAR_DAYS = 366
_AUDIT_LOCK = Lock()


class SignalAuditNotFound(ValueError):
    pass


class SignalAuditBusy(RuntimeError):
    pass


@dataclass(frozen=True)
class AuditRun:
    status: str
    code_version: str
    start_date: date
    end_date: date
    parameters: dict[str, Any]
    oversized: bool = False


def load_audit_run(run_id: int) -> AuditRun:
    """Copy a bounded contract and release the research write lock before computing."""
    repository = BacktestRepository()
    with duckdb_write_lock(
        "data/research_write.lock", timeout_seconds=3, poll_interval_seconds=0.25
    ), ResearchSessionLocal() as db:
        header = repository.signal_audit_header(db, run_id=run_id)
        if header is None:
            raise SignalAuditNotFound("回测运行不存在。")
        if (header["parameter_chars"] or 0) > MAX_PARAMETER_CHARS:
            return AuditRun("", "", date.min, date.min, {}, oversized=True)
        run = repository.get_run(db, run_id=run_id)
        assert run is not None
        return AuditRun(
            run.status, run.code_version, run.start_date, run.end_date,
            deepcopy(run.parameters),
        )


class SignalAuditService:
    def __init__(
        self, *, run_loader: Callable[[int], AuditRun] = load_audit_run,
        code_version: Callable[[], str] = source_code_version,
        research: StrategyResearchService | None = None,
    ) -> None:
        self.run_loader = run_loader
        self.code_version = code_version
        self.research = research or StrategyResearchService()
        self.securities = SecurityRepository()
        self.calendar = TradingCalendarRepository()

    def build_report(self, db: Session, *, run_id: int) -> SignalAuditRead:
        if not _AUDIT_LOCK.acquire(blocking=False):
            raise SignalAuditBusy("已有信号审计正在计算，请稍后重试。")
        try:
            return self._build(db, run_id)
        finally:
            _AUDIT_LOCK.release()

    def _build(self, db: Session, run_id: int) -> SignalAuditRead:
        started = monotonic()
        run = self.run_loader(run_id)
        report = SignalAuditRead(
            run_id=run_id, generated_at=datetime.now(UTC),
            reason="运行证据不足，无法核验。",
            recorded_code_version=run.code_version or None,
            current_code_version=self.code_version(),
            declarations=[
                "仅抽查已存因子输入到目标权重信号的前缀稳定性，不构成完整历史回放。",
                "不覆盖行情到因子的计算、公告首次可得时间、修订、幸存者偏差或预处理。",
                "不认证缺失值业务处理政策；缺失值未被自动补零。",
                "不验证成交、费用、收益或公司行为会计；不改变运行资格和原始结果。",
                "证券类型和调仓日历未绑定原运行摘要；本次冻结后核对完整原始信号。",
                "停止等待只关闭浏览器请求；服务端按有界规模与协作式时间预算结束。",
            ],
            limits={"rows": MAX_ROWS, "signals": MAX_SIGNALS, "dates": MAX_DATES,
                    "features": MAX_FEATURES, "cutoffs": 3, "evaluations": 9,
                    "examples": MAX_EXAMPLES, "seconds": MAX_SECONDS,
                    "parameter_chars": MAX_PARAMETER_CHARS,
                    "calendar_days": MAX_CALENDAR_DAYS},
        )
        try:
            if run.oversized:
                raise ValueError("运行合同超过大小上限，未载入完整 JSON。")
            if run.status != "success":
                raise ValueError("仅支持已成功完成的因子选股运行。")
            if run.code_version != report.current_code_version:
                raise ValueError("原运行代码版本与当前源码不一致或无法确认，请用当前版本重新研究。")
            parameters = run.parameters
            required = {
                "factor_specs", "top_n", "allowed_security_types", "coverage_ratio",
                "weighting_mode", "minimum_score_policy", "minimum_score", "invested_weight",
                "rebalance_rule", "rebalance_interval", "strategy_input_snapshot", "signals",
                "start_date", "end_date",
            }
            if not required <= parameters.keys():
                raise ValueError("缺少完整因子选股合同或原始信号，不支持此运行。")
            if parameters.get("selection_policy") or parameters.get("selection_result"):
                raise ValueError("本版暂不核验训练窗自动选参运行，需先绑定有效因子合同。")
            if parameters.get("portfolio_constraints"):
                raise ValueError("本版暂不核验附带组合约束的运行。")
            if (run.end_date - run.start_date).days not in range(MAX_CALENDAR_DAYS):
                raise ValueError("审计窗口须在 366 个自然日以内，不截断原运行冒充完整审计。")
            if (parameters["start_date"], parameters["end_date"]) != (
                run.start_date.isoformat(), run.end_date.isoformat()
            ):
                raise ValueError("运行日期与研究合同不一致。")
            specs = self._specs(parameters["factor_specs"])
            snapshot = parameters["strategy_input_snapshot"]
            if not isinstance(snapshot, dict) or snapshot.get("contract_version") != (
                "strategy_factor_frame_v1"
            ):
                raise ValueError("缺少支持版本的因子输入摘要。")
            if not 0 < snapshot.get("row_count", 0) <= MAX_ROWS:
                raise ValueError("原运行因子行数为空或超过 20000 行审计上限。")
            raw_signals = parameters["signals"]
            if not isinstance(raw_signals, list) or len(raw_signals) > MAX_SIGNALS:
                raise ValueError("原始信号合同无效或超过审计上限。")
            expected = [TargetWeightSignal(
                date.fromisoformat(item["signal_date"]), item["symbol"],
                self._decimal(item["target_weight"]),
            ) for item in raw_signals]
            canonical_signals(expected)
            strategy = MultiFactorStrategy(
                top_n=int(parameters["top_n"]),
                allowed_security_types=set(parameters["allowed_security_types"]),
                coverage_ratio=self._optional_decimal(parameters["coverage_ratio"]),
                weighting_mode=WeightingMode(parameters["weighting_mode"]),
                minimum_score_policy=MinimumScorePolicy(parameters["minimum_score_policy"]),
                minimum_score=self._optional_decimal(parameters["minimum_score"]),
                invested_weight=self._decimal(parameters["invested_weight"]),
            )
            frame, types, open_dates = self._inputs(db, run, specs)
            report.input_rows = frame.snapshot()["row_count"]
            report.input_sha256 = frame.snapshot()["sha256"]
            if frame.snapshot() != snapshot:
                raise ValueError("当前因子数据与原运行摘要不一致，不能核验该运行。")
            report.input_verified = True
            rebalance = parameters["rebalance_rule"]
            selected_dates = None if rebalance == "daily" else select_rebalance_dates(
                open_dates, rule=rebalance, interval=int(parameters["rebalance_interval"])
            )
            by_feature = {str(index): spec for index, spec in enumerate(specs)}
            observations = tuple(
                AuditObservation(row.trade_date, row.symbol, feature, row.value)
                for feature, spec in by_feature.items() for row in frame.rows_by_factor[spec]
            )

            def evaluate(rows: tuple[AuditObservation, ...]) -> list[TargetWeightSignal]:
                rebuilt = StrategyFactorFrame.from_rows(
                    as_of_date=max(row.trade_date for row in rows),
                    rows_by_factor={spec: [
                        StrategyFactorObservation(row.symbol, row.trade_date, row.value)
                        for row in rows if row.feature == feature
                    ] for feature, spec in by_feature.items()},
                )
                decisions = None if selected_dates is None else sorted(
                    selected_dates & set(rebuilt.observation_dates)
                )
                return strategy.generate_history(
                    frame=rebuilt, security_types=types, decision_dates=decisions
                )

            # End this read transaction before re-reading to detect concurrent input changes.
            if db is not None:
                db.rollback()
            result = audit_signals(
                observations, evaluate, expected_signals=expected,
                time_budget=MAX_SECONDS - (monotonic() - started),
            )
            report = SignalAuditRead.model_validate({**report.model_dump(), **asdict(result)})
            report.baseline_matches_run = bool(result.checks)
            again, types_after, dates_after = self._inputs(db, run, specs)
            if (again.snapshot() != snapshot or types_after != types or dates_after != open_dates
                    or self.code_version() != report.current_code_version):
                report.status = "insufficient"
                report.reason = "审计期间输入或源码发生变化，结果不可核验，请重试。"
                report.input_verified = False
            if monotonic() - started > MAX_SECONDS:
                report.status = "insufficient"
                report.reason = "达到协作式时间预算，检查未完成。"
            # Revalidate nested check dictionaries from dataclasses as response models.
            return SignalAuditRead.model_validate(report.model_dump())
        except (ValueError, TypeError, KeyError, ArithmeticError) as exc:
            report.status = "insufficient"
            report.reason = f"不可核验：{exc}"
            return report

    def _inputs(self, db: Session, run: AuditRun, specs: list[FactorScoreSpec]):
        frame = self.research.load_signal_audit_frame(
            db, factor_specs=specs, start_date=run.start_date, end_date=run.end_date,
            max_rows=MAX_ROWS,
        )
        symbols = {row.symbol for rows in frame.rows_by_factor.values() for row in rows}
        types = {symbol.upper(): kind for symbol, kind in
                 self.securities.get_security_types(db, symbols).items()}
        dates = [] if run.parameters["rebalance_rule"] == "daily" else (
            self.calendar.list_open_dates(
                db, exchange="SH", start_date=run.start_date, end_date=run.end_date
            )
        )
        return frame, types, dates

    @staticmethod
    def _decimal(value: Any) -> Decimal:
        if not isinstance(value, (str, int, Decimal)) or isinstance(value, bool):
            raise ValueError("金融参数必须为精确十进制字符串。")
        result = Decimal(value)
        if not result.is_finite():
            raise ValueError("金融参数必须为有限 Decimal。")
        return result

    @classmethod
    def _optional_decimal(cls, value: Any) -> Decimal | None:
        return None if value is None else cls._decimal(value)

    @classmethod
    def _specs(cls, items: Any) -> list[FactorScoreSpec]:
        if not isinstance(items, list) or not 1 <= len(items) <= MAX_FEATURES:
            raise ValueError("因子数量须在 1 到 8 之间。")
        specs = []
        for item in items:
            if (not item["factor_name"] or not item["factor_version"]
                    or type(item["higher_is_better"]) is not bool):
                raise ValueError("因子合同无效。")
            spec = FactorScoreSpec(
                item["factor_name"], item["factor_version"], cls._decimal(item["weight"]),
                item["higher_is_better"],
            )
            if spec.weight <= 0:
                raise ValueError("因子权重须为正。")
            specs.append(spec)
        if len({(item.factor_name, item.factor_version) for item in specs}) != len(specs):
            raise ValueError("因子合同重复。")
        return specs
