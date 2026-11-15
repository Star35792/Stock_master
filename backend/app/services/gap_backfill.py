from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from hashlib import sha256
from uuid import uuid4

from sqlalchemy.orm import Session

from app.data_sources.fallback import FallbackDailyBarDataSource
from app.data_sources.hithink import HithinkFinanceDataSource
from app.repositories.market import (
    DailyBarRepository,
    SecurityRepository,
    SecurityStatusEventRepository,
)
from app.repositories.raw import RawDailyBarImportRepository
from app.services.hithink_collection import _to_raw_daily_bar


@dataclass(frozen=True)
class GapBackfillResult:
    batch_no: str | None
    expected_rows: int
    fetched_rows: int
    inserted_rows: int
    skipped_rows: int
    missing_rows: int = 0
    partial_rows: int = 0
    suspect_rows: int = 0
    status_explained_rows: int = 0
    lifecycle_unknown_rows: int = 0
    status_conflict_rows: int = 0
    unresolved_rows: int = 0
    unresolved_actionable_rows: int = 0
    skipped_unchanged_rows: int = 0
    skipped_duplicate_rows: int = 0
    ignored_in_scope_rows: int = 0
    rejected_rows: int = 0
    conflicting_response_rows: int = 0
    status: str = "success"
    source_error_count: int = 0


@dataclass(frozen=True)
class GapBackfillCandidate:
    symbol: str
    trade_date: date
    adjust_type: str
    security_type: str
    reason: str


@dataclass(frozen=True)
class GapBackfillPlan:
    candidates: tuple[GapBackfillCandidate, ...]
    missing_rows: int
    partial_rows: int
    suspect_rows: int
    status_explained_rows: int
    lifecycle_unknown_rows: int
    status_conflict_rows: int

    @property
    def unresolved_rows(self) -> int:
        return self.suspect_rows + self.lifecycle_unknown_rows + self.status_conflict_rows


