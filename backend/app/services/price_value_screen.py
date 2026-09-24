"""Nightly, read-only PE/PB/reported-ROE coverage for human research."""

import json
import os
from collections import Counter
from datetime import datetime
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from pathlib import Path
from tempfile import NamedTemporaryFile
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.config.settings import settings
from app.repositories.strategy.price_value_screen import PriceValueScreenRepository
from app.services.job_logging import logged_job
from app.services.price_value_reported_roe import DATASET as ROE_DATASET

SHANGHAI = ZoneInfo("Asia/Shanghai")
SCREEN_VERSION = "relative_value_research_v4"
REQUIRED_EXCHANGES = {"SH", "SZ", "BJ"}
FINANCIAL_TOKENS = (
    "银行", "保险", "证券", "券商", "期货", "金融", "信托", "bank", "insur", "financ",
)
UNKNOWN_INDUSTRIES = {"", "-", "--", "—", "unknown", "n/a", "待定"}
LIMITATIONS = [
    "仅供人工复核的相对估值初筛；东财 f9 是未核验周期的供应商 PE，"
    "不称 PE(TTM)；PB 也取供应商快照。",
    "ROE 只取当晚可核对的供应商财报加权平均值，跨市场比较使用同一主报告期；"
    "若已有更新报告，另列最新值供人工复核。",
    "估值快照和财报缺少完整首次可得及修订历史；不得用于历史全市场收益认证或自动交易。",
    "页面阈值只过滤研究列表；无默认便宜阈值，金融行业需独立模型复核。",
]


def _text(value: object) -> str | None:
    return None if value is None else str(value)


def _positive(value: Decimal | None) -> bool:
    return value is not None and value.is_finite() and value > 0


def _vendor_pe(valuation: object) -> Decimal | None:
    payload = getattr(valuation, "raw_payload", None)
    value = payload.get("f9") if isinstance(payload, dict) else None
    try:
        return Decimal(str(value)) if value is not None else None
    except InvalidOperation:
        return None


def _vendor_reported_roe(valuation: object) -> Decimal | None:
    payload = getattr(valuation, "raw_payload", None)
    value = payload.get("_roe_reported_ratio") if isinstance(payload, dict) else None
    try:
        ratio = Decimal(str(value)) if value is not None else None
    except InvalidOperation:
        return None
    return ratio if ratio is not None and ratio.is_finite() else None


def _industry(value: object) -> str:
    text = str(value or "").strip()
    return "" if text.lower() in UNKNOWN_INDUSTRIES or "\ufffd" in text else text


