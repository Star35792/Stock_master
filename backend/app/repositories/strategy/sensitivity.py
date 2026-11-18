"""Bounded projections only: never load equity, trades, positions or exposure JSON."""

from sqlalchemy import String, cast, func, select
from sqlalchemy.orm import Session

from app.models.strategy import BacktestPerformance, BacktestRun

MAX_RUN_JSON = 1_000_000
MAX_BATCH_JSON = 8_000_000


class SensitivityRepository:
    def load(self, db: Session, run_ids: list[int]) -> tuple[dict[int, dict], dict[int, str]]:
        headers = list(
            db.execute(
                select(
                    BacktestRun.id,
                    func.length(cast(BacktestRun.parameters, String)).label("chars"),
                ).where(BacktestRun.id.in_(run_ids))
            ).mappings()
        )
        errors = {key: "运行不存在。" for key in run_ids if key not in {r["id"] for r in headers}}
        accepted = []
        total = 0
        for header in sorted(headers, key=lambda item: item["id"]):
            size = header["chars"] or 0
            if size > MAX_RUN_JSON or total + size > MAX_BATCH_JSON:
                errors[header["id"]] = "运行合同超过单项或整批 JSON 大小上限，未载入全文。"
            else:
                accepted.append(header["id"])
                total += size
        if not accepted:
            return {}, errors
        rows = db.execute(
            select(
                BacktestRun.id,
                BacktestRun.status,
                BacktestRun.strategy_name,
                BacktestRun.code_version,
                BacktestRun.start_date,
                BacktestRun.end_date,
                BacktestRun.initial_cash,
                BacktestRun.total_return.label("run_total_return"),
                BacktestRun.parameters,
                BacktestPerformance.total_return,
                BacktestPerformance.maximum_drawdown,
                BacktestPerformance.sharpe_ratio,
                BacktestPerformance.code_version.label("performance_code_version"),
            )
            .outerjoin(BacktestPerformance, BacktestPerformance.run_id == BacktestRun.id)
            .where(BacktestRun.id.in_(accepted))
        ).mappings()
        return {row["id"]: dict(row) for row in rows}, errors
