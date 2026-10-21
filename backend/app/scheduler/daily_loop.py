"""日级数据→因子闭环调度器。

把 ``daily_sync`` 与 ``factor_pipeline`` 串成一次编排运行：
先同步日线并构建 ``market`` 可信层，再滚动计算最近窗口的全部因子。
整次运行写入 ``system.job_log``；sync 失败时不继续执行因子计算。
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import date, timedelta
from pathlib import Path

from apscheduler.schedulers.blocking import BlockingScheduler
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config.settings import settings
from app.database.session import SessionLocal, engine
from app.lake import CuratedLayer, DataLake
from app.lake.curated import CuratedDatasetResult
from app.repositories.lake import ReleaseRepository
from app.repositories.market import SecurityRepository, TradingCalendarRepository
from app.repositories.system import JobLogRepository
from app.repositories.warm_layer import WarmLayerStore
from app.scheduler.daily_sync import run_hithink_daily_sync
from app.scheduler.factor_pipeline import FactorBatchResult, run_all_factors
from app.services import HithinkDailySyncResult
from app.services.adjustment_build import AdjustmentBuildService
from app.services.release import ReleaseService

logger = logging.getLogger(__name__)


@dataclass
class DailyLoopResult:
    sync_result: HithinkDailySyncResult | None = None
    curated_result: CuratedDatasetResult | None = None
    factor_result: FactorBatchResult | None = None
    sync_error: str | None = None
    curated_error: str | None = None
    factor_error: str | None = None
    skipped: bool = False
    skip_reason: str | None = None
    failed: bool = False
    market_release_id: str | None = None
    curated_release_id: str | None = None

    @property
    def total_rows(self) -> int:
        sync_rows = self.sync_result.market_build.trusted_count if self.sync_result else 0
        factor_rows = self.factor_result.total_rows if self.factor_result else 0
        return sync_rows + factor_rows

    @property
    def failed_services(self) -> list[str]:
        if self.factor_result is None:
            return []
        return self.factor_result.failed


def _resolve_symbols(
    db: Session,
    symbols: list[str] | None,
    security_repository: SecurityRepository | None = None,
) -> list[str]:
    if symbols is not None:
        normalized = [symbol.strip().upper() for symbol in symbols if symbol.strip()]
        if not normalized:
            raise ValueError("symbols must not be empty when provided")
        return normalized
    repo = security_repository or SecurityRepository()
    return repo.get_active_symbols(db)


def run_daily_loop(
    *,
    symbols: list[str] | None = None,
    sync_days: int = 14,
    factor_lookback_days: int = 14,
    sync_adjust_type: str = "none",
    factor_adjust_type: str = "qfq",
    refresh_reference_data: bool = False,
    code_version: str = "local",
    session_factory: Callable[[], Session] = SessionLocal,
    security_repository: SecurityRepository | None = None,
    job_log_repository: JobLogRepository | None = None,
    trading_calendar_repository: TradingCalendarRepository | None = None,
) -> DailyLoopResult:
    """运行一次完整的数据同步→因子计算日级闭环。

    参数 ``symbols`` 为 ``None`` 时从 ``market.security`` 拉取全部 active 证券。
    同步失败时直接结束并标记任务失败；因子管道部分失败时标记 partial。
    """
    repo = job_log_repository or JobLogRepository()
    result = DailyLoopResult()

    with session_factory() as db:
        # ---- Trading calendar gate ----
        # If every exchange says "closed" we skip silently. If the calendar is
        # missing we fail closed so the operator investigates.
        today = date.today()
        cal_repo = trading_calendar_repository or TradingCalendarRepository()
        cal_entries = cal_repo.get_many_on_date(
            db, exchanges={"SH", "SZ", "BJ"}, trade_date=today,
        )
        if not cal_entries:
            error_message = (
                f"trading calendar missing for {today} on all exchanges (SH,SZ,BJ); "
                "cannot determine if market is open"
            )
            result.skip_reason = "CALENDAR_MISSING"
            result.failed = True
            job = repo.create(
                db,
                job_name="daily_loop",
                job_type="schedule",
                metadata={
                    "gate_status": "blocked",
                    "publish_eligible": False,
                    "reason_codes": ["CALENDAR_MISSING"],
                    "target_trade_date": today.isoformat(),
                },
            )
            repo.finish(db, job_log=job, status="failed", error_message=error_message)
            db.commit()
            logger.error(error_message)
            return result

        any_open = any(entry.is_open for entry in cal_entries)
        if not any_open:
            result.skipped = True
            result.skip_reason = "MARKET_CLOSED"
            job = repo.create(
                db,
                job_name="daily_loop",
                job_type="schedule",
                metadata={
                    "gate_status": "skipped",
                    "publish_eligible": False,
                    "reason_codes": ["MARKET_CLOSED"],
                    "target_trade_date": today.isoformat(),
                },
            )
            repo.finish(
                db,
                job_log=job,
                status="success",
                metadata={
                    "gate_status": "skipped",
                    "publish_eligible": False,
                    "reason_codes": ["MARKET_CLOSED"],
                    "target_trade_date": today.isoformat(),
                },
            )
            db.commit()
            logger.info("Market closed on %s — skipping daily loop", today)
            return result

        resolved_symbols = _resolve_symbols(db, symbols, security_repository)
        job = repo.create(
            db,
            job_name="daily_loop",
            job_type="schedule",
            metadata={
                "symbols_count": len(resolved_symbols),
                "sync_days": sync_days,
                "factor_lookback_days": factor_lookback_days,
                "sync_adjust_type": sync_adjust_type,
                "factor_adjust_type": factor_adjust_type,
                "refresh_reference_data": refresh_reference_data,
                "code_version": code_version,
            },
        )
        db.commit()

        try:
            sync_result = run_hithink_daily_sync(
                symbols=resolved_symbols,
                days=sync_days,
                adjust_type=sync_adjust_type,
                refresh_reference_data=refresh_reference_data,
                session_factory=session_factory,
                job_log_repository=repo,
            )
            result.sync_result = sync_result
            job.metadata_ = {
                **(job.metadata_ or {}),
                "sync_batch_no": sync_result.collection.batch_no,
                "sync_status": sync_result.collection.status,
            }
        except Exception as exc:  # noqa: BLE001 - log and surface
            error_message = f"sync failed: {type(exc).__name__}: {exc}"
            result.sync_error = error_message
            result.failed = True
            repo.finish(
                db,
                job_log=job,
                status="failed",
                error_message=error_message,
                metadata=job.metadata_,
            )
            db.commit()
            logger.exception("Daily loop failed at sync stage")
            return result

        if settings.factor_reads_curated and factor_adjust_type == "qfq":
            try:
                if sync_adjust_type != "none":
                    raise ValueError(
                        "factor_adjust_type='qfq' with factor_reads_curated requires "
                        f"sync_adjust_type='none', got '{sync_adjust_type}'"
                    )
                adjustment = AdjustmentBuildService().rebuild_qfq(
                    db,
                    source_name="hithink_finance_dump",
                    version=code_version,
                )
                db.commit()
            except Exception as exc:  # noqa: BLE001 - log and surface
                error_message = f"curated build failed: {type(exc).__name__}: {exc}"
                result.curated_error = error_message
                result.failed = True
                repo.finish(
                    db,
                    job_log=job,
                    status="failed",
                    error_message=error_message,
                    metadata=job.metadata_,
                )
                db.commit()
                logger.exception("Daily loop failed at curated build stage")
                return result

    # Build the curated Parquet layer outside the ORM session. DuckDB does not
    # allow a second connection to the same database file while SQLAlchemy holds
    # it open, so we dispose the connection pool, materialize, then resume with
    # a fresh session for the factor pipeline and final job log update.
    engine.dispose(close=True)

    if settings.factor_reads_curated and factor_adjust_type == "qfq":
        try:
            db_path = str(Path(settings.metadata_db_path).resolve())
            # Use the warm-layer connection so that prices_qfq is rebuilt from
            # the full parquet history plus the hot rows still in the control
            # plane.  This keeps the control-plane DB small without losing the
            # historical window required by research backtests.
            warm_con = WarmLayerStore().connect(control_db_path=db_path, read_only=True)
            try:
                curated_result = CuratedLayer(DataLake()).build_prices_qfq_from_db(
                    db_path,
                    factor_source_name="hithink_finance_dump",
                    factor_version=code_version,
                    connection=warm_con,
                )
            finally:
                warm_con.close()
            curated_manifest = ReleaseService.build_parquet_manifest(
                Path(curated_result.path),
            )
            curated_checksum = ReleaseService.manifest_checksum(curated_manifest)
            curated_result = replace(curated_result, checksum=curated_checksum)
            result.curated_result = curated_result
            curated_metadata = {
                "curated_rebuilt": True,
                "adjustment_factor_rows": adjustment.factor_rows,
                "adjustment_qfq_bar_rows": adjustment.qfq_bar_rows,
                "curated_path": curated_result.path,
                "curated_row_count": curated_result.row_count,
                "curated_min_date": curated_result.min_date,
                "curated_max_date": curated_result.max_date,
                "curated_checksum": curated_checksum,
                "curated_manifest": curated_manifest,
            }
        except Exception as exc:  # noqa: BLE001 - log and surface
            error_message = f"curated build failed: {type(exc).__name__}: {exc}"
            result.curated_error = error_message
            result.failed = True
            with session_factory() as db:
                job = repo.get(db, job.id)
                if job is not None:
                    repo.finish(
                        db,
                        job_log=job,
                        status="failed",
                        error_message=error_message,
                        metadata=job.metadata_,
                    )
                    db.commit()
            logger.exception("Daily loop failed at curated build stage")
            return result

    with session_factory() as db:
        job_id = job.id
        job = repo.get(db, job_id)
        if job is None:
            logger.error("Daily loop job %s disappeared after sync", job_id)
            result.failed = True
            result.factor_error = "job log disappeared after sync"
            return result

        factor_release_id: str | None = None
        if result.curated_result is not None:
            job.metadata_ = {
                **(job.metadata_ or {}),
                **curated_metadata,
            }
            release_service = ReleaseService()
            release_repository = ReleaseRepository()
            market_checksum = _compute_market_qfq_checksum(db)
            market_result = _market_qfq_result(db)
            market_release = release_service.build_release(
                db,
                dataset="market.daily_bar_qfq",
                curate_result=market_result,
                checksum=market_checksum,
                parent_release_id=None,
                code_version=code_version,
                quality_status="pending",
            )
            curated_release = release_service.build_release(
                db,
                dataset="curated.prices_qfq",
                curate_result=result.curated_result,
                checksum=result.curated_result.checksum or "",
                parent_release_id=market_release.release_id,
                code_version=code_version,
                quality_status="pending",
                metadata={"files": curated_manifest},
            )
            result.market_release_id = market_release.release_id
            result.curated_release_id = curated_release.release_id
            factor_release_id = curated_release.release_id
            db.execute(
                text("""
                    UPDATE market.adjustment_factor
                    SET release_id = :release_id
                    WHERE adjust_type = 'qfq'
                      AND source_name = 'daily_loop'
                      AND version = :version
                """),
                {"release_id": market_release.release_id, "version": code_version},
            )
            adjustment = replace(adjustment, release_id=market_release.release_id)
            result.curated_result = replace(
                result.curated_result,
                release_id=curated_release.release_id,
            )
            job.metadata_ = {
                **(job.metadata_ or {}),
                "market_release_id": market_release.release_id,
                "curated_release_id": curated_release.release_id,
            }
            db.commit()

        try:
            today = date.today()
            factor_result = run_all_factors(
                start_date=today - timedelta(days=factor_lookback_days),
                end_date=today,
                symbols=resolved_symbols,
                adjust_type=factor_adjust_type,
                code_version=code_version,
                release_id=factor_release_id,
                session_factory=session_factory,
                job_log_repository=repo,
            )
            result.factor_result = factor_result
            job.rows_inserted = factor_result.total_rows
            if factor_result.failed:
                result.failed = True
                job.status = "failed"
                job.error_message = (
                    f"factor pipeline failed services: {factor_result.failed}"
                )
            else:
                job.status = "success"
            job.metadata_ = {
                **(job.metadata_ or {}),
                "failed_services": factor_result.failed,
                "services": {
                    name: svc.get("total_rows", 0) for name, svc in factor_result.services.items()
                },
            }

            # ---- Certification metadata & publish gate ----
            try:
                cert_meta = _gather_certification_metadata(
                    db,
                    today,
                    code_version=code_version,
                )
                job.metadata_ = {
                    **(job.metadata_ or {}),
                    **cert_meta,
                }
                has_gate_failure = bool(factor_result.failed) or any(
                    cert_meta.get(key) for key in [
                        "gate_market_date_missing",
                        "gate_duplicate_keys",
                        "gate_qfq_coverage_anomaly",
                        "gate_core_factor_partial",
                        "gate_query_error",
                    ]
                )
                if has_gate_failure:
                    job.metadata_["gate_status"] = "blocked"
                    job.metadata_["publish_eligible"] = False
                    result.failed = True
                    job.status = "failed"
                    job.error_message = (
                        job.error_message or ""
                    ) + "; publish gate blocked: " + "; ".join(
                        job.metadata_.get("reason_codes", [])
                    )
                else:
                    job.metadata_["gate_status"] = "passed"
                    job.metadata_["publish_eligible"] = True
                    if (
                        result.market_release_id is not None
                        and result.curated_release_id is not None
                    ):
                        release_repository = ReleaseRepository()
                        release_repository.activate(
                            db, release_id=result.market_release_id
                        )
                        release_repository.activate(
                            db, release_id=result.curated_release_id
                        )
                        db.commit()
                        logger.info(
                            "Activated releases market=%s curated=%s",
                            result.market_release_id,
                            result.curated_release_id,
                        )
            except Exception as exc:  # noqa: BLE001 - gate failure must fail closed
                logger.exception("Certification or release activation failed: %s", exc)
                if job.metadata_ is None:
                    job.metadata_ = {}
                job.metadata_["gate_status"] = "blocked"
                job.metadata_["publish_eligible"] = False
                reason_codes = job.metadata_.setdefault("reason_codes", [])
                if "RELEASE_ACTIVATION_FAILED" not in reason_codes:
                    reason_codes.append("RELEASE_ACTIVATION_FAILED")
                result.failed = True
                job.status = "failed"
                job.error_message = (
                    (job.error_message + "; ") if job.error_message else ""
                ) + f"release activation failed: {type(exc).__name__}: {exc}"
        except Exception as exc:  # noqa: BLE001 - log and surface
            error_message = f"factor pipeline failed: {type(exc).__name__}: {exc}"
            result.factor_error = error_message
            result.failed = True
            job.status = "failed"
            job.error_message = error_message

        repo.finish(
            db,
            job_log=job,
            status=job.status,
            error_message=job.error_message,
            rows_inserted=job.rows_inserted,
            metadata=job.metadata_,
        )
        db.commit()

    logger.info(
        "Daily loop completed: failed=%s sync_trusted=%s factor_rows=%s",
        result.failed,
        result.sync_result.market_build.trusted_count if result.sync_result else 0,
        result.factor_result.total_rows if result.factor_result else 0,
    )
    return result


def create_daily_loop_scheduler(
    *,
    symbols: list[str] | None = None,
    sync_days: int = 14,
    factor_lookback_days: int = 14,
    sync_adjust_type: str = "none",
    factor_adjust_type: str = "qfq",
    refresh_reference_data: bool = False,
    code_version: str = "local",
    hour: int = 18,
    minute: int = 40,
    timezone: str = "Asia/Shanghai",
    session_factory: Callable[[], Session] = SessionLocal,
    job_log_repository: JobLogRepository | None = None,
) -> BlockingScheduler:
    """日级数据→因子闭环调度器：每个交易日收盘后自动运行。"""
    if sync_days < 1:
        raise ValueError("sync_days must be >= 1")
    if factor_lookback_days < 1:
        raise ValueError("factor_lookback_days must be >= 1")

    def _job() -> None:
        run_daily_loop(
            symbols=symbols,
            sync_days=sync_days,
            factor_lookback_days=factor_lookback_days,
            sync_adjust_type=sync_adjust_type,
            factor_adjust_type=factor_adjust_type,
            refresh_reference_data=refresh_reference_data,
            code_version=code_version,
            session_factory=session_factory,
            job_log_repository=job_log_repository,
        )

    scheduler = BlockingScheduler(timezone=timezone)
    scheduler.add_job(
        _job,
        trigger="cron",
        day_of_week="mon-fri",
        hour=hour,
        minute=minute,
        id="daily_loop",
        name="Daily data to factor closed loop",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
        misfire_grace_time=3600,
    )
    return scheduler


def _compute_market_qfq_checksum(db: Session) -> str:
    """Hash canonical forward-adjusted rows in deterministic key order.

    QFQ prices are no longer materialized in ``market.daily_bar``; they are
    derived on demand from ``adjust_type='none'`` rows multiplied by the latest
    ``daily_loop`` QFQ factor. The checksum therefore hashes the computed series
    rather than stored qfq rows so that the release fingerprint remains stable.
    """
    rows = db.execute(
        text("""
            SELECT d.symbol, d.trade_date,
                   d.open * f.factor, d.high * f.factor,
                   d.low * f.factor, d.close * f.factor,
                   d.volume, d.amount, d.turnover_rate, d.quality_status
            FROM market.daily_bar d
            JOIN LATERAL (
                SELECT af.factor
                FROM market.adjustment_factor af
                WHERE af.symbol = d.symbol
                  AND af.trade_date = d.trade_date
                  AND af.adjust_type = 'qfq'
                  AND af.source_name = 'daily_loop'
                ORDER BY af.updated_at DESC, af.id DESC
                LIMIT 1
            ) f ON true
            WHERE d.adjust_type = 'none'
            ORDER BY d.symbol, d.trade_date
        """)
    )
    hasher = hashlib.sha256()
    for row in rows:
        payload = "|".join("" if value is None else str(value) for value in row)
        hasher.update(payload.encode())
        hasher.update(b"\n")
    return hasher.hexdigest()


@dataclass(frozen=True)
class _DatasetStats:
    row_count: int
    min_date: date | None
    max_date: date | None


def _market_qfq_result(db: Session) -> _DatasetStats:
    row = db.execute(
        text("""
            SELECT COUNT(*), MIN(trade_date), MAX(trade_date)
            FROM market.daily_bar
            WHERE adjust_type = 'none'
        """)
    ).one()
    return _DatasetStats(
        row_count=int(row[0]),
        min_date=row[1],
        max_date=row[2],
    )


def _gather_certification_metadata(
    db: Session,
    target_date: date,
    *,
    code_version: str,
) -> dict:
    """Collect data certification metadata and run publish gate checks.

    Returns a flat dict of metadata values plus gate_* boolean fields.
    Callers merge this into job_log.metadata_ and set publish_eligible.
    """
    meta: dict = {
        "target_trade_date": target_date.isoformat(),
        "code_version": code_version,
        "gate_status": "pending",
        "publish_eligible": False,
        "reason_codes": [],
        "gate_market_date_missing": False,
        "gate_duplicate_keys": False,
        "gate_qfq_coverage_anomaly": False,
        "gate_core_factor_partial": False,
        "gate_query_error": False,
    }

    try:
        # market.daily_bar (none) latest date
        row = db.execute(
            text("SELECT MAX(trade_date) FROM market.daily_bar WHERE adjust_type = 'none'"),
        ).scalar()
        if row is None or row < target_date:
            meta["gate_market_date_missing"] = True
            meta["reason_codes"].append("DATA_STALE")
        meta["market_max_date"] = str(row) if row else None

        # market.daily_bar (none) row count
        row = db.execute(
            text("SELECT COUNT(*) FROM market.daily_bar WHERE adjust_type = 'none'"),
        ).scalar()
        meta["market_none_rows"] = int(row or 0)

        # duplicate keys check
        row = db.execute(
            text("""
                SELECT COUNT(*) FROM (
                    SELECT symbol, trade_date, adjust_type, COUNT(*)
                    FROM market.daily_bar
                    WHERE adjust_type = 'none'
                    GROUP BY symbol, trade_date, adjust_type
                    HAVING COUNT(*) > 1
                ) t
            """),
        ).scalar()
        if int(row or 0) > 0:
            meta["gate_duplicate_keys"] = True
            meta["reason_codes"].append("DUPLICATE_KEYS")
        meta["market_duplicate_groups"] = int(row or 0)

        # QFQ coverage
        # QFQ rows are no longer materialized in market.daily_bar; they live as
        # factors in market.adjustment_factor and as computed prices in the
        # curated prices_qfq parquet. Gate the coverage via adjustment_factor.
        qfq_source_name = "daily_loop"
        qfq_version = code_version
        row = db.execute(
            text("""
                SELECT COUNT(*)
                FROM market.adjustment_factor
                WHERE adjust_type = 'qfq'
                  AND source_name = :source_name
                  AND version = :version
            """),
            {"source_name": qfq_source_name, "version": qfq_version},
        ).scalar()
        meta["qfq_rows"] = int(row or 0)
        row = db.execute(
            text("""
                SELECT MIN(trade_date), MAX(trade_date)
                FROM market.adjustment_factor
                WHERE adjust_type = 'qfq'
                  AND source_name = :source_name
                  AND version = :version
            """),
            {"source_name": qfq_source_name, "version": qfq_version},
        ).fetchone()
        if row:
            meta["qfq_min_date"] = str(row[0]) if row[0] else None
            meta["qfq_max_date"] = str(row[1]) if row[1] else None
        else:
            meta["qfq_min_date"] = None
            meta["qfq_max_date"] = None

        row = db.execute(
            text("""
                SELECT COUNT(*)
                FROM market.daily_bar base
                LEFT JOIN market.adjustment_factor af
                  ON af.symbol = base.symbol
                 AND af.trade_date = base.trade_date
                 AND af.adjust_type = 'qfq'
                 AND af.source_name = :source_name
                 AND af.version = :version
                WHERE base.adjust_type = 'none'
                  AND af.id IS NULL
            """),
            {"source_name": qfq_source_name, "version": qfq_version},
        ).scalar()
        meta["qfq_missing_rows"] = int(row or 0)
        if meta["qfq_rows"] == 0 or meta["qfq_missing_rows"] > 0:
            meta["gate_qfq_coverage_anomaly"] = True
            meta["reason_codes"].append("QFQ_COVERAGE_GAP")

        # core factor check (at least one factor run succeeded)
        row = db.execute(
            text("""
                SELECT COUNT(*) FROM factor.factor_run
                WHERE status = 'success'
                  AND input_end_date >= :d
            """),
            {"d": target_date},
        ).scalar()
        meta["core_factor_run_count"] = int(row or 0)
        if int(row or 0) == 0:
            meta["gate_core_factor_partial"] = True
            meta["reason_codes"].append("NO_FACTOR_RUNS")

    except Exception as exc:  # noqa: BLE001 - never crash the loop over gate checks
        logging.getLogger(__name__).warning("Certification gate query error: %s", exc)
        meta["gate_query_error"] = True
        meta["reason_codes"].append(f"GATE_QUERY_ERROR: {exc}")

    return meta