class PriceValueScreenService:
    def __init__(
        self,
        *,
        repository: PriceValueScreenRepository | None = None,
        output_dir: str | Path | None = None,
    ) -> None:
        self.repository = repository or PriceValueScreenRepository()
        self.output_dir = Path(output_dir or settings.price_value_screen_dir)

    def scan(self, db: Session, *, now: datetime | None = None) -> dict:
        instant = now or datetime.now(SHANGHAI)
        if instant.tzinfo is None:
            raise ValueError("screen clock must include timezone")
        local = instant.astimezone(SHANGHAI)
        target = self.repository.latest_open_date(db, before=local.date())
        global_reasons: list[str] = []
        if target is None:
            global_reasons.append("trading_calendar_missing")
        elif (local.date() - target).days > 4:
            global_reasons.append("trading_calendar_stale")

        bars = self.repository.bars(db, trade_date=target) if target else {}
        valuations = self.repository.valuation(db, trade_date=target) if target else {}
        prep = self.repository.completed_input_prep(db, trade_date=target) if target else None
        prep_metadata = prep.metadata_ or {} if prep else {}
        batch_id = prep_metadata.get("batch_id")
        raw_hashes = (
            self.repository.validated_raw_hashes(db, batch_id=batch_id)
            if isinstance(batch_id, int)
            else None
        )
        roe_hashes = (
            self.repository.validated_raw_hashes(
                db, batch_id=batch_id, dataset=ROE_DATASET
            )
            if isinstance(batch_id, int) and prep_metadata.get("roe_raw_page_hashes")
            else set()
        )
        roe_input_verified = (
            not prep_metadata.get("roe_raw_page_hashes")
            or roe_hashes == set(prep_metadata["roe_raw_page_hashes"])
        )
        if target is not None:
            if prep is None:
                global_reasons.append("input_preparation_missing_for_target")
            elif not raw_hashes or raw_hashes != set(prep_metadata.get("raw_page_hashes", [])):
                global_reasons.append("input_batch_unverified")
            if prep is not None and not roe_input_verified:
                global_reasons.append("roe_input_batch_unverified")
            if self.repository.calendar_exchanges(db, trade_date=target) != REQUIRED_EXCHANGES:
                global_reasons.append("trading_calendar_incomplete")
            if not bars:
                global_reasons.append("trusted_bars_missing_for_target")
            if not valuations:
                global_reasons.append("valuation_missing_for_target")
            if not any(_vendor_reported_roe(value) is not None for value in valuations.values()):
                global_reasons.append("roe_reported_missing_for_target")

        rows = []
        reason_counts: Counter[str] = Counter()
        exchange_coverage_now = {
            exchange: {
                "securities": 0, "trusted_bars": 0, "valuation": 0,
                "reported_roe": 0, "proxy_roe": 0, "valid_pe_pb": 0,
                "industry": 0,
                "listing_date": 0, "treatment_known": 0,
            }
            for exchange in sorted(REQUIRED_EXCHANGES)
        }
        for security in self.repository.stocks(db):
            bar = bars.get(security.symbol)
            valuation = valuations.get(security.symbol)
            pe = _vendor_pe(valuation)
            payload = (
                valuation.raw_payload
                if valuation and isinstance(valuation.raw_payload, dict) else {}
            )
            reported_roe = _vendor_reported_roe(valuation)
            reported_hash = payload.get("_roe_raw_page_hash")
            newer_hash = payload.get("_roe_newer_raw_page_hash")
            newer_verified = bool(
                newer_hash and roe_input_verified and newer_hash in roe_hashes
            )
            newer_roe = None
            if newer_verified:
                try:
                    newer_roe = Decimal(str(payload.get("_roe_newer_reported_ratio")))
                except (InvalidOperation, TypeError):
                    pass
                if newer_roe is not None and not newer_roe.is_finite():
                    newer_roe = None
            if reported_roe is not None and (
                not roe_input_verified or reported_hash not in roe_hashes
            ):
                reported_roe = None
            coverage = exchange_coverage_now.get(security.exchange)
            if coverage is not None:
                coverage["securities"] += 1
                coverage["trusted_bars"] += bool(
                    bar and bar.quality_status in {"trusted", "manual_fixed"}
                    and not bar.is_suspect
                )
                coverage["valuation"] += valuation is not None
                coverage["valid_pe_pb"] += bool(
                    valuation and _positive(pe) and _positive(valuation.pb)
                )
                coverage["reported_roe"] += reported_roe is not None
                coverage["listing_date"] += security.list_date is not None
                coverage["treatment_known"] += bool(
                    bar and (bar.special_treatment_status or "").strip().lower()
                    in {"normal", "none", "st", "*st"}
                )
            reasons = list(global_reasons)
            if not security.is_active or (
                target and security.delist_date and security.delist_date <= target
            ):
                reasons.append("inactive_or_delisted")
            if target and (security.list_date is None or security.list_date > target):
                reasons.append("listing_unverified")
            industry = _industry(security.industry)
            vendor_industry = _industry(payload.get("_industry_reported"))
            if coverage is not None:
                coverage["industry"] += bool(industry)
            if not industry:
                reasons.append("industry_missing")
            if industry and vendor_industry and industry != vendor_industry:
                reasons.append("industry_source_conflict")
            if any(
                token in name.lower()
                for name in (industry, vendor_industry)
                for token in FINANCIAL_TOKENS
            ):
                reasons.append("financial_industry_separate_model")
            if bar is None:
                reasons.append("trusted_quote_missing")
            elif (
                bar.quality_status not in {"trusted", "manual_fixed"}
                or bar.is_suspect
                or not _positive(bar.close)
                or not bar.source_name
            ):
                reasons.append("untrusted_quote")
            elif bar.is_suspended:
                reasons.append("suspended")
            elif (bar.special_treatment_status or "").strip().lower() not in {
                "normal", "none"
            }:
                reasons.append("treatment_unknown_or_restricted")
            elif not _positive(bar.volume) or not _positive(bar.amount):
                reasons.append("liquidity_missing")

            if valuation is None:
                reasons.append("valuation_missing")
            elif (
                valuation.currency != "CNY"
                or not _positive(pe)
                or not _positive(valuation.pb)
            ):
                reasons.append("valuation_invalid_or_nonpositive")
            elif (
                bar is not None
                and _positive(bar.close)
                and _positive(valuation.close)
                and abs(valuation.close - bar.close) > Decimal("0.01")
            ):
                reasons.append("vendor_close_mismatch")
            elif prep is not None and (
                not isinstance(valuation.raw_payload, dict)
                or valuation.raw_payload.get("_research_batch_id")
                != prep_metadata.get("batch_id")
                or valuation.raw_payload.get("_raw_page_hash") not in (raw_hashes or set())
            ):
                reasons.append("valuation_lineage_unverified")

            if reported_hash and (not roe_input_verified or reported_hash not in roe_hashes):
                reasons.append("roe_observation_lineage_unverified")
            if newer_hash and not newer_verified:
                reasons.append("roe_observation_lineage_unverified")
            if reported_roe is None:
                reasons.append("roe_reported_missing")
            elif (
                prep_metadata.get("roe_report_dates")
                and payload.get("_roe_report_date") != prep_metadata["roe_report_dates"][0]
            ):
                reasons.append("roe_report_period_not_comparable")

            # A financial company still appears in the full-market report, but
            # the generic industrial-company research list excludes it.
            status = "ready_for_review" if not reasons else "needs_review"
            reason_counts.update(reasons)
            rows.append(
                {
                    "symbol": security.symbol,
                    "name": security.name,
                    "industry": industry or None,
                    "vendor_industry_hint": vendor_industry or None,
                    "status": status,
                    "reason_codes": reasons,
                    "close": _text(bar.close) if bar else None,
                    "pe_vendor_unverified": _text(pe),
                    "pb_vendor": _text(valuation.pb) if valuation else None,
                    "roe_single_period_proxy": None,
                    "roe_reported_vendor": _text(reported_roe),
                    "roe_method": "vendor_weighted_average" if reported_roe is not None else None,
                    "roe_report_date": payload.get("_roe_report_date"),
                    "roe_newer_report_date": (
                        payload.get("_roe_newer_report_date") if newer_roe is not None
                        else None
                    ),
                    "roe_newer_reported_vendor": _text(newer_roe),
                    "roe_newer_raw_page_hash": newer_hash if newer_roe is not None else None,
                    "roe_notice_date": payload.get("_roe_notice_date"),
                    "roe_raw_page_hash": reported_hash,
                    "valuation_source": valuation.source_name if valuation else None,
                    "valuation_updated_at": _text(valuation.updated_at) if valuation else None,
                    "valuation_raw_page_hash": (
                        valuation.raw_payload.get("_raw_page_hash")
                        if valuation and isinstance(valuation.raw_payload, dict)
                        else None
                    ),
                    "bar_source": bar.source_name if bar else None,
                    "bar_updated_at": _text(bar.updated_at) if bar else None,
                    "roe_factor_name": None,
                    "roe_factor_version": None,
                    "roe_factor_run_id": None,
                }
            )

        ready = sum(row["status"] == "ready_for_review" for row in rows)
        if ready == 0 and not global_reasons:
            global_reasons.append("no_reviewable_candidates")
        status = "blocked" if global_reasons else "ready" if ready == len(rows) else "partial"
        sync_job = self.repository.latest_daily_sync_job(db)
        archive = (
            self.repository.latest_historical_archive(db, trade_date=target)
            if target else None
        )
        archive_metadata = archive.metadata_ or {} if archive else {}
        report = {
            "screen_version": SCREEN_VERSION,
            "code_version": settings.app_code_version,
            "generated_at": local.isoformat(),
            "target_trade_date": target.isoformat() if target else None,
            "status": status,
            "global_reason_codes": global_reasons,
            "universe_count": len(rows),
            "ready_count": ready,
            "needs_review_count": len(rows) - ready,
            "reason_counts": dict(sorted(reason_counts.items())),
            "valuation_source": "eastmoney",
            "input_batch_id": prep_metadata.get("batch_id"),
            "input_job_id": prep.id if prep else None,
            "supplier_total": prep_metadata.get("supplier_total"),
            "valuation_accepted_count": prep_metadata.get("accepted_count"),
            "exchange_coverage": prep_metadata.get("exchange_coverage"),
            "current_coverage_by_exchange": exchange_coverage_now,
            "latest_daily_sync": (
                {
                    "job_id": sync_job.id,
                    "status": sync_job.status,
                    "started_at": _text(sync_job.started_at),
                    "finished_at": _text(sync_job.finished_at),
                    "error_summary": (
                        sync_job.error_message.splitlines()[0][:240]
                        if sync_job.error_message else None
                    ),
                }
                if sync_job else None
            ),
            "historical_raw_archive": (
                {
                    "job_id": archive.id,
                    "observed_at_utc": archive_metadata.get("observed_at_utc"),
                    "supplier_total": archive_metadata.get("supplier_total"),
                    "pit_verified": False,
                    "publication_scope": "raw_only",
                }
                if archive else None
            ),
            "daily_sync_job_id": prep_metadata.get("daily_sync_job_id"),
            "prepared_factor_run_id": None,
            "roe_reported_count": prep_metadata.get("roe_reported_count"),
            "roe_report_dates": prep_metadata.get("roe_report_dates"),
            "roe_raw_page_hashes": prep_metadata.get("roe_raw_page_hashes"),
            "roe_factor_name": None,
            "roe_factor_version": None,
            "research_only": True,
            "pit_verified": False,
            "execution_eligible": False,
            "error_message": None,
            "limitations": LIMITATIONS,
            "rows": rows,
        }
        stable = {key: value for key, value in report.items() if key != "generated_at"}
        report["snapshot_hash"] = sha256(
            json.dumps(stable, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        return report

    def run(self, db: Session, *, now: datetime | None = None) -> dict:
        try:
            report = self.scan(db, now=now)
        except Exception:
            with logged_job(db, job_name="price_value_screen", job_type="schedule"):
                raise
        latest_job = self.repository.latest_screen_job(db)
        if latest_job is not None:
            metadata = latest_job.metadata_ or {}
            if metadata.get("snapshot_hash") == report["snapshot_hash"]:
                existing = self._load_artifact(metadata.get("artifact_name"))
                if existing is not None:
                    existing["run_id"] = latest_job.id
                    return existing

        with logged_job(
            db,
            job_name="price_value_screen",
            job_type="schedule",
            metadata={
                "target_trade_date": report["target_trade_date"],
                "screen_version": SCREEN_VERSION,
                "snapshot_hash": report["snapshot_hash"],
            },
        ) as job:
            report["run_id"] = job.id
            artifact_name = (
                f"{report['target_trade_date'] or 'no-calendar'}-"
                f"{report['snapshot_hash'][:20]}.json"
            )
            self.output_dir.mkdir(parents=True, exist_ok=True)
            with NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=self.output_dir, delete=False,
                prefix=".price-value-", suffix=".tmp",
            ) as output:
                json.dump(report, output, ensure_ascii=False, separators=(",", ":"))
                temporary_name = output.name
            os.replace(temporary_name, self.output_dir / artifact_name)
            job.rows_read = report["universe_count"]
            job.rows_failed = report["needs_review_count"]
            job.status = "failed" if report["status"] == "blocked" else (
                "partial" if report["status"] == "partial" else "success"
            )
            job.error_message = ", ".join(report["global_reason_codes"]) or None
            job.metadata_ = {
                **(job.metadata_ or {}),
                "artifact_name": artifact_name,
                "status": report["status"],
                "ready_count": report["ready_count"],
                "needs_review_count": report["needs_review_count"],
                "reason_counts": report["reason_counts"],
            }
        return report

    def _load_artifact(self, name: str | None) -> dict | None:
        if not name or Path(name).name != name:
            return None
        path = self.output_dir / name
        if not path.is_file():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def latest(self, db: Session) -> dict | None:
        job = self.repository.latest_screen_job(db)
        if job is None:
            return None
        report = self._load_artifact((job.metadata_ or {}).get("artifact_name"))
        if report is not None:
            report["run_id"] = job.id
            return report
        running = job.status == "running"
        failure_type = (job.error_message or "UnknownError").split(":", 1)[0]
        reason_code = "screen_job_running" if running else "screen_job_failed"
        metadata = job.metadata_ or {}
        target_text = metadata.get("target_trade_date")
        try:
            target = datetime.fromisoformat(target_text).date() if target_text else None
        except ValueError:
            target = None
        sync_job = self.repository.latest_daily_sync_job(db)
        archive = (
            self.repository.latest_historical_archive(db, trade_date=target)
            if target else None
        )
        archive_metadata = archive.metadata_ or {} if archive else {}
        return {
            "run_id": job.id,
            "screen_version": SCREEN_VERSION,
            "code_version": settings.app_code_version,
            "generated_at": (job.finished_at or job.started_at).isoformat(),
            "target_trade_date": metadata.get("target_trade_date"),
            "status": "blocked",
            "global_reason_codes": [reason_code],
            "error_message": (
                "筛选任务正在运行，结果尚未生成。" if running
                else f"筛选任务失败（{failure_type}），请查看任务日志。"
            ),
            "universe_count": 0,
            "ready_count": 0,
            "needs_review_count": 0,
            "reason_counts": {reason_code: 1},
            "valuation_source": "eastmoney",
            "input_batch_id": None,
            "input_job_id": None,
            "supplier_total": None,
            "valuation_accepted_count": None,
            "exchange_coverage": None,
            "current_coverage_by_exchange": {},
            "latest_daily_sync": (
                {
                    "job_id": sync_job.id,
                    "status": sync_job.status,
                    "started_at": _text(sync_job.started_at),
                    "finished_at": _text(sync_job.finished_at),
                    "error_summary": (
                        sync_job.error_message.splitlines()[0][:240]
                        if sync_job.error_message else None
                    ),
                }
                if sync_job else None
            ),
            "historical_raw_archive": (
                {
                    "job_id": archive.id,
                    "observed_at_utc": archive_metadata.get("observed_at_utc"),
                    "supplier_total": archive_metadata.get("supplier_total"),
                    "pit_verified": False,
                    "publication_scope": "raw_only",
                }
                if archive else None
            ),
            "daily_sync_job_id": None,
            "prepared_factor_run_id": None,
            "roe_reported_count": None,
            "roe_report_dates": None,
            "roe_raw_page_hashes": None,
            "roe_factor_name": None,
            "roe_factor_version": None,
            "research_only": True,
            "pit_verified": False,
            "execution_eligible": False,
            "limitations": LIMITATIONS,
            "rows": [],
            "snapshot_hash": sha256(f"screen-failed:{job.id}".encode()).hexdigest(),
        }

    def profit_status(self, db: Session) -> dict:
        run = self.repository.latest_price_value_backtest(db)
        accounts = self.repository.paper_accounts(db)
        paper = []
        for account in accounts:
            snapshot = self.repository.latest_paper_snapshot(db, account_id=account.id)
            paper.append(
                {
                    "account_id": account.id,
                    "account_name": account.name,
                    "latest_valuation_date": (
                        snapshot.valuation_date.isoformat() if snapshot else None
                    ),
                    "latest_total_equity": _text(snapshot.total_equity) if snapshot else None,
                    "profit": None,
                    "profit_reason": "no_verified_capital_baseline",
                }
            )
        backtest = None
        if run and run.final_equity is not None:
            backtest = {
                "run_id": run.id,
                "end_date": run.end_date.isoformat(),
                "initial_cash": _text(run.initial_cash),
                "final_equity": _text(run.final_equity),
                "hypothetical_profit": _text(run.final_equity - run.initial_cash),
                "total_return": _text(run.total_return),
                "research_only": True,
                "pit_verified": False,
            }
        return {
            "value_backtest": backtest,
            "paper_accounts": paper,
            "paper_profit_note": "模拟账户缺少可核验初始投入与资金流基线，不计算累计利润。",
        }
