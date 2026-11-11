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
from app.repositories.system.auto_data_sync import AutoDataSyncRepository
from app.repositories.warm_layer import WarmLayerStore
from app.scheduler.alternative_data import (
    AlternativeDataCollectionResult,
    run_alternative_data_collection,
)
from app.scheduler.daily_sync import run_hithink_daily_sync
from app.scheduler.factor_pipeline import (
    DAILY_FACTOR_SET_VERSION,
    DAILY_PRICE_VOLUME_FACTORS,
    FactorBatchResult,
    run_all_factors,
)
from app.services import HithinkDailySyncResult
from app.services.adjustment_build import AdjustmentBuildService
from app.services.auto_data_sync import MIN_STOCK_COVERAGE_RATE, completed_daily_cutoff
from app.services.release import ReleaseService
from app.utils.discard import discard_directory

logger = logging.getLogger(__name__)

CORE_PRICE_FACTORS = (
    "momentum_20",
    "momentum_60",
    "volatility_20",
    "downside_volatility_20",
    "max_drawdown_20",
    "liquidity_20",
    "volume_stability_20",
)


@dataclass
class DailyLoopResult:
    sync_result: HithinkDailySyncResult | None = None
    curated_result: CuratedDatasetResult | None = None
    factor_result: FactorBatchResult | None = None
    alternative_result: AlternativeDataCollectionResult | None = None
    sync_error: str | None = None
    curated_error: str | None = None
    factor_error: str | None = None
    alternative_error: str | None = None
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
    adjustment_version: str = "v1",
    target_date: date | None = None,
    skip_sync: bool = False,
    collect_alternative_data: bool = True,
    alternative_collector: Callable[..., AlternativeDataCollectionResult] | None = None,
    session_factory: Callable[[], Session] = SessionLocal,
    security_repository: SecurityRepository | None = None,
    job_log_repository: JobLogRepository | None = None,
    trading_calendar_repository: TradingCalendarRepository | None = None,
) -> DailyLoopResult:
    """运行一次完整的数据同步→因子计算日级闭环。

    参数 ``symbols`` 为 ``None`` 时从 ``market.security`` 拉取全部 active 证券。
    同步失败时直接结束并标记任务失败；因子管道部分失败时标记 partial。

    六族 alternative 数据（情绪/宏观/政策/盈利预期/资金流/拥挤度）在因子管道
    **之前**采集——这六族因子读的就是 ``market.alternative_data_snapshot``，
    晚采一天就等于让它们白等一天。采集失败**不**让整轮失败（行情更重要），
    但会把任务降级为 partial 并留下 reason code，避免断供再次伪装成成功。
    """
    repo = job_log_repository or JobLogRepository()
    result = DailyLoopResult()

    with session_factory() as db:
        # ---- Trading calendar gate ----
        # If every exchange says "closed" we skip silently. If the calendar is
        # missing we fail closed so the operator investigates.
        today = target_date or completed_daily_cutoff()
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
                "adjustment_version": adjustment_version,
                "target_trade_date": today.isoformat(),
                "sync_skipped": skip_sync,
            },
        )
        db.commit()

        if skip_sync:
            coverage = AutoDataSyncRepository().market_coverage_on_date(
                db, trade_date=today
            )
            job.metadata_ = {**(job.metadata_ or {}), "market_coverage": coverage}
            if coverage["stock_coverage_rate"] < MIN_STOCK_COVERAGE_RATE:
                error_message = (
                    "pre-synced market data failed coverage gate: "
                    f"{coverage['covered_stocks']}/{coverage['eligible_stocks']} "
                    f"on {today}"
                )
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
                return result
        else:
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
                intraday_dates = sync_result.market_build.intraday_snapshot_dates
                job.metadata_ = {
                    **(job.metadata_ or {}),
                    "sync_batch_no": sync_result.collection.batch_no,
                    "sync_status": sync_result.collection.status,
                    # A sync started before the close stores partial bars as
                    # suspect, so nothing downstream silently reads a 11:00
                    # price as the day's close.  Recorded here because the fix
                    # is an operator action: re-sync after 15:30.
                    "intraday_snapshot_count": (
                        sync_result.market_build.intraday_snapshot_count
                    ),
                    "intraday_snapshot_dates": [
                        trade_date.isoformat() for trade_date in intraday_dates
                    ],
                }
                if intraday_dates:
                    logger.warning(
                        "daily_loop captured %s bars before their session settled "
                        "(dates=%s); they are stored as suspect. Re-sync after "
                        "15:30 Asia/Shanghai to publish final closes.",
                        sync_result.market_build.intraday_snapshot_count,
                        ", ".join(trade_date.isoformat() for trade_date in intraday_dates),
                    )
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
                    version=adjustment_version,
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
                    factor_version=adjustment_version,
                    connection=warm_con,
                    publish=False,
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
            market_checksum = _compute_market_qfq_checksum(
                db,
                source_name="hithink_finance_dump",
                version=adjustment_version,
            )
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
                      AND source_name = 'hithink_finance_dump'
                      AND version = :version
                """),
                {
                    "release_id": market_release.release_id,
                    "version": adjustment_version,
                },
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

        # ---- Alternative data collection (six families) ----
        # Runs *before* the factor pipeline on purpose: sentiment / macro /
        # policy / earnings-revision / capital-flow / crowding factors all read
        # ``market.alternative_data_snapshot``, and that snapshot is a daily
        # series — collecting afterwards would leave the six families one day
        # behind for no reason.
        #
        # Failure semantics: a vendor outage must not fail the whole loop (the
        # market sync matters more), but it must not be swallowed either —
        # otherwise the six families go stale again while the job log keeps
        # claiming "success", which is precisely the failure mode this step
        # exists to fix.  So the job is degraded to ``partial`` and the reason
        # code is surfaced (see the degradation block before ``repo.finish``).
        alternative_status: str | None = None
        alternative_meta: dict[str, object] = {}
        if collect_alternative_data:
            collector = alternative_collector or run_alternative_data_collection
            try:
                alt_result = collector(
                    session_factory=session_factory,
                    job_log_repository=repo,
                )
                result.alternative_result = alt_result
                alternative_status = alt_result.status
                alternative_meta = {
                    "alternative_status": alt_result.status,
                    "alternative_sources": alt_result.sources,
                    "alternative_empty_datasets": list(alt_result.empty_datasets),
                    "alternative_failures": alt_result.failures,
                }
                if alt_result.status == "failed":
                    result.alternative_error = (
                        "alternative data collection failed: "
                        + "; ".join(
                            f"{name}: {message}"
                            for name, message in sorted(alt_result.failures.items())
                        )
                    )
                elif alt_result.status == "partial":
                    result.alternative_error = (
                        "alternative data collection returned empty datasets: "
                        + ", ".join(alt_result.empty_datasets)
                    )
            except Exception as exc:  # noqa: BLE001 - never fail the loop here
                alternative_status = "failed"
                alternative_meta = {
                    "alternative_status": "failed",
                    "alternative_sources": {},
                    "alternative_empty_datasets": [],
                    "alternative_failures": {
                        "collector": f"{type(exc).__name__}: {exc}"
                    },
                }
                result.alternative_error = (
                    f"alternative data collection raised: {type(exc).__name__}: {exc}"
                )
                logger.exception("Alternative data collection failed")
            job.metadata_ = {**(job.metadata_ or {}), **alternative_meta}
            db.commit()

        try:
            factor_result = run_all_factors(
                start_date=today - timedelta(days=factor_lookback_days),
                end_date=today,
                symbols=resolved_symbols,
                adjust_type=factor_adjust_type,
                code_version=code_version,
                release_id=factor_release_id,
                curated_path=(
                    result.curated_result.path
                    if result.curated_result is not None
                    else None
                ),
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
            elif factor_result.empty:
                job.status = "partial"
            else:
                job.status = "success"
            job.metadata_ = {
                **(job.metadata_ or {}),
                "failed_services": factor_result.failed,
                "empty_services": factor_result.empty,
                "services": {
                    name: svc.get("total_rows", 0) for name, svc in factor_result.services.items()
                },
                "daily_factor_set_version": DAILY_FACTOR_SET_VERSION,
                "daily_price_volume_factors": list(DAILY_PRICE_VOLUME_FACTORS),
            }

            # ---- Certification metadata & publish gate ----
            try:
                cert_meta = _gather_certification_metadata(
                    db,
                    today,
                    code_version=code_version,
                    qfq_source_name="hithink_finance_dump",
                    qfq_version=adjustment_version,
                )
                job.metadata_ = {
                    **(job.metadata_ or {}),
                    **cert_meta,
                }
                # A partial session on the target date is a certification
                # failure with its own cause.  The coverage gate would catch it
                # anyway (it counts trusted bars only), but reporting
                # "MARKET_CROSS_SECTION_INCOMPLETE" would send the operator
                # looking for a vendor outage instead of an early start.
                intraday_target_hit = today.isoformat() in (
                    job.metadata_.get("intraday_snapshot_dates") or []
                )
                if intraday_target_hit:
                    job.metadata_["reason_codes"] = [
                        *job.metadata_.get("reason_codes", []),
                        "INTRADAY_SNAPSHOT",
                    ]
                has_gate_failure = (
                    bool(factor_result.failed)
                    or intraday_target_hit
                    or any(
                        cert_meta.get(key)
                        for key in [
                            "gate_market_date_missing",
                            "gate_market_coverage_incomplete",
                            "gate_duplicate_keys",
                            "gate_qfq_coverage_anomaly",
                            "gate_core_factor_partial",
                            "gate_query_error",
                        ]
                    )
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
                        published = CuratedLayer(DataLake()).publish_prices_qfq(
                            result.curated_result
                        )
                        result.curated_result = replace(
                            published,
                            release_id=result.curated_release_id,
                        )
                        job.metadata_["curated_path"] = result.curated_result.path
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

        # ---- Alternative data degradation ----
        # Applied last so the factor/gate stages cannot overwrite it, and
        # deliberately *not* a hard failure: the market release and the publish
        # gate are independent of the alternative snapshots.  A degraded job is
        # still a visible one — that is the whole point.
        if alternative_status and alternative_status != "success":
            reason_codes = list((job.metadata_ or {}).get("reason_codes") or [])
            reason_code = (
                "ALTERNATIVE_DATA_SOURCE_FAILED"
                if alternative_status == "failed"
                else "ALTERNATIVE_DATA_EMPTY_DATASETS"
            )
            if reason_code not in reason_codes:
                reason_codes.append(reason_code)
            job.metadata_ = {
                **(job.metadata_ or {}),
                "reason_codes": reason_codes,
            }
            if job.status == "success":
                job.status = "partial"

        repo.finish(
            db,
            job_log=job,
            status=job.status,
            error_message=job.error_message,
            rows_inserted=job.rows_inserted,
            metadata=job.metadata_,
        )
        db.commit()

    if result.failed and result.curated_result is not None:
        staged_path = Path(result.curated_result.path)
        if staged_path.name.startswith(".prices_qfq.tmp-"):
            discard_directory(staged_path)

    logger.info(
        "Daily loop completed: failed=%s sync_trusted=%s factor_rows=%s "
        "alternative=%s",
        result.failed,
        result.sync_result.market_build.trusted_count if result.sync_result else 0,
        result.factor_result.total_rows if result.factor_result else 0,
        result.alternative_result.status if result.alternative_result else "skipped",
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


def _compute_market_qfq_checksum(
    db: Session,
    *,
    source_name: str = "hithink_finance_dump",
    version: str = "v1",
) -> str:
    """Hash canonical forward-adjusted rows in deterministic key order.

    QFQ prices are no longer materialized in ``market.daily_bar``; they are
    derived on demand from ``adjust_type='none'`` rows multiplied by the selected
    vendor-dump QFQ factor. The checksum therefore hashes the computed series
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
                  AND af.source_name = :source_name
                  AND af.version = :version
                ORDER BY af.updated_at DESC, af.id DESC
                LIMIT 1
            ) f ON true
            WHERE d.adjust_type = 'none'
            ORDER BY d.symbol, d.trade_date
        """),
        {"source_name": source_name, "version": version},
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
    qfq_source_name: str = "hithink_finance_dump",
    qfq_version: str = "v1",
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
        "gate_market_coverage_incomplete": False,
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

        coverage = AutoDataSyncRepository().market_coverage_on_date(
            db, trade_date=target_date
        )
        meta["market_coverage"] = coverage
        if coverage["stock_coverage_rate"] < MIN_STOCK_COVERAGE_RATE:
            meta["gate_market_coverage_incomplete"] = True
            meta["reason_codes"].append("MARKET_CROSS_SECTION_INCOMPLETE")

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
                  -- Historical bulk imports may legitimately leave the
                  -- surrogate id NULL. Presence must be tested on the joined
                  -- business key, not on that nullable implementation detail.
                  AND af.symbol IS NULL
            """),
            {"source_name": qfq_source_name, "version": qfq_version},
        ).scalar()
        meta["qfq_missing_rows"] = int(row or 0)
        if meta["qfq_rows"] == 0 or meta["qfq_missing_rows"] > 0:
            meta["gate_qfq_coverage_anomaly"] = True
            meta["reason_codes"].append("QFQ_COVERAGE_GAP")

        # All production price factors must cover the target-day stock universe.
        expected = int(coverage["covered_stocks"])
        factor_coverage: dict[str, dict[str, int | float]] = {}
        incomplete_factors: list[str] = []
        for factor_name in DAILY_PRICE_VOLUME_FACTORS:
            count = int(
                db.execute(
                    text("""
                        SELECT COUNT(DISTINCT fv.symbol)
                        FROM factor.factor_value fv
                        JOIN market.security s ON s.symbol = fv.symbol
                        WHERE fv.factor_name = :factor_name
                          AND fv.factor_version = 'v1-qfq'
                          AND fv.trade_date = :trade_date
                          AND s.security_type = 'stock'
                          AND s.is_active = true
                          AND (s.list_date IS NULL OR s.list_date <= :trade_date)
                          AND (s.delist_date IS NULL OR s.delist_date >= :trade_date)
                    """),
                    {"factor_name": factor_name, "trade_date": target_date},
                ).scalar()
                or 0
            )
            rate = count / expected if expected else 0.0
            factor_coverage[factor_name] = {"rows": count, "coverage_rate": rate}
            if factor_name in CORE_PRICE_FACTORS and rate < MIN_STOCK_COVERAGE_RATE:
                incomplete_factors.append(factor_name)
        meta["daily_factor_coverage"] = factor_coverage
        meta["daily_factor_incomplete"] = [
            name for name, row in factor_coverage.items()
            if row["coverage_rate"] < MIN_STOCK_COVERAGE_RATE
        ]
        meta["core_factor_coverage"] = {
            name: factor_coverage[name] for name in CORE_PRICE_FACTORS
        }
        meta["core_factor_incomplete"] = incomplete_factors
        if incomplete_factors:
            meta["gate_core_factor_partial"] = True
            meta["reason_codes"].append("CORE_FACTOR_COVERAGE_INCOMPLETE")

    except Exception as exc:  # noqa: BLE001 - never crash the loop over gate checks
        logging.getLogger(__name__).warning("Certification gate query error: %s", exc)
        meta["gate_query_error"] = True
        meta["reason_codes"].append(f"GATE_QUERY_ERROR: {exc}")

    return meta
