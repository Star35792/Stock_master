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


@dataclass(frozen=True)
class SecurityRecord:
    symbol: str
    raw_symbol: str | None
    name: str
    security_type: str
    exchange: str
    currency: str = "CNY"


@dataclass(frozen=True)
class TradingDayRecord:
    exchange: str
    trade_date: date
    is_open: bool = True
