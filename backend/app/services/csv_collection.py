from csv import DictReader
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from json import dumps
from pathlib import Path
from uuid import uuid4

from sqlalchemy.orm import Session

from app.models.raw import RawDailyBar
from app.repositories.raw import RawDailyBarImportRepository
from app.services.hithink_collection import DailyBarCollectionResult

_REQUIRED_FIELDS = {"symbol", "trade_date", "open", "high", "low", "close"}
_DEFAULT_ENCODINGS = ("utf-8-sig", "utf-8", "gb18030", "gbk")


@dataclass(frozen=True)
class CsvDailyBarRow:
    symbol: str
    raw_symbol: str
    security_type: str
    trade_date: date
    adjust_type: str
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    pre_close: Decimal | None
    change_amount: Decimal | None
    pct_change: Decimal | None
    volume: Decimal | None
    amount: Decimal | None
    turnover_rate: Decimal | None
    amplitude: Decimal | None
    raw_payload: dict[str, str | None]


class CsvDailyBarCollectionService:
    def __init__(self, repository: RawDailyBarImportRepository | None = None) -> None:
        self.repository = repository or RawDailyBarImportRepository()

    def collect_daily_bars(
        self,
        db: Session,
        *,
        file_path: str | Path,
        source_name: str = "csv",
        encoding: str | None = None,
        source_priority: int = 40,
    ) -> DailyBarCollectionResult:
        path = Path(file_path)
        rows, used_encoding = _read_csv_rows(path, encoding)
        batch_no = f"csv_daily_bar_{datetime.now(UTC):%Y%m%d%H%M%S}_{uuid4().hex[:8]}"
        batch = self.repository.create_batch(
            db,
            batch_no=batch_no,
            source_name=source_name,
            source_type="csv",
            dataset="daily_bar",
            file_path=str(path),
            request_params={"encoding": used_encoding},
        )

        accepted_count = 0
        errors: list[str] = []
        try:
            for line_no, row in rows:
                try:
                    parsed = _parse_csv_row(row)
                except ValueError as exc:
                    errors.append(f"line {line_no}: {exc}")
                    continue

                self.repository.add_daily_bar(
                    db,
                    _to_raw_daily_bar(
                        batch_id=batch.id,
                        source_name=source_name,
                        source_priority=source_priority,
                        row=parsed,
                    ),
                )
                accepted_count += 1

            rejected_count = len(errors)
            if errors and accepted_count:
                status = "partial"
            elif errors:
                status = "failed"
            else:
                status = "success"

            error_message = "; ".join(errors) if errors else None
            self.repository.finish_batch(
                db,
                batch,
                status=status,
                row_count=accepted_count + rejected_count,
                accepted_count=accepted_count,
                rejected_count=rejected_count,
                error_message=error_message,
            )
            db.commit()
        except Exception:
            db.rollback()
            raise

        return DailyBarCollectionResult(
            batch_no=batch_no,
            source_name=source_name,
            status=status,
            row_count=accepted_count + rejected_count,
            accepted_count=accepted_count,
            rejected_count=rejected_count,
            error_message=error_message,
        )


def _read_csv_rows(
    path: Path, encoding: str | None
) -> tuple[list[tuple[int, dict[str, str | None]]], str]:
    encodings = (encoding,) if encoding else _DEFAULT_ENCODINGS
    last_error: UnicodeDecodeError | None = None
    for candidate in encodings:
        try:
            with path.open(newline="", encoding=candidate) as file:
                reader = DictReader(file)
                if reader.fieldnames is None:
                    raise ValueError("CSV header is required")
                missing = _REQUIRED_FIELDS - set(reader.fieldnames)
                if missing:
                    raise ValueError(f"missing required columns: {', '.join(sorted(missing))}")
                return [(index, row) for index, row in enumerate(reader, start=2)], candidate
        except UnicodeDecodeError as exc:
            last_error = exc
            continue
    if last_error:
        raise last_error
    raise ValueError("CSV could not be read")


