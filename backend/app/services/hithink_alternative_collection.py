from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from json import dumps
from uuid import uuid4
from zoneinfo import ZoneInfo

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.data_sources import HithinkFinanceDataSource
from app.models.market import AlternativeDataSnapshot, IndexConstituentSnapshot
from app.models.raw import RawVendorSnapshot
from app.repositories.raw import RawDailyBarImportRepository
from app.services.alternative_data_supply import EmptyDatasetError, check_dataset_supply

_SHANGHAI = ZoneInfo("Asia/Shanghai")


@dataclass(frozen=True)
class AlternativeCollectionResult:
    batch_no: str
    raw_snapshots: int
    normalized_rows: int
    constituent_rows: int
    #: 归一化后没有任何行的 dataset。端点返回空集时**必须**出现在这里，
    #: 否则断供会伪装成一次成功采集。
    empty_datasets: tuple[str, ...] = ()


class HithinkAlternativeCollectionService:
    def __init__(
        self,
        *,
        data_source: HithinkFinanceDataSource | None = None,
        repository: RawDailyBarImportRepository | None = None,
    ) -> None:
        self.data_source = data_source or HithinkFinanceDataSource()
        self.repository = repository or RawDailyBarImportRepository()

    def collect_current(
        self,
        db: Session,
        *,
        index_symbols: list[str] | None = None,
    ) -> AlternativeCollectionResult:
        batch_no = f"hithink_alternative_{datetime.now(UTC):%Y%m%d%H%M%S}_{uuid4().hex[:8]}"
        batch = self.repository.create_batch(
            db,
            batch_no=batch_no,
            source_name=self.data_source.name,
            source_type=self.data_source.source_type,
            dataset="alternative_data",
            request_params={"index_symbols": index_symbols or []},
        )
        dragon_tiger = self.data_source.fetch_dragon_tiger_list()
        # 游资榜是**独立的一张榜**：端点用 ``board_type`` 选择返回哪张榜，
        # ``board_type="all"`` 的响应里 ``hot_money_items`` 恒为空数组。
        # 早先把这个数据集判成「端点该子集无内容」是误判——参数没传对而已。
        dragon_tiger_hot_money = self.data_source.fetch_dragon_tiger_list(board_type="hot_money")
        payloads = {
            "limit_up_pool": self.data_source.fetch_limit_up_pool(),
            # 炸板池：``failed_limit_up`` 数据集的来源。早先记成「未找到端点」
            # 是错的——端点一直在（/api/a-share/special-data/limit-break-pool），
            # 只是此前没有对着端点清单核对过。
            "failed_limit_up": self.data_source.fetch_limit_break_pool(),
            "limit_down_pool": self.data_source.fetch_limit_down_pool(),
            # 涨停统计是**聚合口径**（没有逐股 item），落成一条 ``market`` 汇总行：
            # 封板率 / 涨停数 / 炸板数 / 触及涨停数都在 attributes 里。
            # 它也是池类端点里唯一**回显 trade_date** 的，回填历史最省事。
            "limit_up_statistics": self.data_source.fetch_limit_up_statistics(),
            "limit_up_ladder": self.data_source.fetch_limit_up_ladder(),
            "anomaly": self.data_source.fetch_anomaly_list(),
            "hot_stock": self.data_source.fetch_hot_stock_list(),
            "skyrocket": self.data_source.fetch_skyrocket_list(),
            "dragon_tiger": dragon_tiger,
            "dragon_tiger_hot_money": {
                "timestamp": dragon_tiger_hot_money.get("timestamp"),
                "trade_date": dragon_tiger_hot_money.get("trade_date"),
                "item": _flatten_hot_money(dragon_tiger_hot_money.get("hot_money_items") or []),
            },
            **{
                f"index_catalog_{tag}": {
                    "timestamp": int(datetime.now(_SHANGHAI).timestamp() * 1000),
                    "item": self.data_source.fetch_ths_indices(tag=tag),
                }
                for tag in ("industry", "cn_concept", "region", "tszs")
            },
        }
        raw_count = 0
        normalized_count = 0
        constituent_count = 0
        empty_datasets: list[str] = []
        try:
            for dataset, payload in payloads.items():
                snapshot_date = _snapshot_date(payload)
                db.add(
                    _raw_snapshot(
                        batch.id,
                        self.data_source.name,
                        dataset,
                        snapshot_date,
                        payload,
                    )
                )
                raw_count += 1
                rows = _normalize_payload(dataset, snapshot_date, payload, self.data_source.name)
                try:
                    check_dataset_supply(dataset, len(rows))
                except EmptyDatasetError:
                    # 端点活着但返回空集：留痕、记账、但不把整个批次判成成功。
                    empty_datasets.append(dataset)
                    continue
                normalized_count += _upsert_alternative_rows(db, rows)

            for index_symbol in index_symbols or []:
                members = self.data_source.fetch_index_constituents(symbol=index_symbol)
                constituent_count += _upsert_constituents(
                    db,
                    snapshot_date=date.today(),
                    index_symbol=index_symbol.upper(),
                    members=members,
                    source_name=self.data_source.name,
                )

            self.repository.finish_batch(
                db,
                batch,
                status="partial" if empty_datasets else "success",
                row_count=raw_count,
                accepted_count=raw_count - len(empty_datasets),
                rejected_count=len(empty_datasets),
                error_message=(
                    f"empty datasets: {', '.join(sorted(empty_datasets))}"
                    if empty_datasets
                    else None
                ),
            )
            db.commit()
        except Exception:
            db.rollback()
            raise
        return AlternativeCollectionResult(
            batch_no=batch_no,
            raw_snapshots=raw_count,
            normalized_rows=normalized_count,
            constituent_rows=constituent_count,
            empty_datasets=tuple(sorted(empty_datasets)),
        )