class MissingDailyBarBackfillService:
    """Plan recent gaps and repair only missing or required-field-incomplete rows."""

    def __init__(
        self,
        *,
        primary_data_source: HithinkFinanceDataSource | None = None,
        data_source: FallbackDailyBarDataSource | None = None,
        daily_bar_repository: DailyBarRepository | None = None,
        security_repository: SecurityRepository | None = None,
        security_status_repository: SecurityStatusEventRepository | None = None,
        repository: RawDailyBarImportRepository | None = None,
    ) -> None:
        self.primary_data_source = primary_data_source or HithinkFinanceDataSource()
        self.data_source = data_source or FallbackDailyBarDataSource()
        self.daily_bar_repository = daily_bar_repository or DailyBarRepository()
        self.security_repository = security_repository or SecurityRepository()
        self.security_status_repository = (
            security_status_repository or SecurityStatusEventRepository()
        )
        self.repository = repository or RawDailyBarImportRepository()

    def plan(
        self,
        db: Session,
        *,
        symbols: list[str],
        start_date: date,
        end_date: date,
        adjust_type: str = "qfq",
        page_size: int = 1000,
    ) -> GapBackfillPlan:
        """Build a read-only, reasoned repair plan before downloading data."""
        if start_date > end_date:
            raise ValueError("start_date must be earlier than or equal to end_date")
        if page_size < 1:
            raise ValueError("page_size must be greater than or equal to 1")
        normalized = sorted({symbol.strip().upper() for symbol in symbols if symbol.strip()})
        rows = []
        for symbol in normalized:
            offset = 0
            while True:
                page, total = self.daily_bar_repository.list_repair_candidates_by_calendar(
                    db,
                    start_date=start_date,
                    end_date=end_date,
                    adjust_type=adjust_type,
                    symbol=symbol,
                    limit=page_size,
                    offset=offset,
                )
                rows.extend(page)
                offset += len(page)
                if not page or offset >= total:
                    break

        mapped_rows = [row._mapping if hasattr(row, "_mapping") else row for row in rows]
        missing_by_date: dict[date, set[str]] = {}
        for row in mapped_rows:
            if _row_value(row, "gap_reason") == "missing":
                missing_by_date.setdefault(_row_value(row, "trade_date"), set()).add(
                    _row_value(row, "symbol")
                )
        explicit_statuses: dict[tuple[str, date], str] = {}
        for trade_date, date_symbols in missing_by_date.items():
            statuses = self.security_status_repository.explicit_statuses_on_date(
                db, symbols=date_symbols, trade_date=trade_date
            )
            explicit_statuses.update(
                {(symbol, trade_date): status for symbol, status in statuses.items()}
            )

        candidates: list[GapBackfillCandidate] = []
        missing_rows = partial_rows = suspect_rows = 0
        status_explained_rows = lifecycle_unknown_rows = status_conflict_rows = 0
        seen: set[tuple[str, date]] = set()
        for row in mapped_rows:
            symbol = _row_value(row, "symbol")
            trade_date = _row_value(row, "trade_date")
            key = (symbol, trade_date)
            if key in seen:
                continue
            seen.add(key)
            reason = _row_value(row, "gap_reason")
            if reason == "missing":
                missing_rows += 1
                if _row_value(row, "list_date") is None:
                    lifecycle_unknown_rows += 1
                    continue
                explicit_status = explicit_statuses.get(key)
                if explicit_status == "conflict":
                    status_conflict_rows += 1
                    continue
                if explicit_status in {"suspended", "unlisted", "delisted"}:
                    status_explained_rows += 1
                    continue
            elif reason == "required_field_missing":
                partial_rows += 1
            else:
                # Source conflicts and other suspect rows need diagnosis.  They
                # are visible in the plan but are not blindly downloaded.
                suspect_rows += 1
                continue
            candidates.append(
                GapBackfillCandidate(
                    symbol=symbol,
                    trade_date=trade_date,
                    adjust_type=_row_value(row, "adjust_type"),
                    security_type=_row_value(row, "security_type"),
                    reason=reason,
                )
            )
        return GapBackfillPlan(
            candidates=tuple(candidates),
            missing_rows=missing_rows,
            partial_rows=partial_rows,
            suspect_rows=suspect_rows,
            status_explained_rows=status_explained_rows,
            lifecycle_unknown_rows=lifecycle_unknown_rows,
            status_conflict_rows=status_conflict_rows,
        )

    def backfill(
        self,
        db: Session,
        *,
        symbols: list[str],
        start_date: date,
        end_date: date,
        adjust_type: str = "qfq",
        source_priority: int = 80,
    ) -> GapBackfillResult:
        if start_date > end_date:
            raise ValueError("start_date must be earlier than or equal to end_date")
        if adjust_type != "qfq":
            raise ValueError("fallback K-line backfill currently supports qfq only")
        normalized = sorted({symbol.strip().upper() for symbol in symbols if symbol.strip()})
        plan = self.plan(
            db,
            symbols=normalized,
            start_date=start_date,
            end_date=end_date,
            adjust_type=adjust_type,
        )
        expected_keys = {
            (row.symbol, row.trade_date, row.adjust_type): row for row in plan.candidates
        }
        if not expected_keys:
            return GapBackfillResult(
                None,
                0,
                0,
                0,
                0,
                missing_rows=plan.missing_rows,
                partial_rows=plan.partial_rows,
                suspect_rows=plan.suspect_rows,
                status_explained_rows=plan.status_explained_rows,
                lifecycle_unknown_rows=plan.lifecycle_unknown_rows,
                status_conflict_rows=plan.status_conflict_rows,
                unresolved_rows=plan.unresolved_rows,
                status="partial" if plan.unresolved_rows else "success",
            )

        batch_no = f"gap_backfill_{datetime.now(UTC):%Y%m%d%H%M%S}_{uuid4().hex[:8]}"
        batch = self.repository.create_batch(
            db,
            batch_no=batch_no,
            source_name=self.data_source.name,
            source_type=self.data_source.source_type,
            dataset="daily_bar_gap_backfill",
            request_params={
                "symbols": normalized,
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
                "adjust_type": adjust_type,
                "plan": {
                    "actionable_rows": len(expected_keys),
                    "missing_rows": plan.missing_rows,
                    "partial_rows": plan.partial_rows,
                    "suspect_rows": plan.suspect_rows,
                    "status_explained_rows": plan.status_explained_rows,
                    "lifecycle_unknown_rows": plan.lifecycle_unknown_rows,
                    "status_conflict_rows": plan.status_conflict_rows,
                },
            },
        )
        fetched = inserted = skipped = 0
        skipped_unchanged = skipped_duplicate = ignored_in_scope = rejected = 0
        conflicting_response = 0
        source_errors: list[str] = []
        observed_keys: set[tuple[str, date, str]] = set()
        conflicting_keys_counted: set[tuple[str, date, str]] = set()
        conflict_audit_by_key: dict[str, dict[str, object]] = {}
        try:
            for symbol in normalized:
                symbol_keys = {key for key in expected_keys if key[0] == symbol}
                if not symbol_keys:
                    continue
                primary_records = []
                try:
                    primary_records = self.primary_data_source.fetch_daily_bars(
                        symbol=symbol,
                        start_date=start_date,
                        end_date=end_date,
                        adjust_type=adjust_type,  # type: ignore[arg-type]
                    )
                except Exception as exc:
                    source_errors.append(f"{symbol} primary: {exc}")
                primary_conflicts = _conflicting_record_keys(primary_records)
                primary_by_key = {
                    (record.symbol, record.trade_date, record.adjust_type): record
                    for record in primary_records
                    if (record.symbol, record.trade_date, record.adjust_type)
                    not in primary_conflicts
                }
                remaining = symbol_keys - primary_by_key.keys()
                fallback_records = []
                if remaining:
                    try:
                        fallback_records = self.data_source.fetch_daily_bars(
                            symbol=symbol,
                            start_date=start_date,
                            end_date=end_date,
                            adjust_type=adjust_type,
                        )
                    except Exception as exc:
                        source_errors.append(f"{symbol} fallback: {exc}")
                fallback_conflicts = _conflicting_record_keys(fallback_records)
                blocked_conflict_keys = primary_conflicts | fallback_conflicts
                records = [
                    *((record, True) for record in primary_records),
                    *((record, False) for record in fallback_records),
                ]
                fallback_source_name = self.data_source.last_source_name or self.data_source.name
                for summary in _conflict_audit_summaries(
                    blocked_conflict_keys,
                    (
                        (self.primary_data_source.name, primary_records),
                        (fallback_source_name, fallback_records),
                    ),
                ):
                    audit_key = str(summary["key"])
                    existing_summary = conflict_audit_by_key.get(audit_key)
                    if existing_summary is None:
                        conflict_audit_by_key[audit_key] = summary
                    else:
                        existing_summary["sources"] = sorted(
                            set(existing_summary["sources"]) | set(summary["sources"])
                        )
                        existing_summary["variant_digests"] = sorted(
                            set(existing_summary["variant_digests"])
                            | set(summary["variant_digests"])
                        )
                fetched += len(records)
                seen_keys = set()
                for record, is_primary in records:
                    key = (record.symbol, record.trade_date, record.adjust_type)
                    if key in blocked_conflict_keys:
                        skipped += 1
                        if key not in conflicting_keys_counted:
                            conflicting_keys_counted.add(key)
                            rejected += 1
                            conflicting_response += 1
                        continue
                    if key in seen_keys:
                        skipped += 1
                        skipped_duplicate += 1
                        continue
                    seen_keys.add(key)
                    if (
                        record.symbol != symbol
                        or record.adjust_type != adjust_type
                        or not start_date <= record.trade_date <= end_date
                    ):
                        skipped += 1
                        rejected += 1
                        continue
                    expected_row = expected_keys.get(key)
                    if expected_row is None:
                        skipped += 1
                        ignored_in_scope += 1
                        continue
                    raw_row = _to_raw_daily_bar(
                        batch_id=batch.id,
                        source_name=(
                            self.primary_data_source.name
                            if is_primary
                            else fallback_source_name
                        ),
                        source_type=(
                            self.primary_data_source.source_type
                            if is_primary
                            else self.data_source.source_type
                        ),
                        source_priority=(
                            max(source_priority - 30, 0)
                            if is_primary
                            else source_priority
                        ),
                        record=record,
                        security_type=expected_row.security_type,
                    )
                    observed_keys.add(key)
                    if self.repository.add_daily_bar_if_changed(db, raw_row):
                        inserted += 1
                    else:
                        skipped += 1
                        skipped_unchanged += 1
            unresolved_actionable = len(expected_keys.keys() - observed_keys)
            conflict_audit = sorted(
                conflict_audit_by_key.values(), key=lambda item: str(item["key"])
            )
            batch.request_params = {
                **(batch.request_params or {}),
                "unresolved_actionable_rows": unresolved_actionable,
                "skipped_unchanged_count": skipped_unchanged,
                "skipped_duplicate_count": skipped_duplicate,
                "ignored_in_scope_count": ignored_in_scope,
                "rejected_out_of_scope_count": rejected - conflicting_response,
                "conflicting_response_count": conflicting_response,
                "conflicting_response_keys": conflict_audit[:100],
                "conflicting_response_keys_truncated": max(len(conflict_audit) - 100, 0),
            }
            batch_status = (
                "failed"
                if not observed_keys
                else "partial"
                if source_errors or unresolved_actionable or rejected
                else "success"
            )
            error_parts = [*source_errors]
            if unresolved_actionable:
                error_parts.append(f"{unresolved_actionable} planned rows were not returned")
            if rejected:
                out_of_scope = rejected - conflicting_response
                if out_of_scope:
                    error_parts.append(f"{out_of_scope} out-of-scope records were rejected")
                if conflicting_response:
                    error_parts.append(
                        f"{conflicting_response} conflicting response keys were rejected"
                    )
            self.repository.finish_batch(
                db,
                batch,
                status=batch_status,
                row_count=fetched,
                accepted_count=inserted,
                rejected_count=rejected,
                error_message="; ".join(error_parts) or None,
            )
            db.commit()
        except Exception:
            db.rollback()
            raise
        if not observed_keys:
            details = "; ".join(error_parts) or "all sources returned no rows"
            raise RuntimeError(f"unable to backfill missing K-lines for {normalized}: {details}")
        return GapBackfillResult(
            batch_no,
            len(expected_keys),
            fetched,
            inserted,
            skipped,
            missing_rows=plan.missing_rows,
            partial_rows=plan.partial_rows,
            suspect_rows=plan.suspect_rows,
            status_explained_rows=plan.status_explained_rows,
            lifecycle_unknown_rows=plan.lifecycle_unknown_rows,
            status_conflict_rows=plan.status_conflict_rows,
            unresolved_rows=plan.unresolved_rows + unresolved_actionable,
            unresolved_actionable_rows=unresolved_actionable,
            skipped_unchanged_rows=skipped_unchanged,
            skipped_duplicate_rows=skipped_duplicate,
            ignored_in_scope_rows=ignored_in_scope,
            rejected_rows=rejected,
            conflicting_response_rows=conflicting_response,
            status=batch_status,
            source_error_count=len(source_errors),
        )


