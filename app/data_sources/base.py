from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any, Literal

AdjustType = Literal["none", "qfq", "hfq"]


@dataclass(frozen=True)
class DailyBarRecord:
    symbol: str
    trade_date: date
    adjust_type: AdjustType
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal | None = None
    amount: Decimal | None = None
    raw_payload: dict[str, Any] | None = None