def _normalize_payload(
    dataset: str, snapshot_date: date, payload: dict, source_name: str
) -> list[dict]:
    items = payload.get("item", [])
    if dataset == "dragon_tiger":
        items = payload.get("stock_items", [])
    if dataset == "limit_up_ladder":
        items = _flatten_ladder(items)
    if dataset == "limit_up_statistics":
        # 聚合口径：没有逐股明细，落成一条 market 汇总行，值取封板率。
        return [
            {
                "dataset": dataset,
                "snapshot_date": snapshot_date,
                "item_key": "market",
                "symbol": None,
                "rank": None,
                "value": _first_decimal(payload, "seal_rate"),
                "attributes": payload,
                "source_name": source_name,
            }
        ]
    rows = []
    for index, item in enumerate(items):
        symbol = item.get("thscode")
        item_date = _parse_item_date(item.get("date")) or snapshot_date
        item_key = str(symbol or item.get("name") or item.get("stock_name") or index)
        value = _first_decimal(
            item,
            "heat",
            "net_value",
            "seal_money",
            "continue_day_cnt",
        )
        if dataset == "dragon_tiger_hot_money":
            # 同一只股票可能同时出现在两个游资席位下（实测 2026-11-10 有 7 只）。
            # item_key 必须带上席位，否则 (dataset, snapshot_date, item_key)
            # 唯一键会让后一条覆盖前一条，席位归属就静默丢了。
            seat = item.get("hot_money_seat")
            if seat:
                item_key = f"{seat}|{item_key}"
            # 席位口径的净买额。端点的 ``net_value`` 在这张榜里是**该席位**
            # 的净买额，与总榜 ``dragon_tiger`` 的 ``net_value`` 不同口径。
            seat_value = _first_decimal(
                item,
                "hot_money_item_net_value",
                "hot_money_net_value",
            )
            if seat_value is not None:
                value = seat_value
        rows.append(
            {
                "dataset": dataset,
                "snapshot_date": item_date,
                "item_key": item_key,
                "symbol": str(symbol).upper() if symbol else None,
                "rank": _integer(item.get("rank") or item.get("hot_rank")),
                "value": value,
                "attributes": item,
                "source_name": source_name,
            }
        )
    return rows


