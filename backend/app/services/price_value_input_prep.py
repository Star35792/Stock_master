"""Collect a same-evening relative-valuation observation for manual review."""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from hashlib import sha256
from itertools import chain
from json import dumps
from uuid import uuid4
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.config.settings import settings
from app.data_sources.eastmoney import (
    EastmoneyDataError,
    EastmoneyDataSource,
    _to_decimal,
    normalize_symbol,
)
from app.models.raw import ImportBatch, RawVendorSnapshot
from app.repositories.market import ValuationSnapshotRepository
from app.repositories.raw import RawDailyBarImportRepository
from app.repositories.strategy.price_value_screen import PriceValueScreenRepository
from app.services.job_logging import logged_job
from app.services.price_value_reported_roe import (
    PriceValueReportedRoeService,
    ReportedRoe,
)

SHANGHAI = ZoneInfo("Asia/Shanghai")
DATASET = "price_value_valuation_observation"


def select_comparable_roe_period(
    *,
    report_dates: list[str],
    period_observations: dict[str, dict[str, ReportedRoe]],
    matched_by_exchange: dict[str, set[str]],
) -> str:
    """Choose the newest period covering at least 80% of matched stocks per exchange."""
    if not any(matched_by_exchange.values()):
        raise ValueError("same-day reported ROE has no matched securities")
    for period in report_dates:
        period_rows = period_observations.get(period, {})
        if all(
            not symbols or 5 * sum(
                symbol in period_rows
                for symbol in symbols
            ) >= 4 * len(symbols)
            for symbols in matched_by_exchange.values()
        ):
            return period
    raise ValueError("comparable reported ROE coverage is incomplete")


