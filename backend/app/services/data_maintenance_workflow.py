"""Bounded adapters for the existing daily data and factor production workflow."""

import json
from datetime import date, timedelta
from hashlib import sha256

from app.config.settings import settings
from app.repositories.market import SecurityRepository, TradingCalendarRepository
from app.repositories.strategy.price_value_screen import PriceValueScreenRepository
from app.repositories.system import JobLogRepository
from app.services.auto_data_sync import AutoDataSyncService, completed_daily_cutoff
from app.services.daily_data_workflow import run_daily_loop

FACTOR_LOOKBACK_DAYS = 14


class DataMaintenanceWorkflow:
    def checks(self, db, *, target_date: date | None = None):
        cutoff = completed_daily_cutoff()
        target = target_date or PriceValueScreenRepository().latest_open_date(
            db, before=cutoff + timedelta(days=1)
        )
        calendar = (
            TradingCalendarRepository().get_many_on_date(
                db, exchanges={"SH", "SZ", "BJ"}, trade_date=target
            )
            if target is not None
            else []
        )
        symbols = sorted(set(SecurityRepository().get_active_symbols(db)))
        sync_repository = AutoDataSyncService().repository
        completed = sync_repository.completed_through(db)
        calendar_covers_cutoff = target is not None
        if target is not None:
            try:
                sync_repository.require_calendar_coverage(db, start_date=target, end_date=cutoff)
            except ValueError:
                calendar_covers_cutoff = False
        return {
            "target_trade_date": target.isoformat() if target else None,
            "safe_cutoff": cutoff.isoformat(),
            "completed_through": self._date(completed),
            "newer_market_data": target is not None
            and (
                (completed is not None and completed > target)
                or sync_repository.has_unadjusted_bars_after(db, trade_date=target)
            ),
            "calendar_ready": (
                target is not None
                and calendar_covers_cutoff
                and target <= cutoff
                and {row.exchange for row in calendar} == {"SH", "SZ", "BJ"}
                and any(row.is_open for row in calendar)
            ),
            "api_key_configured": bool(settings.hithink_finance_api_key),
            "curated_enabled": settings.factor_reads_curated,
            "active_symbols": len(symbols),
            "universe_sha256": sha256(json.dumps(symbols).encode()).hexdigest(),
            "factor_lookback_days": FACTOR_LOOKBACK_DAYS,
            "scope": "trusted_daily_qfq_and_factor_values",
            "alternative_sources": ["hithink", "eastmoney"],
        }

    @staticmethod
    def executable(checks):
        return bool(
            checks["calendar_ready"]
            and not checks["newer_market_data"]
            and checks["api_key_configured"]
            and checks["curated_enabled"]
            and checks["active_symbols"]
        )

    def validate_frozen(self, db, checks):
        target = date.fromisoformat(checks["target_trade_date"])
        current = self.checks(db, target_date=target)
        if current["newer_market_data"]:
            raise ValueError("已有晚于原目标日的日线，请重新预检确认。")
        immutable = (
            "target_trade_date",
            "universe_sha256",
            "active_symbols",
            "factor_lookback_days",
            "scope",
            "alternative_sources",
        )
        if not self.executable(current) or any(current[key] != checks[key] for key in immutable):
            raise ValueError("原同步范围或数据源条件已变化，请重新预检确认。")
        return target

    def execute(self, db, *, checks, session_factory, job_repository: JobLogRepository):
        target = self.validate_frozen(db, checks)
        symbols = sorted(set(SecurityRepository().get_active_symbols(db)))
        sync = AutoDataSyncService().sync(db, cutoff=target)
        db.commit()
        payload = {
            "problem_closed": False,
            "sync": {
                "completed_through": sync.end_date.isoformat(),
                "advanced": sync.advanced,
                "rows_inserted": sync.rows_inserted,
                "market_rows": sync.market_rows,
                "batch_nos": list(sync.batch_nos),
            },
            "recheck": {
                "target_trade_date": target.isoformat(),
                "coverage": sync.coverage,
                "scope": checks["scope"],
            },
        }
        if sync.end_date < target:
            payload["recheck"]["reason_codes"] = ["DAILY_SYNC_BEHIND_TARGET"]
            return payload, "partial"
        # The dump sync may refresh reference data. A changed universe must not
        # silently enlarge the factor computation approved by the user.
        current = sorted(set(SecurityRepository().get_active_symbols(db)))
        if current != symbols:
            payload["recheck"]["reason_codes"] = ["REFERENCE_UNIVERSE_CHANGED"]
            return payload, "partial"
        # Finish the current transaction before the existing workflow opens its
        # own independent sessions and audited jobs.
        db.commit()
        result = run_daily_loop(
            symbols=symbols,
            target_date=target,
            skip_sync=True,
            factor_lookback_days=checks["factor_lookback_days"],
            code_version=settings.app_code_version,
            session_factory=session_factory,
            job_log_repository=job_repository,
        )
        original = job_repository.get(db, result.job_id) if result.job_id else None
        metadata = (original.metadata_ or {}) if original is not None else {}
        factors = result.factor_result
        payload["daily_loop_job_id"] = result.job_id
        payload["recheck"].update(
            {
                "daily_loop_status": original.status if original else "unknown",
                "gate_status": metadata.get("gate_status"),
                "publish_eligible": metadata.get("publish_eligible") is True,
                "reason_codes": [
                    str(code).split(":", 1)[0] for code in metadata.get("reason_codes", [])
                ],
                "market_release_id": result.market_release_id,
                "curated_release_id": result.curated_release_id,
                "factor_rows": factors.total_rows if factors else 0,
                "failed_services": factors.failed if factors else [],
                "empty_services": factors.empty if factors else [],
                "factor_services": {
                    name: {
                        key: value
                        for key, value in outcome.items()
                        if key in {"status", "total_rows", "input_rows", "run_ids"}
                    }
                    for name, outcome in factors.services.items()
                }
                if factors
                else {},
                "alternative_status": result.alternative_result.status
                if result.alternative_result
                else "unknown",
            }
        )
        closed = bool(
            original is not None
            and original.status == "success"
            and not result.failed
            and not result.skipped
            and factors is not None
            and factors.total_rows > 0
            and not factors.failed
            and not factors.empty
            and metadata.get("gate_status") == "passed"
            and metadata.get("publish_eligible") is True
            and result.market_release_id
            and result.curated_release_id
        )
        payload["problem_closed"] = closed
        status = (
            "success" if closed else ("failed" if result.failed or original is None else "partial")
        )
        return payload, status

    @staticmethod
    def _date(value):
        return value.isoformat() if value is not None else None