def _flatten_hot_money(seats: list[dict]) -> list[dict]:
    """把「游资席位 → 个股」两层结构拍平成个股行。

    端点的游资榜是**按席位分组**的：每个席位一个对象，内含 ``rows``
    （该席位的个股明细）。把它当个股列表直接读会得到 0 行——这正是该
    数据集长期被误判为「端点无内容」的原因。

    席位名随行携带（``hot_money_seat``），归一化时用它区分 item_key。
    """
    flattened: list[dict] = []
    for seat in seats:
        seat_name = seat.get("name")
        seat_buying = seat.get("buying")
        for row in seat.get("rows") or []:
            flattened.append(
                {
                    **row,
                    "hot_money_seat": seat_name,
                    "hot_money_seat_buying": seat_buying,
                }
            )
    return flattened


def _flatten_ladder(items: list[dict]) -> list[dict]:
    flattened = []
    for day in items:
        for board, stocks in (day.get("boards") or {}).items():
            for stock in stocks:
                flattened.append({**stock, "date": day.get("date"), "board": board})
    return flattened


def _upsert_alternative_rows(db: Session, rows: list[dict]) -> int:
    if not rows:
        return 0
    rows = list(
        {(row["dataset"], row["snapshot_date"], row["item_key"]): row for row in rows}.values()
    )
    statement = insert(AlternativeDataSnapshot).values(rows)
    statement = statement.on_conflict_do_update(
        index_elements=[
            AlternativeDataSnapshot.dataset,
            AlternativeDataSnapshot.snapshot_date,
            AlternativeDataSnapshot.item_key,
        ],
        set_={
            "symbol": statement.excluded.symbol,
            "rank": statement.excluded.rank,
            "value": statement.excluded.value,
            "attributes": statement.excluded.attributes,
            "source_name": statement.excluded.source_name,
        },
    )
    db.execute(statement)
    return len(rows)


def _upsert_constituents(
    db: Session,
    *,
    snapshot_date: date,
    index_symbol: str,
    members: list[dict],
    source_name: str,
) -> int:
    rows = [
        {
            "snapshot_date": snapshot_date,
            "index_symbol": index_symbol,
            "member_symbol": str(item["thscode"]).upper(),
            "member_name": str(item.get("name") or item["thscode"]),
            "weight": _decimal(item.get("weight")),
            "source_name": source_name,
        }
        for item in members
    ]
    if not rows:
        return 0
    statement = insert(IndexConstituentSnapshot).values(rows)
    statement = statement.on_conflict_do_update(
        index_elements=[
            IndexConstituentSnapshot.snapshot_date,
            IndexConstituentSnapshot.index_symbol,
            IndexConstituentSnapshot.member_symbol,
        ],
        set_={
            "member_name": statement.excluded.member_name,
            "weight": statement.excluded.weight,
            "source_name": statement.excluded.source_name,
        },
    )
    db.execute(statement)
    return len(rows)


def _raw_snapshot(
    batch_id: int, source_name: str, dataset: str, snapshot_date: date, payload: dict
) -> RawVendorSnapshot:
    return RawVendorSnapshot(
        batch_id=batch_id,
        source_name=source_name,
        dataset=dataset,
        snapshot_date=snapshot_date,
        raw_payload=payload,
        row_hash=sha256(dumps(payload, sort_keys=True, default=str).encode()).hexdigest(),
    )


def _snapshot_date(payload: dict) -> date:
    if payload.get("trade_date"):
        return date.fromisoformat(str(payload["trade_date"]))
    timestamp = payload.get("timestamp") or payload.get("date_ms")
    if timestamp:
        return datetime.fromtimestamp(int(timestamp) / 1000, tz=_SHANGHAI).date()
    return date.today()


def _parse_item_date(value: object) -> date | None:
    if not value:
        return None
    raw = str(value)
    if len(raw) == 8 and raw.isdigit():
        return datetime.strptime(raw, "%Y%m%d").date()
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def _first_decimal(item: dict, *keys: str) -> Decimal | None:
    for key in keys:
        if item.get(key) is not None:
            return _decimal(item[key])
    return None


def _decimal(value: object) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return None


def _integer(value: object) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None