class PriceValueInputPrepService:
    def __init__(
        self,
        *,
        source: EastmoneyDataSource | None = None,
        screen_repository: PriceValueScreenRepository | None = None,
        raw_repository: RawDailyBarImportRepository | None = None,
        valuation_repository: ValuationSnapshotRepository | None = None,
        reported_roe_service: PriceValueReportedRoeService | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.source = source or EastmoneyDataSource()
        self.screen_repository = screen_repository or PriceValueScreenRepository()
        self.raw_repository = raw_repository or RawDailyBarImportRepository()
        self.valuation_repository = valuation_repository or ValuationSnapshotRepository()
        self.reported_roe_service = reported_roe_service or PriceValueReportedRoeService(
            source=self.source
        )
        self.clock = clock or (lambda: datetime.now(SHANGHAI))

    def _capture_time(self, target) -> datetime:
        captured = self.clock()
        if captured.tzinfo is None or captured.astimezone(SHANGHAI).date() != target:
            raise ValueError("research observation crossed its target trading date")
        return captured.astimezone(UTC)

    def _fail_running_batch(self, db: Session, job, exc: Exception) -> None:
        if job.batch_id is None:
            return
        batch = db.get(ImportBatch, job.batch_id)
        if batch is None or batch.status != "running":
            return
        self.raw_repository.finish_batch(
            db,
            batch,
            status="failed",
            row_count=batch.row_count,
            accepted_count=0,
            rejected_count=batch.row_count,
            error_message=f"{type(exc).__name__}: {exc}",
        )

    def run(self, db: Session, *, now: datetime | None = None) -> dict:
        instant = now or self.clock()
        if instant.tzinfo is None:
            raise ValueError("input preparation clock must include timezone")
        local = instant.astimezone(SHANGHAI)
        target = local.date()
        if local.hour < 21:
            raise ValueError("current vendor quote may only be collected after 21:00 Shanghai")

        prior = self.screen_repository.completed_input_prep(db, trade_date=target)
        if prior is not None:
            return dict(prior.metadata_ or {})

        with logged_job(
            db,
            job_name="price_value_input_prep",
            job_type="import",
            on_failure=self._fail_running_batch,
            metadata={
                "target_trade_date": target.isoformat(),
                "code_version": settings.app_code_version,
            },
        ) as job:
            latest = self.screen_repository.latest_open_date(
                db, before=target + timedelta(days=1)
            )
            if latest != target or self.screen_repository.calendar_exchanges(
                db, trade_date=target
            ) != {"SH", "SZ", "BJ"}:
                raise ValueError("target date is not a complete mainland trading session")
            sync = self.screen_repository.completed_daily_sync(db, trade_date=target)
            if sync is None:
                raise ValueError("same-day trusted daily data sync has not completed")
            bars = self.screen_repository.bars(db, trade_date=target)
            securities = {
                security.symbol: security
                for security in self.screen_repository.stocks(db)
                if security.is_active
                and security.currency == "CNY"
                and security.exchange in {"SH", "SZ", "BJ"}
            }
            eligible_bars = {
                symbol: bar
                for symbol, bar in bars.items()
                if symbol in securities
                and bar.quality_status in {"trusted", "manual_fixed"}
                and not bar.is_suspect
                and bar.close is not None
                and bar.close > 0
            }
            if not eligible_bars or not securities:
                raise ValueError("same-day trusted stock bars or securities are missing")

            batch_no = f"pv_obs_{target:%Y%m%d}_{uuid4().hex[:12]}"
            batch = self.raw_repository.create_batch(
                db,
                batch_no=batch_no,
                source_name=self.source.name,
                source_type=self.source.source_type,
                dataset=DATASET,
                request_params={
                    "target_trade_date": target.isoformat(),
                    "endpoint_selection": "current_clist_then_same_day_dated_fallback",
                    "collected_after": local.isoformat(),
                    "supplier_asof_verified": False,
                    "unadjusted_daily_sync_job_id": sync.id,
                    "code_version": settings.app_code_version,
                },
            )
            db.commit()
            batch_id = batch.id
            job.batch_id = batch_id
            db.commit()
            items_by_symbol: dict[str, tuple[dict, str, str]] = {}
            received = 0
            expected_total = None
            page_hashes: list[str] = []
            try:
                valuation_mode = "current_clist"
                pages = iter(self.source.iter_valuation_snapshot_pages())
                try:
                    first_page = next(pages)
                except EastmoneyDataError:
                    if not hasattr(self.source, "iter_same_day_dated_valuation_pages"):
                        raise
                    valuation_mode = "same_day_dated_fallback"
                    pages = iter(self.source.iter_same_day_dated_valuation_pages(trade_date=target))
                    first_page = next(pages)
                for page, total, items, response_json in chain((first_page,), pages):
                    if expected_total is not None and total != expected_total:
                        raise ValueError("vendor total changed during pagination")
                    expected_total = total
                    fetched_at = self.clock().astimezone(UTC).isoformat()
                    payload = {
                        "page": page,
                        "supplier_total": total,
                        "fetched_at_utc": fetched_at,
                        "target_trade_date": target.isoformat(),
                        "supplier_asof_verified": False,
                        "items": items,
                        "response_json": response_json,
                    }
                    page_hash = sha256(response_json.encode("utf-8")).hexdigest()
                    db.add(
                        RawVendorSnapshot(
                            batch_id=batch_id,
                            source_name=self.source.name,
                            dataset=DATASET,
                            snapshot_date=target,
                            raw_payload=payload,
                            row_hash=page_hash,
                        )
                    )
                    db.commit()  # Preserve each raw page if later fetch or validation fails.
                    page_hashes.append(page_hash)
                    self._capture_time(target)
                    for item in items:
                        code = item.get("f12")
                        if not code:
                            raise ValueError("vendor row has no security code")
                        symbol = normalize_symbol(str(code))
                        if symbol in items_by_symbol:
                            raise ValueError(f"duplicate vendor security: {symbol}")
                        items_by_symbol[symbol] = (item, page_hash, fetched_at)
                    received += len(items)
                if not expected_total or received != expected_total:
                    raise ValueError("vendor pagination is incomplete")
                accepted = []
                vendor_symbols: set[str] = set()
                for symbol, (item, page_hash, fetched_at) in items_by_symbol.items():
                    if symbol not in securities:
                        continue
                    security = securities[symbol]
                    expected_market = "1" if security.exchange == "SH" else "0"
                    if str(item.get("f13")) != expected_market:
                        continue
                    if symbol.rsplit(".", 1)[-1] != security.exchange:
                        continue
                    vendor_symbols.add(symbol)
                    bar = eligible_bars.get(symbol)
                    close = _to_decimal(item.get("f2"))
                    if (
                        bar is None
                        or close is None
                        or not close.is_finite()
                        or close <= 0
                        or abs(close - bar.close) > Decimal("0.01")
                    ):
                        continue
                    accepted.append(
                        {
                            "symbol": symbol,
                            "trade_date": target,
                            "close": close,
                            "total_market_cap": _to_decimal(item.get("f20")),
                            "float_market_cap": _to_decimal(item.get("f21")),
                            # f9's dynamic/TTM period is not supplier-certified.
                            # Preserve it as a generic vendor PE in raw_payload.
                            "pe_ttm": None,
                            "pb": _to_decimal(item.get("f23")),
                            "ps_ttm": None,
                            "currency": "CNY",
                            "source_name": self.source.name,
                            "raw_payload": {
                                **item,
                                "_industry_reported": (
                                    (item.get("_supplier_payload") or {}).get("BOARD_NAME")
                                    if isinstance(item.get("_supplier_payload"), dict) else None
                                ),
                                "_research_batch_id": batch_id,
                                "_raw_page_hash": page_hash,
                                "_fetched_at_utc": fetched_at,
                                "_supplier_asof_verified": False,
                            },
                        }
                    )
                accepted_symbols = {row["symbol"] for row in accepted}
                reported_roe = self.reported_roe_service.collect_into_batch(
                    db, batch_id=batch_id, target=target, observed_at=local
                )
                accepted_symbols = {row["symbol"] for row in accepted}
                matched_by_exchange = {
                    exchange: {
                        symbol for symbol in accepted_symbols
                        if securities[symbol].exchange == exchange
                    }
                    for exchange in ("SH", "SZ", "BJ")
                }
                primary_roe_period = select_comparable_roe_period(
                    report_dates=reported_roe.report_dates,
                    period_observations=reported_roe.period_observations,
                    matched_by_exchange=matched_by_exchange,
                )
                primary_observations = reported_roe.period_observations[primary_roe_period]
                for row in accepted:
                    observation = (
                        primary_observations.get(row["symbol"])
                        or reported_roe.observations.get(row["symbol"])
                    )
                    if observation is None:
                        continue
                    row["raw_payload"].update({
                        "_roe_reported_ratio": str(observation.ratio),
                        "_roe_report_date": observation.report_date.isoformat(),
                        "_roe_notice_date": observation.notice_date.isoformat(),
                        "_roe_disclosed_at": observation.disclosed_at.isoformat(),
                        "_roe_raw_page_hash": observation.raw_page_hash,
                        "_industry_reported": observation.industry,
                    })
                    latest = reported_roe.observations.get(row["symbol"])
                    if latest and latest.report_date > observation.report_date:
                        row["raw_payload"]["_roe_newer_report_date"] = (
                            latest.report_date.isoformat()
                        )
                        row["raw_payload"]["_roe_newer_reported_ratio"] = str(
                            latest.ratio
                        )
                        row["raw_payload"]["_roe_newer_raw_page_hash"] = (
                            latest.raw_page_hash
                        )
                reported_symbols = accepted_symbols & reported_roe.observations.keys()
                coverage = {}
                for exchange in ("SH", "SZ", "BJ"):
                    expected = {
                        symbol for symbol, security in securities.items()
                        if security.exchange == exchange
                    }
                    trusted = expected & eligible_bars.keys()
                    vendor = trusted & vendor_symbols
                    matched = trusted & accepted_symbols
                    reported = matched & reported_symbols
                    comparable = {
                        symbol for symbol in reported
                        if symbol in primary_observations
                    }
                    valid_pe_pb = {
                        row["symbol"] for row in accepted
                        if row["symbol"] in matched
                        and (pe := _to_decimal(row["raw_payload"].get("f9"))) is not None
                        and pe.is_finite() and pe > 0
                        and row["pb"] is not None and row["pb"].is_finite()
                        and row["pb"] > 0
                    }
                    coverage[exchange] = {
                        "active_stocks": len(expected),
                        "trusted_bars": len(trusted),
                        "vendor_mapped": len(vendor),
                        "close_matched": len(matched),
                        "valid_pe_pb": len(valid_pe_pb),
                        "reported_roe": len(reported),
                        "comparable_reported_roe": len(comparable),
                    }
                    if expected and (
                        not trusted
                        or len(trusted) < len(expected) * 0.9
                        or len(vendor) < len(trusted) * 0.95
                        or len(matched) < len(trusted) * 0.9
                    ):
                        raise ValueError(f"{exchange} research input coverage is incomplete")
                    if matched and len(valid_pe_pb) * 2 < len(matched):
                        raise ValueError(f"{exchange} valid PE/PB coverage is incomplete")
                    if matched and len(comparable) * 5 < len(matched) * 4:
                        raise ValueError(
                            f"{exchange} comparable reported ROE coverage is incomplete"
                        )

                prior_rows = [
                    row
                    for row in self.valuation_repository.existing_research_rows(
                        db, trade_date=target
                    )
                    if row.symbol in accepted_symbols
                ]
                if prior_rows:
                    prior_payload = {
                        "target_trade_date": target.isoformat(),
                        "captured_at_utc": datetime.now(UTC).isoformat(),
                        "previous_rows": [
                            {
                                "symbol": row.symbol,
                                "market_row_id": row.id,
                                "close": str(row.close) if row.close is not None else None,
                                "total_market_cap": (
                                    str(row.total_market_cap)
                                    if row.total_market_cap is not None else None
                                ),
                                "float_market_cap": (
                                    str(row.float_market_cap)
                                    if row.float_market_cap is not None else None
                                ),
                                "total_shares": (
                                    str(row.total_shares) if row.total_shares is not None else None
                                ),
                                "float_shares": (
                                    str(row.float_shares) if row.float_shares is not None else None
                                ),
                                "pe_ttm": str(row.pe_ttm) if row.pe_ttm is not None else None,
                                "pb": str(row.pb) if row.pb is not None else None,
                                "ps_ttm": str(row.ps_ttm) if row.ps_ttm is not None else None,
                                "currency": row.currency,
                                "source_name": row.source_name,
                                "created_at": (
                                    row.created_at.isoformat() if row.created_at else None
                                ),
                                "updated_at": (
                                    row.updated_at.isoformat() if row.updated_at else None
                                ),
                                "raw_payload": row.raw_payload,
                            }
                            for row in prior_rows
                        ],
                    }
                    db.add(
                        RawVendorSnapshot(
                            batch_id=batch_id,
                            source_name=self.source.name,
                            dataset="price_value_replaced_market_rows",
                            snapshot_date=target,
                            raw_payload=prior_payload,
                            row_hash=sha256(
                                dumps(prior_payload, sort_keys=True).encode()
                            ).hexdigest(),
                        )
                    )

                if not reported_symbols:
                    raise ValueError("same-day reported ROE has no eligible values")
                self._capture_time(target)
                inserted = self.valuation_repository.insert_research_rows(db, rows=accepted)
                batch = db.get(ImportBatch, batch_id)
                self.raw_repository.finish_batch(
                    db,
                    batch,
                    status="success",
                    row_count=received,
                    accepted_count=len(accepted),
                    rejected_count=received - len(accepted),
                )
                job.rows_read = received
                job.rows_inserted = inserted
                job.rows_failed = received - len(accepted)
                job.metadata_ = {
                    **(job.metadata_ or {}),
                    "batch_id": batch_id,
                    "batch_no": batch_no,
                    "raw_page_hashes": page_hashes,
                    "valuation_mode": valuation_mode,
                    "supplier_total": expected_total,
                    "accepted_count": len(accepted),
                    "inserted_count": inserted,
                    "roe_reported_count": len(reported_symbols),
                    "roe_report_dates": [
                        primary_roe_period,
                        *(period for period in reported_roe.report_dates
                          if period != primary_roe_period),
                    ],
                    "roe_primary_report_date": primary_roe_period,
                    "roe_source_rows": reported_roe.source_rows,
                    "roe_raw_page_hashes": reported_roe.raw_page_hashes,
                    "exchange_coverage": coverage,
                    "daily_sync_job_id": sync.id,
                    "pit_verified": False,
                    "research_only": True,
                }
                self._capture_time(target)
            except Exception as exc:
                db.rollback()
                batch = db.get(ImportBatch, batch_id)
                self.raw_repository.finish_batch(
                    db,
                    batch,
                    status="failed",
                    row_count=received,
                    accepted_count=0,
                    rejected_count=received,
                    error_message=f"{type(exc).__name__}: {exc}",
                )
                db.commit()
                raise
        return dict(job.metadata_ or {})
