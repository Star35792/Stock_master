"""Capture a single vendor observation under the operator action's writer lock."""

import json
import logging
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.data_sources.tencent import TencentQuoteDataSource, to_symbol
from app.models.raw import RawVendorSnapshot
from app.repositories.market.security import SecurityRepository
from app.repositories.raw.daily_bar_import import RawDailyBarImportRepository
from app.repositories.raw.vendor_snapshot import RawVendorSnapshotRepository

DATASET = "tencent_quote_capture"
logger = logging.getLogger(__name__)


def _decimal(value: object) -> Decimal | None:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return number if number.is_finite() and number != 0 else None


def verify_quote_capture(
    db: Session, *, action_id: int, symbol: object, observation: object
) -> bool:
    if (
        not isinstance(observation, dict)
        or observation.get("observation_status") != "captured"
        or not isinstance(symbol, str)
        or not symbol
        or observation.get("symbol") != symbol
    ):
        return False
    batch_id = observation.get("batch_id")
    raw_id = observation.get("raw_snapshot_id")
    if type(batch_id) is not int or type(raw_id) is not int:
        return False
    batch, snapshot = RawVendorSnapshotRepository().get_capture_references(
        db, batch_id=batch_id, snapshot_id=raw_id
    )
    if batch is None or snapshot is None:
        return False
    params, payload = batch.request_params, snapshot.raw_payload
    return (
        isinstance(params, dict)
        and isinstance(payload, dict)
        and batch.status == "success"
        and batch.dataset == snapshot.dataset == DATASET
        and batch.source_name == snapshot.source_name == TencentQuoteDataSource.name
        and params.get("operator_action_id") == action_id
        and params.get("raw_snapshot_id") == raw_id
        and params.get("raw_batch_id") == snapshot.batch_id == observation.get("raw_batch_id")
        and params.get("symbol") == payload.get("symbol") == symbol
    )


class TencentQuoteCaptureService:
    def __init__(self, data_source: TencentQuoteDataSource | None = None) -> None:
        self.data_source = data_source or TencentQuoteDataSource()
        self.snapshots = RawVendorSnapshotRepository()
        self.batches = RawDailyBarImportRepository()

    def resolve_stock(self, db: Session, symbol: str) -> str | None:
        normalized = symbol if "." in symbol else to_symbol(symbol)
        if normalized is None:
            return None
        stock = SecurityRepository().get_by_symbol(db, symbol=normalized)
        if stock is None or stock.security_type != "stock" or not stock.is_active:
            return None
        return stock.symbol if stock.currency == "CNY" else None

    def capture(self, db: Session, *, action_id: int, symbol: str) -> tuple[dict, str]:
        code, exchange = symbol.split(".")
        vendor_code = exchange.lower() + code
        source_url = f"https://qt.gtimg.cn/q={vendor_code}"
        batch = self.batches.create_batch(
            db,
            batch_no=f"tencent-quote-action-{action_id}",
            source_name=self.data_source.name,
            source_type=self.data_source.source_type,
            dataset=DATASET,
            request_params={
                "symbol": symbol,
                "source_url": source_url,
                "operator_action_id": action_id,
            },
        )
        try:
            quote = self.data_source.fetch_quotes([vendor_code]).get(vendor_code)
            observation_status = "captured" if quote else "no_quote"
        except Exception as exc:
            logger.warning(
                "Tencent quote capture failed for action %s: %s", action_id, type(exc).__name__
            )
            quote, observation_status = None, "upstream_error"
        observed_at = datetime.now(UTC)
        result = {
            "symbol": symbol,
            "source_name": batch.source_name,
            "source_url": source_url,
            "observed_at": observed_at.isoformat(),
            "quote_time_verified": False,
        }
        if quote:
            price = _decimal(quote.get("price"))
            if quote.get("is_stale") is True:
                observation_status = "vendor_stale"
            elif price is None or price <= 0 or quote.get("is_stale") is not False:
                observation_status = "invalid_quote"
            else:
                result["price_cny"] = str(price)
                for field in ("pe_ttm", "pb"):
                    ratio = _decimal(quote.get(field))
                    result[field] = str(ratio) if ratio is not None else None
        status = {"captured": "success", "vendor_stale": "partial", "invalid_quote": "partial"}.get(
            observation_status, "failed"
        )
        result.update(batch_id=batch.id, observation_status=observation_status)
        inserted = False
        if quote:
            payload = {
                "symbol": symbol,
                "source_url": source_url,
                "quote": {
                    key: str(value) if isinstance(value, float) else value
                    for key, value in quote.items()
                },
            }
            encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, allow_nan=False)
            snapshot = self.snapshots.add_if_changed(
                db,
                RawVendorSnapshot(
                    batch_id=batch.id,
                    source_name=batch.source_name,
                    dataset=batch.dataset,
                    snapshot_date=observed_at.astimezone(ZoneInfo("Asia/Shanghai")).date(),
                    raw_payload=payload,
                    row_hash=sha256(encoded.encode()).hexdigest(),
                ),
            )
            inserted = snapshot.inserted
            result.update(raw_snapshot_id=snapshot.snapshot_id, raw_batch_id=snapshot.batch_id)
        self.batches.finish_batch(
            db,
            batch,
            status=status,
            row_count=int(bool(quote)),
            accepted_count=int(inserted),
            rejected_count=0,
            error_message=observation_status if status != "success" else None,
        )
        batch.finished_at = observed_at
        # The caller commits the batch, raw evidence and terminal action together.
        batch.request_params = {
            **(batch.request_params or {}),
            "raw_snapshot_id": result.get("raw_snapshot_id"),
            "raw_batch_id": result.get("raw_batch_id"),
        }
        return {"quote_observation": result}, status