def _row_value(row: object, key: str):
    if isinstance(row, dict):
        return row[key]
    try:
        return row[key]  # type: ignore[index]
    except (KeyError, TypeError):
        return getattr(row, key)


def _conflicting_record_keys(records: list[object]) -> set[tuple[str, date, str]]:
    """Return business keys carrying more than one distinct price/turnover payload."""
    signatures: dict[tuple[str, date, str], tuple[object, ...]] = {}
    conflicts: set[tuple[str, date, str]] = set()
    for record in records:
        key = (record.symbol, record.trade_date, record.adjust_type)
        signature = (
            record.open,
            record.high,
            record.low,
            record.close,
            record.volume,
            record.amount,
        )
        previous = signatures.setdefault(key, signature)
        if previous != signature:
            conflicts.add(key)
    return conflicts


def _conflict_audit_summaries(
    blocked_keys: set[tuple[str, date, str]],
    sources: tuple[tuple[str, list[object]], ...],
) -> list[dict[str, object]]:
    """Build bounded-payload audit summaries without persisting full vendor responses."""
    summaries = {
        key: {
            "key": f"{key[0]}|{key[1].isoformat()}|{key[2]}",
            "sources": set(),
            "variant_digests": set(),
        }
        for key in blocked_keys
    }
    for source_name, records in sources:
        for record in records:
            key = (record.symbol, record.trade_date, record.adjust_type)
            if key not in summaries:
                continue
            signature = (
                record.open,
                record.high,
                record.low,
                record.close,
                record.volume,
                record.amount,
            )
            digest_payload = "|".join("" if value is None else str(value) for value in signature)
            summaries[key]["sources"].add(source_name)
            summaries[key]["variant_digests"].add(
                sha256(digest_payload.encode("utf-8")).hexdigest()
            )
    return [
        {
            "key": summary["key"],
            "sources": sorted(summary["sources"]),
            "variant_digests": sorted(summary["variant_digests"]),
        }
        for summary in summaries.values()
    ]
