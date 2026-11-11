"""End-to-end run of the Tencent valuation collection for one trading day."""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.config.settings import settings  # noqa: E402
from app.database.session import engine  # noqa: E402
from app.services.tencent_valuation_collection import (  # noqa: E402
    TencentValuationCollectionService,
)


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    return date.fromisoformat(value)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Collect Tencent valuation observations into market.valuation_snapshot",
    )
    parser.add_argument("--trade-date", help="target trading date (YYYY-MM-DD)")
    parser.add_argument("--db", help="override the control-plane database path")
    args = parser.parse_args()

    target = _parse_date(args.trade_date)
    db_path = Path(args.db or settings.metadata_db_path)

    print("=== 腾讯估值采集 ===")
    print(f"目标交易日: {target or '最近已完成交易日(由服务判定)'}")
    print("注意: 该源只能描述最近一个已收盘交易日，其他日期会被拒绝")
    print(f"数据库: {db_path}")

    if args.db:
        # An explicit path means the caller wants a different database than
        # the one the session module bound at import time.
        from sqlalchemy import create_engine

        bind = create_engine(f"duckdb:///{db_path}")
    else:
        bind = engine

    with Session(bind) as db:
        result = TencentValuationCollectionService().run(db, trade_date=target)
    print("\n=== 结果 ===")
    print(f"请求代码数     : {result.requested}")
    print(f"收到报价       : {result.received}")
    print(f"通过闸门       : {result.accepted}")
    print(f"拒绝           : {result.rejected}")
    print(f"入库行数       : {result.inserted}")
    print(f"批次           : {result.batch_no} (id={result.batch_id})")
    if result.rejection_reasons:
        print(f"拒绝原因       : {result.rejection_reasons}")
    print("\n分所覆盖:")
    for exchange, counts in result.coverage_by_exchange.items():
        expected = counts.get("expected", 0)
        accepted = counts.get("accepted", 0)
        rate = accepted / expected if expected else 0.0
        print(f"  {exchange}: {accepted}/{expected} = {rate:.2%}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
