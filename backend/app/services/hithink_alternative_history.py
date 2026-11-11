"""历史回填：把只有 1–2 个快照日的 alternative 数据集补成有历史序列。

为什么需要它
------------
六族因子的时点性认证已解除（见 ``app/factors/pit_audit_catalog.py``），但因子池
分级器仍然看不到它们——``factor.factor_profile`` 里一条六族因子的 IC 证据都没有。
根因不是认证，是**快照历史太短**：``market.alternative_data_snapshot`` 里除了
``limit_up_ladder``（端点自带历史序列），其余 dataset 只有 1–2 个快照日，
连 RankIC 都算不出来。

「等调度自己攒」是可行的，但要等两个月。端点本身**多数支持按日期回溯**——
把历史一次性补齐，今天就能算出第一批 IC 证据。

每个端点的历史参数都不一样（实测结论，不是推测）
--------------------------------------------------

=====================  ==============================  =============  =========
dataset                端点                            历史参数       回显日期
=====================  ==============================  =============  =========
hot_stock              hot-stock-list-history           date(ISO)     是
limit_up_pool          limit-up-pool                    date_ms(ms)   **否**
failed_limit_up        limit-break-pool                 date_ms(ms)   **否**
limit_down_pool        limit-down-pool                  date_ms(ms)   **否**
limit_up_statistics    limit-up-statistics              date(ISO)     是
dragon_tiger           dragon-tiger-list                date(ISO)     是
dragon_tiger_hot_money dragon-tiger-list?board_type=... date(ISO)     是
=====================  ==============================  =============  =========

两个必须记住的坑
---------------

1. **参数名用错会被静默忽略**，返回当期数据而不报错。实测
   ``limit-break-pool`` 传 ``date`` / ``trade_date`` 都返回**当期** 19 条，
   传 ``date_ms`` 才返回 2026-10-20 的 29 条；``limit-up-statistics`` 正好相反，
   要 ISO 字符串 ``date``、传 ``date_ms`` 会被忽略。这与龙虎榜 ``board_type``
   同一类：**端点不会告诉你参数没生效**。
2. **不回显日期的端点必须由调用方注入请求日期**。``limit-up-pool`` /
   ``limit-break-pool`` / ``limit-down-pool`` 的响应里没有日期字段，
   ``timestamp`` 是**响应时刻**（也就是回填当天）。不注入的话，快照会变成
   「回填当天」——把 2026-10-20 的涨停数据标成 2026-11-11，
   是**教科书级的前视污染**。本模块用 ``echoes_date`` 显式区分两类端点，
   不回显的一律注入 ``trade_date``。

龙虎榜的日期口径
----------------
龙虎榜是 **T 日交易、T+1 公布**。本模块把 ``snapshot_date`` 记成**交易日 T**
（与当期采集一致），由因子的消费规则（``snapshot_date < 目标交易日``）保证
最早在 T+1 取用——那正是公布日，不前视。改成记公布日会让历史序列与当期采集
的序列对不齐，反而制造口径分叉。
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.data_sources import HithinkFinanceDataSource
from app.models.market import TradingCalendar
from app.repositories.raw import RawDailyBarImportRepository
from app.services.alternative_data_supply import EmptyDatasetError, check_dataset_supply
from app.services.hithink_alternative_collection import (
    _flatten_hot_money,
    _normalize_payload,
    _raw_snapshot,
    _snapshot_date as _payload_snapshot_date,
    _upsert_alternative_rows,
)

#: 回填时默认覆盖的历史 dataset（含龙虎榜席位榜）。
DEFAULT_HISTORY_DATASETS: tuple[str, ...] = (
    "hot_stock",
    "limit_up_pool",
    "failed_limit_up",
    "limit_down_pool",
    "limit_up_statistics",
    "dragon_tiger",
    "dragon_tiger_hot_money",
)


@dataclass(frozen=True)
class AlternativeHistoryResult:
    """一次历史回填的结果。"""

    batch_no: str
    #: 实际处理的交易日数。
    trade_days: int
    raw_snapshots: int
    normalized_rows: int
    #: 归一化为空的 (dataset, 交易日) 组合。
    empty_datasets: tuple[str, ...] = ()
    #: dataset → 错误信息。单个 dataset 失败不拖垮其余。
    failures: dict[str, str] = field(default_factory=dict)

    @property
    def status(self) -> str:
        if self.failures:
            return "failed"
        if self.empty_datasets:
            return "partial"
        return "success"


def _to_ms(trade_date: date) -> int:
    """把日期转成 Asia/Shanghai 零点毫秒时间戳。

    端点的 ``date_ms`` 参数用的是**上海时区**的零点毫秒，不能用 UTC 或
    ``time.mktime``——差 8 小时会落到前一天。
    """
    from datetime import datetime, time as dt_time
    from zoneinfo import ZoneInfo

    shanghai = ZoneInfo("Asia/Shanghai")
    return int(datetime.combine(trade_date, dt_time.min, tzinfo=shanghai).timestamp() * 1000)


def _fetch_hot_stock(ds: HithinkFinanceDataSource, trade_date: date) -> dict:
    payload = ds.fetch_hot_stock_list_history(trade_date=trade_date)
    payload.setdefault("trade_date", trade_date.isoformat())
    return payload


def _fetch_limit_up_pool(ds: HithinkFinanceDataSource, trade_date: date) -> dict:
    payload = ds.fetch_limit_up_pool(date_ms=_to_ms(trade_date))
    # 该端点不回显请求日期，必须注入——否则快照日期会变成回填当天。
    payload["trade_date"] = trade_date.isoformat()
    return payload


def _fetch_failed_limit_up(ds: HithinkFinanceDataSource, trade_date: date) -> dict:
    payload = ds.fetch_limit_break_pool(date_ms=_to_ms(trade_date))
    payload["trade_date"] = trade_date.isoformat()
    return payload


def _fetch_limit_down_pool(ds: HithinkFinanceDataSource, trade_date: date) -> dict:
    payload = ds.fetch_limit_down_pool(date_ms=_to_ms(trade_date))
    payload["trade_date"] = trade_date.isoformat()
    return payload


def _fetch_limit_up_statistics(ds: HithinkFinanceDataSource, trade_date: date) -> dict:
    payload = ds.fetch_limit_up_statistics(date=trade_date.isoformat())
    payload.setdefault("trade_date", trade_date.isoformat())
    return payload


def _fetch_dragon_tiger(ds: HithinkFinanceDataSource, trade_date: date) -> dict:
    payload = ds.fetch_dragon_tiger_list(trade_date=trade_date)
    payload.setdefault("trade_date", trade_date.isoformat())
    return payload


def _fetch_dragon_tiger_hot_money(ds: HithinkFinanceDataSource, trade_date: date) -> dict:
    raw = ds.fetch_dragon_tiger_list(board_type="hot_money", trade_date=trade_date)
    return {
        "timestamp": raw.get("timestamp"),
        "trade_date": raw.get("trade_date") or trade_date.isoformat(),
        "item": _flatten_hot_money(raw.get("hot_money_items") or []),
    }


#: dataset → (抓取函数, 端点是否回显日期)
#:
#: ``echoes_date=False`` 的端点，抓取函数**必须**把请求日期写进 payload，
#: 否则 ``_snapshot_date`` 会取响应时刻（= 回填当天），把历史数据标成今天。
HISTORY_FETCHERS: dict[str, tuple[Callable[[HithinkFinanceDataSource, date], dict], bool]] = {
    "hot_stock": (_fetch_hot_stock, True),
    "limit_up_pool": (_fetch_limit_up_pool, False),
    "failed_limit_up": (_fetch_failed_limit_up, False),
    "limit_down_pool": (_fetch_limit_down_pool, False),
    "limit_up_statistics": (_fetch_limit_up_statistics, True),
    "dragon_tiger": (_fetch_dragon_tiger, True),
    "dragon_tiger_hot_money": (_fetch_dragon_tiger_hot_money, True),
}


def open_trade_dates(db: Session, start_date: date, end_date: date) -> list[date]:
    """返回区间内的交易日（升序）。

    用交易日历而不是自然日——非交易日去请求端点只会拿到空集，白跑一轮
    请求还会把限流额度烧掉。
    """
    if start_date > end_date:
        raise ValueError("start_date must be on or before end_date")
    rows = db.scalars(
        select(TradingCalendar.trade_date)
        .where(
            TradingCalendar.is_open.is_(True),
            TradingCalendar.trade_date >= start_date,
            TradingCalendar.trade_date <= end_date,
        )
        .distinct()
        .order_by(TradingCalendar.trade_date)
    )
    return list(rows)


class HithinkAlternativeHistoryService:
    """按交易日回溯补齐情绪族数据集的历史快照。"""

    def __init__(
        self,
        *,
        data_source: HithinkFinanceDataSource | None = None,
        repository: RawDailyBarImportRepository | None = None,
        request_interval_seconds: float = 0.35,
    ) -> None:
        self.data_source = data_source or HithinkFinanceDataSource()
        self.repository = repository or RawDailyBarImportRepository()
        self.request_interval_seconds = request_interval_seconds

    def collect_history(
        self,
        db: Session,
        *,
        start_date: date,
        end_date: date,
        datasets: tuple[str, ...] = DEFAULT_HISTORY_DATASETS,
    ) -> AlternativeHistoryResult:
        """回填 ``[start_date, end_date]`` 区间内各 dataset 的历史快照。

        幂等：落库走 ``ON CONFLICT (dataset, snapshot_date, item_key) DO UPDATE``，
        重复回填同一天不会产生重复行。

        单个 dataset 失败会被记进 ``failures`` 并**继续**跑其余 dataset——
        一个端点抽风不该让整轮回填白跑。
        """
        unknown = [name for name in datasets if name not in HISTORY_FETCHERS]
        if unknown:
            raise ValueError(f"unknown datasets: {', '.join(sorted(unknown))}")

        trade_days = open_trade_dates(db, start_date, end_date)
        batch_no = f"hithink_alternative_history_{end_date:%Y%m%d}_{uuid4().hex[:8]}"
        batch = self.repository.create_batch(
            db,
            batch_no=batch_no,
            source_name=self.data_source.name,
            source_type=self.data_source.source_type,
            dataset="alternative_data_history",
            request_params={
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
                "datasets": list(datasets),
            },
        )

        raw_count = 0
        normalized_count = 0
        empty_datasets: list[str] = []
        failures: dict[str, str] = {}

        for dataset in datasets:
            fetcher, _echoes = HISTORY_FETCHERS[dataset]
            for trade_date in trade_days:
                try:
                    payload = fetcher(self.data_source, trade_date)
                except Exception as exc:  # noqa: BLE001 - 单日失败不应中断整轮
                    failures[f"{dataset}@{trade_date.isoformat()}"] = f"{type(exc).__name__}: {exc}"
                    continue
                snapshot_date = _payload_snapshot_date(payload)
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
                    empty_datasets.append(f"{dataset}@{trade_date.isoformat()}")
                    continue
                normalized_count += _upsert_alternative_rows(db, rows)
                if self.request_interval_seconds > 0:
                    time.sleep(self.request_interval_seconds)

        status = "failed" if failures else ("partial" if empty_datasets else "success")
        self.repository.finish_batch(
            db,
            batch,
            status=status,
            row_count=raw_count,
            accepted_count=raw_count - len(empty_datasets),
            rejected_count=len(empty_datasets),
            error_message=(
                f"{len(failures)} failure(s); empty: {len(empty_datasets)}"
                if failures or empty_datasets
                else None
            ),
        )
        db.commit()
        return AlternativeHistoryResult(
            batch_no=batch_no,
            trade_days=len(trade_days),
            raw_snapshots=raw_count,
            normalized_rows=normalized_count,
            empty_datasets=tuple(sorted(set(empty_datasets))),
            failures=failures,
        )


__all__ = [
    "AlternativeHistoryResult",
    "DEFAULT_HISTORY_DATASETS",
    "HISTORY_FETCHERS",
    "HithinkAlternativeHistoryService",
    "open_trade_dates",
]
