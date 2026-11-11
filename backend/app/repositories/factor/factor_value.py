from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models.factor import FactorValue


class FactorValueRepository:
    def list_for_cross_section(
        self,
        db: Session,
        *,
        factor_name: str,
        factor_version: str,
        start_date: date,
        end_date: date,
    ) -> list[FactorValue]:
        return list(
            db.scalars(
                select(FactorValue)
                .where(
                    FactorValue.factor_name == factor_name,
                    FactorValue.factor_version == factor_version,
                    FactorValue.trade_date >= start_date,
                    FactorValue.trade_date <= end_date,
                )
                .order_by(FactorValue.trade_date, FactorValue.symbol)
            )
        )

    def upsert_value(
        self,
        db: Session,
        *,
        symbol: str,
        trade_date: date,
        factor_name: str,
        factor_version: str,
        value: Decimal,
        run_id: int,
    ) -> None:
        row = {
            "symbol": symbol,
            "trade_date": trade_date,
            "factor_name": factor_name,
            "factor_version": factor_version,
            "value": value,
            "run_id": run_id,
        }
        statement = insert(FactorValue).values(row)
        statement = statement.on_conflict_do_update(
            index_elements=[
                FactorValue.symbol,
                FactorValue.trade_date,
                FactorValue.factor_name,
                FactorValue.factor_version,
            ],
            set_={
                "value": statement.excluded.value,
                "run_id": statement.excluded.run_id,
            },
        )
        db.execute(statement)

    def upsert_values(
        self,
        db: Session,
        rows: list[dict[str, Any]],
        *,
        batch_size: int = 5000,
    ) -> int:
        """批量写入因子值。

        为什么需要批量版本
        ------------------
        :meth:`upsert_value` 每次调用发一条单行 ``INSERT ... ON CONFLICT``。
        一个因子覆盖 20 个交易日 × 5,500 标的 ≈ 11 万行，11 个因子就是
        **167 万次单行 execute**——实测跑满 22 分钟仍未结束，整条因子链路
        实际不可用。批量写入把往返次数从「行数」降到「批次数」。

        行为与逐行版本一致：同一 ``(symbol, trade_date, factor_name,
        factor_version)`` 重复出现时保留最后一条（与逐行 upsert 的覆盖语义
        相同）。

        分批提交，避免单条语句的参数个数超过驱动上限。
        """

        if not rows:
            return 0
        deduped = {
            (
                row["symbol"],
                row["trade_date"],
                row["factor_name"],
                row["factor_version"],
            ): row
            for row in rows
        }
        payload = list(deduped.values())
        written = 0
        for start in range(0, len(payload), batch_size):
            chunk = payload[start : start + batch_size]
            statement = insert(FactorValue).values(chunk)
            statement = statement.on_conflict_do_update(
                index_elements=[
                    FactorValue.symbol,
                    FactorValue.trade_date,
                    FactorValue.factor_name,
                    FactorValue.factor_version,
                ],
                set_={
                    "value": statement.excluded.value,
                    "run_id": statement.excluded.run_id,
                },
            )
            db.execute(statement)
            written += len(chunk)
        return written

    def list(
        self,
        db: Session,
        *,
        factor_name: str | None = None,
        factor_version: str | None = None,
        symbol: str | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
        limit: int = 1000,
        offset: int = 0,
    ) -> tuple[list[FactorValue], int]:
        filters = []
        if factor_name:
            filters.append(FactorValue.factor_name == factor_name)
        if factor_version:
            filters.append(FactorValue.factor_version == factor_version)
        if symbol:
            filters.append(FactorValue.symbol == symbol)
        if start_date:
            filters.append(FactorValue.trade_date >= start_date)
        if end_date:
            filters.append(FactorValue.trade_date <= end_date)

        total = db.scalar(select(func.count()).select_from(FactorValue).where(*filters)) or 0
        items = list(
            db.scalars(
                select(FactorValue)
                .where(*filters)
                .order_by(FactorValue.trade_date, FactorValue.symbol, FactorValue.factor_name)
                .limit(limit)
                .offset(offset)
            )
        )
        return items, total
