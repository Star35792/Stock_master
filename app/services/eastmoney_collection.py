from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from json import dumps
from uuid import uuid4

from sqlalchemy.orm import Session

from app.data_sources.eastmoney import EastmoneyDataSource
from app.models.raw import RawVendorSnapshot
from app.repositories.raw import RawDailyBarImportRepository
from app.services.hithink_alternative_collection import _upsert_alternative_rows


@dataclass(frozen=True)
class EastmoneyFlowCollectionResult:
    batch_no: str
    raw_snapshots: int
    normalized_rows: int


class EastmoneyFlowCollectionService:
    def __init__(
        self,
        *,
        data_source: EastmoneyDataSource | None = None,
        repository: RawDailyBarImportRepository | None = None,
    ) -> None:
        self.data_source = data_source or EastmoneyDataSource()
        self.repository = repository or RawDailyBarImportRepository()

    def collect_current(
        self,
        db: Session,
        *,
        limit: int = 100,
        market: str = "all",
    ) -> EastmoneyFlowCollectionResult:
        payloads = {
            "eastmoney_main_fund_rank": self.data_source.fetch_main_fund_rank(
                limit=limit, market=market
            ),
            "eastmoney_industry_fund_flow": self.data_source.fetch_sector_fund_flow(
                kind="industry", limit=limit
            ),
            "eastmoney_concept_fund_flow": self.data_source.fetch_sector_fund_flow(
                kind="concept", limit=limit
            ),
        }
        snapshot_date = date.today()
        batch_no = f"eastmoney_flow_{datetime.now(UTC):%Y%m%d%H%M%S}_{uuid4().hex[:8]}"
        batch = self.repository.create_batch(
            db,
            batch_no=batch_no,
            source_name=self.data_source.name,
            source_type=self.data_source.source_type,
            dataset="capital_flow",
            request_params={
                "limit": limit,
                "market": market,
                "snapshot_date": snapshot_date.isoformat(),
            },
        )
        normalized_rows = 0
        try:
            for dataset, items in payloads.items():
                payload = {
                    "timestamp": int(datetime.now(UTC).timestamp() * 1000),
                    "item": items,
                }
                db.add(
                    RawVendorSnapshot(
                        batch_id=batch.id,
                        source_name=self.data_source.name,
                        dataset=dataset,
                        snapshot_date=snapshot_date,
                        raw_payload=payload,
                        row_hash=sha256(
                            dumps(payload, sort_keys=True, default=str).encode()
                        ).hexdigest(),
                    )
                )
                normalized_rows += _upsert_alternative_rows(
                    db, _normalize_rows(dataset, items, snapshot_date, self.data_source.name)
                )
            self.repository.finish_batch(
                db,
                batch,
                status="success",
                row_count=len(payloads),
                accepted_count=len(payloads),
                rejected_count=0,
            )
            db.commit()
        except Exception:
            db.rollback()
            raise
        return EastmoneyFlowCollectionResult(
            batch_no=batch_no,
            raw_snapshots=len(payloads),
            normalized_rows=normalized_rows,
        )


def _normalize_rows(
    dataset: str,
    items: list[dict],
    snapshot_date: date,
    source_name: str,
) -> list[dict]:
    rows = []
    for rank, item in enumerate(items, 1):
        code = str(item.get("code") or "")
        item_key = code or str(item.get("name") or rank)
        rows.append(
            {
                "dataset": dataset,
                "snapshot_date": snapshot_date,
                "item_key": item_key,
                "symbol": _symbol(code),
                "rank": rank,
                "value": _money(item.get("main_net_inflow")),
                "attributes": item,
                "source_name": source_name,
            }
        )
    return rows


def _symbol(code: str) -> str | None:
    if not code or code.startswith("BK"):
        return None
    if code.startswith(("60", "68", "11", "13", "5")):
        return f"{code}.SH"
    if code.startswith(("8", "4")):
        return f"{code}.BJ"
    return f"{code}.SZ"


def _money(value: object) -> Decimal | None:
    if value in (None, ""):
        return None
    text = str(value).strip()
    multiplier = Decimal(1)
    if text.endswith("亿"):
        text, multiplier = text[:-1], Decimal(100000000)
    elif text.endswith("万"):
        text, multiplier = text[:-1], Decimal(10000)
    try:
        return Decimal(text) * multiplier
    except InvalidOperation:
        return None
