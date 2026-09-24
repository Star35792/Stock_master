"""Collect dated Eastmoney valuation evidence into raw only.

Historical vendor rows are observed now, so they must not be published into
market.valuation_snapshot until point-in-time consumption is isolated.
"""

from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from uuid import uuid4
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.config.settings import settings
from app.data_sources.eastmoney import EastmoneyDataSource, normalize_symbol
from app.models.raw import ImportBatch, RawVendorSnapshot
from app.repositories.raw import RawDailyBarImportRepository
from app.services.job_logging import logged_job

DATASET = "price_value_historical_valuation_raw"
PAGE_SIZE = 500
MAX_ROWS = 10000
SHANGHAI = ZoneInfo("Asia/Shanghai")


def _number(value: object, *, required_positive: bool = False) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError("invalid boolean valuation number")
    try:
        number = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError("invalid valuation number") from exc
    if not number.is_finite() or (required_positive and number <= 0):
        raise ValueError("non-finite or nonpositive valuation number")
    return number


class PriceValueHistoricalCollectionService:
    def __init__(
        self,
        *,
        source: EastmoneyDataSource | None = None,
        raw_repository: RawDailyBarImportRepository | None = None,
    ) -> None:
        self.source = source or EastmoneyDataSource()
        self.raw_repository = raw_repository or RawDailyBarImportRepository()

    def _fail_batch(self, db: Session, job, exc: Exception) -> None:
        if job.batch_id is None:
            return
        batch = db.get(ImportBatch, job.batch_id)
        if batch is None or batch.status != "running":
            return
        received = job.rows_read or 0
        self.raw_repository.finish_batch(
            db,
            batch,
            status="failed",
            row_count=received,
            accepted_count=0,
            rejected_count=received,
            error_message=f"{type(exc).__name__}: {exc}",
        )

    def collect(self, db: Session, *, trade_date: date) -> dict:
        if trade_date >= datetime.now(SHANGHAI).date():
            raise ValueError("historical collection requires a past trade date")

        started_at = datetime.now(UTC).isoformat()
        with logged_job(
            db,
            job_name="price_value_historical_valuation_collect",
            job_type="import",
            on_failure=self._fail_batch,
            metadata={
                "target_trade_date": trade_date.isoformat(),
                "collection_mode": "historical_raw_only",
                "observed_at_utc": started_at,
                "pit_verified": False,
                "code_version": settings.app_code_version,
            },
        ) as job:
            batch = self.raw_repository.create_batch(
                db,
                batch_no=f"pv_hist_{trade_date:%Y%m%d}_{uuid4().hex[:12]}",
                source_name=self.source.name,
                source_type=self.source.source_type,
                dataset=DATASET,
                request_params={
                    "endpoint": "https://datacenter-web.eastmoney.com/api/data/v1/get",
                    "report_name": "RPT_VALUEANALYSIS_DET",
                    "filter": f"(TRADE_DATE='{trade_date.isoformat()}')",
                    "page_size": PAGE_SIZE,
                    "observed_at_utc": started_at,
                    "supplier_trade_date_verified": False,
                    "pit_verified": False,
                    "publication_scope": "raw_only",
                    "code_version": settings.app_code_version,
                },
            )
            db.commit()
            job.batch_id = batch.id
            db.commit()

            total: int | None = None
            pages: int | None = None
            received = 0
            page_hashes: list[str] = []
            symbols: set[str] = set()
            missing_pe = 0
            missing_pb = 0
            for page in range(1, MAX_ROWS // PAGE_SIZE + 2):
                response, raw_text = self.source.fetch_historical_valuation_page(
                    trade_date=trade_date, page=page, page_size=PAGE_SIZE
                )
                fetched_at = datetime.now(UTC).isoformat()
                page_hash = sha256(raw_text.encode("utf-8")).hexdigest()
                db.add(
                    RawVendorSnapshot(
                        batch_id=batch.id,
                        source_name=self.source.name,
                        dataset=DATASET,
                        snapshot_date=trade_date,
                        raw_payload={
                            "page": page,
                            "target_trade_date": trade_date.isoformat(),
                            "fetched_at_utc": fetched_at,
                            "endpoint": "RPT_VALUEANALYSIS_DET",
                            "response_json": raw_text,
                        },
                        row_hash=page_hash,
                    )
                )
                db.commit()  # Keep this evidence even if a later page fails.
                page_hashes.append(page_hash)

                result = response.get("result")
                if response.get("success") is not True or not isinstance(result, dict):
                    raise ValueError(f"supplier rejected historical valuation page {page}")
                page_total = result.get("count")
                page_count = result.get("pages")
                items = result.get("data")
                if (
                    not isinstance(page_total, int)
                    or isinstance(page_total, bool)
                    or not 1 <= page_total <= MAX_ROWS
                    or not isinstance(page_count, int)
                    or isinstance(page_count, bool)
                    or page_count != (page_total + PAGE_SIZE - 1) // PAGE_SIZE
                    or not isinstance(items, list)
                ):
                    raise ValueError("invalid historical valuation pagination metadata")
                if total is None:
                    total, pages = page_total, page_count
                elif (page_total, page_count) != (total, pages):
                    raise ValueError("historical valuation total changed during pagination")
                expected_rows = min(PAGE_SIZE, total - received)
                if page > pages or len(items) != expected_rows:
                    raise ValueError("incomplete historical valuation page")

                for item in items:
                    if not isinstance(item, dict):
                        raise ValueError("invalid historical valuation row")
                    raw_date = item.get("TRADE_DATE")
                    try:
                        item_date = datetime.fromisoformat(str(raw_date)).date()
                    except ValueError as exc:
                        raise ValueError("invalid supplier valuation trade date") from exc
                    if item_date != trade_date:
                        raise ValueError("supplier valuation row has wrong trade date")
                    code = item.get("SECURITY_CODE")
                    secucode = item.get("SECUCODE")
                    if (
                        not isinstance(code, str)
                        or len(code) != 6
                        or not code.isdigit()
                        or secucode != normalize_symbol(code)
                    ):
                        raise ValueError("invalid historical valuation security code")
                    if secucode in symbols:
                        raise ValueError("duplicate historical valuation security")
                    symbols.add(secucode)
                    _number(item.get("CLOSE_PRICE"), required_positive=True)
                    pe = _number(item.get("PE_TTM"))
                    pb = _number(item.get("PB_MRQ"))
                    missing_pe += pe is None
                    missing_pb += pb is None
                received += len(items)
                job.rows_read = received
                db.commit()  # Persist progress for a later-page failure record.
                if page == pages:
                    break

            if total is None or received != total or len(symbols) != total:
                raise ValueError("historical valuation collection is incomplete")

            self.raw_repository.finish_batch(
                db,
                batch,
                status="success",
                row_count=received,
                accepted_count=received,
                rejected_count=0,
            )
            job.rows_inserted = received
            job.metadata_ = {
                **(job.metadata_ or {}),
                "batch_id": batch.id,
                "batch_no": batch.batch_no,
                "supplier_total": total,
                "pages": pages,
                "raw_page_hashes": page_hashes,
                "missing_pe_ttm": missing_pe,
                "missing_pb_mrq": missing_pb,
                "supplier_trade_date_verified": True,
                "publication_scope": "raw_only",
            }
        return dict(job.metadata_ or {})
