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
from app.models.fundamental import FinancialIndicator, FinancialStatement
from app.models.raw import RawFinancialPayload
from app.repositories.raw import RawDailyBarImportRepository

_SHANGHAI = ZoneInfo("Asia/Shanghai")
_COMMON_STATEMENT_FIELDS = {
    "thscode",
    "ticker",
    "period",
    "fiscal_year",
    "fiscal_period",
    "report_date_ms",
    "period_end_ms",
    "currency",
}


@dataclass(frozen=True)
class FinancialCollectionResult:
    batch_no: str
    statement_rows: int
    indicator_rows: int
    raw_rows: int


class HithinkFinancialCollectionService:
    def __init__(
        self,
        *,
        data_source: HithinkFinanceDataSource | None = None,
        repository: RawDailyBarImportRepository | None = None,
    ) -> None:
        self.data_source = data_source or HithinkFinanceDataSource()
        self.repository = repository or RawDailyBarImportRepository()

    def collect_symbol(
        self,
        db: Session,
        *,
        symbol: str,
        period: str = "annual",
        limit: int = 10,
        indicator_reports: list[str] | None = None,
    ) -> FinancialCollectionResult:
        normalized_symbol = symbol.strip().upper()
        batch_no = f"hithink_financial_{datetime.now(UTC):%Y%m%d%H%M%S}_{uuid4().hex[:8]}"
        batch = self.repository.create_batch(
            db,
            batch_no=batch_no,
            source_name=self.data_source.name,
            source_type=self.data_source.source_type,
            dataset="financial",
            request_params={
                "symbol": normalized_symbol,
                "period": period,
                "limit": limit,
                "indicator_reports": indicator_reports or [],
            },
        )
        statement_rows = 0
        indicator_rows = 0
        raw_rows = 0
        try:
            for statement_type in ("income", "balance", "cash_flow"):
                items = self.data_source.fetch_financial_statements(
                    symbol=normalized_symbol,
                    statement_type=statement_type,
                    period=period,
                    limit=limit,
                )
                for item in items:
                    db.add(_raw_statement(batch.id, self.data_source.name, statement_type, item))
                    _upsert_statement(db, self.data_source.name, statement_type, item)
                    statement_rows += 1
                    raw_rows += 1

            for report in indicator_reports or []:
                payload = self.data_source.fetch_financial_indicators(
                    symbol=normalized_symbol,
                    report=report,
                )
                db.add(_raw_indicators(batch.id, self.data_source.name, payload))
                raw_rows += 1
                indicator_rows += _upsert_indicators(db, self.data_source.name, payload)

            self.repository.finish_batch(
                db,
                batch,
                status="success",
                row_count=raw_rows,
                accepted_count=raw_rows,
                rejected_count=0,
            )
            db.commit()
        except Exception:
            db.rollback()
            raise
        return FinancialCollectionResult(
            batch_no=batch_no,
            statement_rows=statement_rows,
            indicator_rows=indicator_rows,
            raw_rows=raw_rows,
        )


def _raw_statement(
    batch_id: int, source_name: str, statement_type: str, item: dict
) -> RawFinancialPayload:
    payload = _json_payload(item)
    return RawFinancialPayload(
        batch_id=batch_id,
        source_name=source_name,
        symbol=str(item["thscode"]).upper(),
        dataset=statement_type,
        report_key=f"{item['fiscal_year']}-{item['fiscal_period']}",
        report_date=_ms_date(item.get("report_date_ms")),
        period_end=_ms_date(item.get("period_end_ms")),
        raw_payload=payload,
        row_hash=_payload_hash(payload),
    )


def _raw_indicators(batch_id: int, source_name: str, payload: dict) -> RawFinancialPayload:
    normalized = _json_payload(payload)
    return RawFinancialPayload(
        batch_id=batch_id,
        source_name=source_name,
        symbol=str(payload["thscode"]).upper(),
        dataset="indicators",
        report_key=str(payload["report"]),
        raw_payload=normalized,
        row_hash=_payload_hash(normalized),
    )


def _upsert_statement(
    db: Session, source_name: str, statement_type: str, item: dict
) -> None:
    row = {
        "symbol": str(item["thscode"]).upper(),
        "statement_type": statement_type,
        "period": str(item["period"]),
        "fiscal_year": int(item["fiscal_year"]),
        "fiscal_period": str(item["fiscal_period"]),
        "report_date": _required_ms_date(item.get("report_date_ms")),
        "period_end": _required_ms_date(item.get("period_end_ms")),
        "currency": str(item.get("currency") or "CNY"),
        "metrics": {
            key: _json_value(value)
            for key, value in item.items()
            if key not in _COMMON_STATEMENT_FIELDS
        },
        "source_name": source_name,
    }
    statement = insert(FinancialStatement).values(row)
    statement = statement.on_conflict_do_update(
        index_elements=[
            FinancialStatement.symbol,
            FinancialStatement.statement_type,
            FinancialStatement.period,
            FinancialStatement.fiscal_year,
            FinancialStatement.fiscal_period,
        ],
        set_={
            "report_date": statement.excluded.report_date,
            "period_end": statement.excluded.period_end,
            "currency": statement.excluded.currency,
            "metrics": statement.excluded.metrics,
            "source_name": statement.excluded.source_name,
        },
    )
    db.execute(statement)


def _upsert_indicators(db: Session, source_name: str, payload: dict) -> int:
    rows = []
    for ability in payload.get("abilities", []):
        for indicator in ability.get("indicators", []):
            rows.append(
                {
                    "symbol": str(payload["thscode"]).upper(),
                    "report": str(payload["report"]),
                    "ability": str(ability["ability"]),
                    "indicator_name": str(indicator["index_id"]),
                    "value": _decimal(indicator.get("value")),
                    "source_name": source_name,
                }
            )
    if not rows:
        return 0
    statement = insert(FinancialIndicator).values(rows)
    statement = statement.on_conflict_do_update(
        index_elements=[
            FinancialIndicator.symbol,
            FinancialIndicator.report,
            FinancialIndicator.ability,
            FinancialIndicator.indicator_name,
        ],
        set_={
            "value": statement.excluded.value,
            "source_name": statement.excluded.source_name,
        },
    )
    db.execute(statement)
    return len(rows)


def _ms_date(value: object) -> date | None:
    if value is None:
        return None
    return datetime.fromtimestamp(int(value) / 1000, tz=_SHANGHAI).date()


def _required_ms_date(value: object) -> date:
    result = _ms_date(value)
    if result is None:
        raise ValueError("financial statement date is required")
    return result


def _decimal(value: object) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError(f"invalid financial indicator value: {value}") from exc


def _json_payload(payload: dict) -> dict:
    return {key: _json_value(value) for key, value in payload.items()}


def _json_value(value: object) -> object:
    return str(value) if isinstance(value, Decimal) else value


def _payload_hash(payload: dict) -> str:
    return sha256(dumps(payload, sort_keys=True, default=str).encode("utf-8")).hexdigest()
