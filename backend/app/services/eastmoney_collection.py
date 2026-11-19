from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from uuid import uuid4
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.data_sources.eastmoney import EastmoneyDataSource
from app.repositories.raw import RawDailyBarImportRepository
from app.services.alternative_data_supply import EmptyDatasetError, check_dataset_supply
from app.services.hithink_alternative_collection import (
    _capture_snapshot,
    _fail_capture_batch,
    _upsert_alternative_rows,
)


@dataclass(frozen=True)
class EastmoneyFlowCollectionResult:
    batch_no: str
    raw_snapshots: int
    normalized_rows: int
    #: 归一化后没有任何行的 dataset。端点返回空集时**必须**出现在这里，
    #: 否则断供会伪装成一次成功采集。
    empty_datasets: tuple[str, ...] = ()
    new_raw_snapshots: int = 0
    reused_raw_snapshots: int = 0


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
        snapshot_date = datetime.now(ZoneInfo("Asia/Shanghai")).date()
        batch_no = f"eastmoney_flow_{datetime.now(UTC):%Y%m%d%H%M%S}_{uuid4().hex[:8]}"
        batch = self.repository.create_batch(
            db, batch_no=batch_no, source_name=self.data_source.name,
            source_type=self.data_source.source_type, dataset="capital_flow",
            request_params={
                "limit": limit, "market": market, "snapshot_date": snapshot_date.isoformat(),
            },
        )
        db.commit()
        fetchers = (
            ("eastmoney_main_fund_rank", lambda: self.data_source.fetch_main_fund_rank(
                limit=limit, market=market,
            )),
            ("eastmoney_industry_fund_flow", lambda: self.data_source.fetch_sector_fund_flow(
                kind="industry", limit=limit,
            )),
            ("eastmoney_concept_fund_flow", lambda: self.data_source.fetch_sector_fund_flow(
                kind="concept", limit=limit,
            )),
        )
        payloads: list[tuple[str, list[dict]]] = []
        raw_count = new_raw_count = normalized_rows = 0
        empty_datasets: list[str] = []
        current_dataset = "fetch"
        try:
            for dataset, fetch in fetchers:
                current_dataset = dataset
                items = fetch()
                new_raw_count += _capture_snapshot(
                    db, batch, self.data_source.name, dataset, snapshot_date, {"item": items},
                )
                raw_count += 1
                payloads.append((dataset, items))
            for dataset, items in payloads:
                current_dataset = dataset
                rows = _normalize_rows(dataset, items, snapshot_date, self.data_source.name)
                try:
                    check_dataset_supply(dataset, len(rows))
                except EmptyDatasetError:
                    empty_datasets.append(dataset)
                    continue
                normalized_rows += _upsert_alternative_rows(db, rows)
            self.repository.finish_batch(
                db, batch, status="partial" if empty_datasets else "success",
                row_count=raw_count, accepted_count=new_raw_count, rejected_count=0,
                error_message=(
                    f"empty datasets: {', '.join(sorted(empty_datasets))}"
                    if empty_datasets else None
                ),
            )
            db.commit()
        except Exception as exc:
            _fail_capture_batch(
                db, self.repository, batch, raw_count, new_raw_count, current_dataset, exc,
            )
            raise
        return EastmoneyFlowCollectionResult(
            batch_no=batch_no, raw_snapshots=raw_count, normalized_rows=normalized_rows,
            empty_datasets=tuple(sorted(empty_datasets)), new_raw_snapshots=new_raw_count,
            reused_raw_snapshots=raw_count - new_raw_count,
        )


def _normalize_rows(
    dataset: str,
    items: list[dict],
    snapshot_date: date,
    source_name: str,
) -> list[dict]:
    rows = []
    for rank, item in enumerate(items, 1):
        code = str(_first_present(item, "code", "f12") or "")
        item_key = code or str(_first_present(item, "name", "f14") or rank)
        rows.append(
            {
                "dataset": dataset,
                "snapshot_date": snapshot_date,
                "item_key": item_key,
                "symbol": _symbol(code),
                "rank": rank,
                "value": _money(_first_present(item, "main_net_inflow", "f62")),
                "attributes": item,
                "source_name": source_name,
            }
        )
    return rows


def _first_present(item: dict, *keys: str) -> object:
    for key in keys:
        value = item.get(key)
        if value not in (None, ""):
            return value
    return None


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
    text = str(value).strip().replace(",", "")
    if text in {"-", "--", "N/A", "n/a", "None"}:
        return None
    multiplier = Decimal(1)
    for suffix, unit_multiplier in (
        ("亿", Decimal(100000000)),
        ("ÒÚ", Decimal(100000000)),
        ("万", Decimal(10000)),
        ("Íò", Decimal(10000)),
    ):
        if text.endswith(suffix):
            text = text[: -len(suffix)]
            multiplier = unit_multiplier
            break
    try:
        return Decimal(text) * multiplier
    except InvalidOperation:
        return None
