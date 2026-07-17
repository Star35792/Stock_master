from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation

from sqlalchemy.orm import Session

from app.repositories.market import DailyBarRepository, ManualCorrectionRepository

_DECIMAL_FIELDS = {
    "open",
    "high",
    "low",
    "close",
    "pre_close",
    "change_amount",
    "pct_change",
    "volume",
    "amount",
    "turnover_rate",
    "amplitude",
}
_REQUIRED_PRICE_FIELDS = {"open", "high", "low", "close"}
_NON_NEGATIVE_FIELDS = {"volume", "amount"}


@dataclass(frozen=True)
class ManualCorrectionResult:
    symbol: str
    trade_date: date
    adjust_type: str
    field_name: str
    old_value: str | None
    new_value: str
    quality_status: str


class ManualCorrectionService:
    def __init__(
        self,
        *,
        daily_bar_repository: DailyBarRepository | None = None,
        correction_repository: ManualCorrectionRepository | None = None,
    ) -> None:
        self.daily_bar_repository = daily_bar_repository or DailyBarRepository()
        self.correction_repository = correction_repository or ManualCorrectionRepository()

    def apply_daily_bar_correction(
        self,
        db: Session,
        *,
        symbol: str,
        trade_date: date,
        field_name: str,
        new_value: str,
        reason: str,
        corrected_by: str,
        adjust_type: str = "none",
    ) -> ManualCorrectionResult:
        normalized_symbol = symbol.strip().upper()
        normalized_field = field_name.strip()
        if normalized_field not in _DECIMAL_FIELDS:
            raise ValueError(f"field_name must be one of {', '.join(sorted(_DECIMAL_FIELDS))}")
        if not reason.strip():
            raise ValueError("reason must not be empty")
        if not corrected_by.strip():
            raise ValueError("corrected_by must not be empty")

        daily_bar = self.daily_bar_repository.get_by_key(
            db,
            symbol=normalized_symbol,
            trade_date=trade_date,
            adjust_type=adjust_type,
        )
        if daily_bar is None:
            raise ValueError("daily bar was not found")

        parsed_value = _parse_decimal(new_value, field_name=normalized_field)
        _validate_value(normalized_field, parsed_value)
        _validate_ohlc_after_update(daily_bar, normalized_field, parsed_value)

        old_value = _value_to_str(getattr(daily_bar, normalized_field))
        self.daily_bar_repository.apply_manual_correction(
            db,
            daily_bar=daily_bar,
            field_name=normalized_field,
            value=parsed_value,
        )
        self.correction_repository.create(
            db,
            dataset="daily_bar",
            symbol=normalized_symbol,
            trade_date=trade_date,
            adjust_type=adjust_type,
            field_name=normalized_field,
            old_value=old_value,
            new_value=str(parsed_value),
            reason=reason.strip(),
            corrected_by=corrected_by.strip(),
        )
        db.commit()
        return ManualCorrectionResult(
            symbol=normalized_symbol,
            trade_date=trade_date,
            adjust_type=adjust_type,
            field_name=normalized_field,
            old_value=old_value,
            new_value=str(parsed_value),
            quality_status=daily_bar.quality_status,
        )


def _parse_decimal(value: str, *, field_name: str) -> Decimal:
    if value.strip() == "":
        raise ValueError(f"{field_name} must not be empty")
    try:
        return Decimal(value.replace(",", "").strip())
    except InvalidOperation as exc:
        raise ValueError(f"{field_name} must be a decimal") from exc


def _validate_value(field_name: str, value: Decimal) -> None:
    if field_name in _REQUIRED_PRICE_FIELDS and value <= 0:
        raise ValueError(f"{field_name} must be greater than 0")
    if field_name in _NON_NEGATIVE_FIELDS and value < 0:
        raise ValueError(f"{field_name} must not be negative")


def _validate_ohlc_after_update(daily_bar: object, field_name: str, value: Decimal) -> None:
    if field_name not in _REQUIRED_PRICE_FIELDS:
        return
    prices = {
        "open": daily_bar.open,
        "high": daily_bar.high,
        "low": daily_bar.low,
        "close": daily_bar.close,
    }
    prices[field_name] = value
    if not (
        prices["high"] >= prices["open"]
        and prices["high"] >= prices["close"]
        and prices["high"] >= prices["low"]
        and prices["low"] <= prices["open"]
        and prices["low"] <= prices["close"]
        and prices["low"] <= prices["high"]
    ):
        raise ValueError("OHLC high/low relationship is invalid")


def _value_to_str(value: object) -> str | None:
    if value is None:
        return None
    return str(value)
