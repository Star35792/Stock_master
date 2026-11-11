"""把通达信官网盘后包采集为独立第二源。

为什么需要它
------------
``raw.daily_bar`` 里的三个来源全部同属同花顺，共享同一个上游 —— 它们之间
互相"对账"证明不了任何事：一个上游的系统性错误会被三个源同时复制。
通达信官网盘后包是另一家厂商的独立产物，是真正能交叉验证的对照源。

它作为**对照源**而不是**权威源**存在
------------------------------------
本服务写入的 ``source_priority`` 高于同花顺各源，因此
:func:`app.services.daily_bar_market_builder.DailyBarMarketBuildService.build_from_raw`
在选主源时仍以同花顺为准，通达信的行只参与跨源比对。这样做有两个理由：

1. 项目已经积累的历史数据全部来自同花顺，换主源会让历史与新数据之间出现
   口径断层；
2. 通达信的指数成交量是「手」而项目是「股」（实测 541 只指数差精确 100 倍、
   2 只差 10000 倍），选它当主源会把单位问题带进可信层。

对账本身不受此限制 —— 通达信的价值正在于作为外部标尺，把同花顺的数据
错误暴露出来。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import uuid4

from sqlalchemy.orm import Session

from app.data_sources.base import DailyBarRecord
from app.data_sources.tdx_official import TdxOfficialDataSource
from app.repositories.market import SecurityRepository
from app.repositories.raw import RawDailyBarImportRepository
from app.services.hithink_collection import _to_raw_daily_bar


@dataclass(frozen=True)
class TdxDailyBarCollectionResult:
    batch_no: str
    source_name: str
    status: str
    trade_date: date
    row_count: int
    accepted_count: int
    skipped_count: int
    error_message: str | None


class TdxDailyBarCollectionService:
    """采集某一交易日的通达信盘后包，写入 ``raw.daily_bar``。"""

    def __init__(
        self,
        *,
        data_source: TdxOfficialDataSource | None = None,
        repository: RawDailyBarImportRepository | None = None,
        security_repository: SecurityRepository | None = None,
        source_priority: int = 70,
    ) -> None:
        self.data_source = data_source or TdxOfficialDataSource()
        self.repository = repository or RawDailyBarImportRepository()
        self.security_repository = security_repository or SecurityRepository()
        # 70 高于同花顺各源（40/50），低于兜底补录（80）：通达信参与对账但不
        # 抢占主源位置。
        self.source_priority = source_priority

    def collect(
        self,
        db: Session,
        *,
        trade_date: date,
    ) -> TdxDailyBarCollectionResult:
        batch_no = f"tdx_daily_bar_{datetime.now(UTC):%Y%m%d%H%M%S}_{uuid4().hex[:8]}"
        package = self.data_source.fetch_package(trade_date)

        # 只保留项目证券主表里存在的标的。通达信独有的 999xxx 指数代码、基金
        # 与债券不在项目命名空间内，放进来只会制造"项目没有的标的"。
        project_symbols = {
            symbol for symbol in self.security_repository.get_active_symbols(db)
        }
        package_symbols = {str(row["symbol"]) for row in package.rows}
        allowed = project_symbols & package_symbols

        security_types = self.security_repository.get_security_types(db, allowed)
        records = self.data_source.to_daily_bar_records(
            package,
            allowed_symbols=allowed,
            security_types=security_types,
        )

        batch = self.repository.create_batch(
            db,
            batch_no=batch_no,
            source_name=self.data_source.name,
            source_type=self.data_source.source_type,
            dataset="daily_bar",
            request_params={
                "trade_date": trade_date.isoformat(),
                "package_rows": len(package.rows),
                "project_symbols_matched": len(allowed),
            },
        )

        accepted_count = 0
        skipped_count = 0
        try:
            for record in records:
                row = _to_raw_daily_bar(
                    batch_id=batch.id,
                    source_name=self.data_source.name,
                    source_type=self.data_source.source_type,
                    source_priority=self.source_priority,
                    record=record,
                    security_type=security_types.get(record.symbol),
                )
                if self.repository.add_daily_bar_if_changed(db, row):
                    accepted_count += 1
                else:
                    skipped_count += 1
            db.flush()

            self.repository.finish_batch(
                db,
                batch,
                status="success",
                row_count=len(records),
                accepted_count=accepted_count,
                rejected_count=0,
            )
            db.commit()
        except Exception as exc:
            db.rollback()
            self.repository.finish_batch(
                db,
                batch,
                status="failed",
                row_count=len(records),
                accepted_count=accepted_count,
                rejected_count=len(records) - accepted_count,
                error_message=str(exc),
            )
            db.commit()
            raise

        return TdxDailyBarCollectionResult(
            batch_no=batch_no,
            source_name=self.data_source.name,
            status="success",
            trade_date=trade_date,
            row_count=len(records),
            accepted_count=accepted_count,
            skipped_count=skipped_count,
            error_message=None,
        )


def _to_decimal_or_none(value: object) -> Decimal | None:
    if value is None:
        return None
    return Decimal(str(value))


__all__ = [
    "DailyBarRecord",
    "TdxDailyBarCollectionResult",
    "TdxDailyBarCollectionService",
]