def _parse_csv_row(row: dict[str, str | None]) -> CsvDailyBarRow:
    raw_symbol = _required_text(row, "symbol")
    symbol = _normalize_symbol(raw_symbol)
    return CsvDailyBarRow(
        symbol=symbol,
        raw_symbol=raw_symbol,
        security_type=_optional_text(row, "security_type") or _infer_security_type(symbol),
        trade_date=_parse_date(_required_text(row, "trade_date")),
        adjust_type=_optional_text(row, "adjust_type") or "none",
        open=_required_decimal(row, "open"),
        high=_required_decimal(row, "high"),
        low=_required_decimal(row, "low"),
        close=_required_decimal(row, "close"),
        pre_close=_optional_decimal(row, "pre_close"),
        change_amount=_optional_decimal(row, "change_amount"),
        pct_change=_optional_decimal(row, "pct_change"),
        volume=_optional_decimal(row, "volume"),
        amount=_optional_decimal(row, "amount"),
        turnover_rate=_optional_decimal(row, "turnover_rate"),
        amplitude=_optional_decimal(row, "amplitude"),
        raw_payload=row,
    )


def _to_raw_daily_bar(
    *,
    batch_id: int,
    source_name: str,
    source_priority: int,
    row: CsvDailyBarRow,
) -> RawDailyBar:
    return RawDailyBar(
        batch_id=batch_id,
        source_name=source_name,
        source_type="csv",
        source_priority=source_priority,
        symbol=row.symbol,
        raw_symbol=row.raw_symbol,
        security_type=row.security_type,
        trade_date=row.trade_date,
        adjust_type=row.adjust_type,
        open=row.open,
        high=row.high,
        low=row.low,
        close=row.close,
        pre_close=row.pre_close,
        change_amount=row.change_amount,
        pct_change=row.pct_change,
        volume=row.volume,
        amount=row.amount,
        turnover_rate=row.turnover_rate,
        amplitude=row.amplitude,
        raw_payload=row.raw_payload,
        row_hash=_csv_daily_bar_row_hash(row),
    )


def _csv_daily_bar_row_hash(row: CsvDailyBarRow) -> str:
    payload = {
        "symbol": row.symbol,
        "trade_date": row.trade_date.isoformat(),
        "adjust_type": row.adjust_type,
        "open": str(row.open),
        "high": str(row.high),
        "low": str(row.low),
        "close": str(row.close),
        "pre_close": str(row.pre_close) if row.pre_close is not None else None,
        "volume": str(row.volume) if row.volume is not None else None,
        "amount": str(row.amount) if row.amount is not None else None,
    }
    return sha256(dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def _normalize_symbol(value: str) -> str:
    symbol = value.strip().upper()
    if "." in symbol:
        return symbol
    if len(symbol) == 6 and symbol.isdigit():
        if symbol.startswith(("60", "68")):
            return f"{symbol}.SH"
        if symbol.startswith(("00", "30", "15", "16", "18")):
            return f"{symbol}.SZ"
        if symbol.startswith(("83", "87", "92")):
            return f"{symbol}.BJ"
    raise ValueError(f"cannot infer exchange for symbol {value!r}")


def _infer_security_type(symbol: str) -> str:
    code = symbol.split(".", 1)[0]
    if code.startswith(("15", "16", "18", "51", "56", "58")):
        return "etf"
    if code.startswith(("00", "30", "60", "68", "83", "87", "92")):
        return "stock"
    return "unknown"


def _parse_date(value: str) -> date:
    text = value.strip()
    if len(text) == 8 and text.isdigit():
        return date.fromisoformat(f"{text[:4]}-{text[4:6]}-{text[6:]}")
    return date.fromisoformat(text)


def _required_decimal(row: dict[str, str | None], field: str) -> Decimal:
    text = _required_text(row, field)
    try:
        return Decimal(text.replace(",", ""))
    except InvalidOperation as exc:
        raise ValueError(f"{field} is not a valid decimal") from exc


def _optional_decimal(row: dict[str, str | None], field: str) -> Decimal | None:
    text = _optional_text(row, field)
    if text is None:
        return None
    try:
        return Decimal(text.replace(",", ""))
    except InvalidOperation as exc:
        raise ValueError(f"{field} is not a valid decimal") from exc


def _required_text(row: dict[str, str | None], field: str) -> str:
    text = _optional_text(row, field)
    if text is None:
        raise ValueError(f"{field} is required")
    return text


def _optional_text(row: dict[str, str | None], field: str) -> str | None:
    value = row.get(field)
    if value is None:
        return None
    text = value.strip()
    return text or None
