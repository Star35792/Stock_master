from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from itertools import combinations

from sqlalchemy.orm import Session

from app.models.raw import RawDailyBar
from app.repositories.market import (
    DailyBarRepository,
    DailyBarSourceCompareRepository,
    SecurityRepository,
    TradingCalendarRepository,
)
from app.repositories.raw import RawDailyBarRepository
from app.repositories.system import DataQualityIssueRepository
from app.services.reconciliation_rules import compare_daily_bar_fields, compare_ohlc
from app.utils.trading_session import is_pre_close_capture, session_settle_at

#: A bar captured inside the session is never final, however clean its numbers
#: look, so it gets a fixed low score instead of the usual per-warning step-down.
INTRADAY_SNAPSHOT_SCORE = Decimal("40.00")


@dataclass(frozen=True)
class MarketBuildResult:
    candidates_count: int
    trusted_count: int
    rejected_count: int
    issue_count: int
    suspect_count: int = 0
    conflict_count: int = 0
    #: Bars captured before their own session settled.  They are written as
    #: ``suspect`` (so every trusted read path skips them) and a later
    #: post-close sync overwrites them; this counter makes the window visible.
    intraday_snapshot_count: int = 0
    intraday_snapshot_dates: tuple[date, ...] = ()


class DailyBarMarketBuildService:
    def __init__(
        self,
        *,
        raw_repository: RawDailyBarRepository | None = None,
        market_repository: DailyBarRepository | None = None,
        compare_repository: DailyBarSourceCompareRepository | None = None,
        security_repository: SecurityRepository | None = None,
        trading_calendar_repository: TradingCalendarRepository | None = None,
        issue_repository: DataQualityIssueRepository | None = None,
    ) -> None:
        self.raw_repository = raw_repository or RawDailyBarRepository()
        self.market_repository = market_repository or DailyBarRepository()
        self.compare_repository = compare_repository or DailyBarSourceCompareRepository()
        self.security_repository = security_repository or SecurityRepository()
        self.trading_calendar_repository = (
            trading_calendar_repository or TradingCalendarRepository()
        )
        self.issue_repository = issue_repository or DataQualityIssueRepository()

    def build_from_raw(
        self,
        db: Session,
        *,
        symbols: list[str] | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
        adjust_type: str = "none",
        source_name: str | None = None,
        commit: bool = True,
    ) -> MarketBuildResult:
        """Promote raw bars into ``market.daily_bar``.

        ``source_name`` selects **which source's rows are eligible**, not
        which sources are compared.  The comparison set always spans every
        source that has a row for the same key, because a cross-source
        reconciliation that only ever sees one source cannot detect anything.

        That distinction is the whole point: passing the collecting source's
        name used to narrow the comparison set to a single source, so
        ``market.daily_bar_source_compare`` stayed empty and every bar was
        published on a single vendor's word alone.
        """
        candidates = self.raw_repository.latest_candidates(
            db,
            symbols=symbols,
            start_date=start_date,
            end_date=end_date,
            adjust_type=adjust_type,
            source_name=source_name,
        )
        raw_groups = self.raw_repository.latest_candidates_by_source(
            db,
            symbols=symbols,
            start_date=start_date,
            end_date=end_date,
            adjust_type=adjust_type,
            source_name=None,
        )
        reconciliation = self._reconcile_sources(db, raw_groups)
        existing_symbols = self.security_repository.get_symbols(
            db, {candidate.symbol for candidate in candidates}
        )
        open_dates_by_exchange = {
            exchange: self.trading_calendar_repository.get_open_dates(
                db,
                exchange=exchange,
                dates={
                    candidate.trade_date
                    for candidate in candidates
                    if _exchange_from_symbol(candidate.symbol) == exchange
                },
            )
            for exchange in {"SH", "SZ", "BJ"}
        }

        trusted_count = 0
        suspect_count = 0
        rejected_count = 0
        issue_count = 0
        conflict_count = 0
        intraday_counts: dict[date, int] = {}
        for candidate in candidates:
            key = (candidate.symbol, candidate.trade_date, candidate.adjust_type)
            if key in reconciliation.conflict_issues:
                rejected_count += 1
                conflict_count += 1
                self.market_repository.mark_source_conflict(
                    db,
                    symbol=candidate.symbol,
                    trade_date=candidate.trade_date,
                    adjust_type=candidate.adjust_type,
                    source_count=reconciliation.source_counts.get(key, 1),
                )
                for issue in reconciliation.conflict_issues[key]:
                    self.issue_repository.create(db, **issue)
                    issue_count += 1
                continue

            assessment = _assess_candidate_quality(
                candidate,
                existing_symbols=existing_symbols,
                open_dates_by_exchange=open_dates_by_exchange,
            )
            if assessment.quality_status == "rejected":
                rejected_count += 1
                self.market_repository.mark_rejected(
                    db,
                    symbol=candidate.symbol,
                    trade_date=candidate.trade_date,
                    adjust_type=candidate.adjust_type,
                )
                for issue in assessment.issues:
                    self.issue_repository.create(db, **issue)
                    issue_count += 1
                continue

            for issue in assessment.issues:
                self.issue_repository.create(db, **issue)
                issue_count += 1

            self.market_repository.upsert_from_raw(
                db,
                raw_bar=candidate,
                quality_score=assessment.quality_score,
                quality_status=assessment.quality_status,
                is_suspect=assessment.is_suspect,
                source_count=reconciliation.source_counts.get(key, 1),
            )
            if assessment.quality_status == "trusted":
                trusted_count += 1
            else:
                suspect_count += 1
            if assessment.captured_pre_close:
                intraday_counts[candidate.trade_date] = (
                    intraday_counts.get(candidate.trade_date, 0) + 1
                )

        # Each affected bar already carries its own ``INTRADAY_SNAPSHOT`` issue;
        # this one row per session states the remedy, which a per-bar row buried
        # among thousands of identical descriptions cannot do.
        for trade_date, bar_count in sorted(intraday_counts.items()):
            self.issue_repository.create(
                db,
                dataset="daily_bar",
                trade_date=trade_date,
                issue_type="INTRADAY_SNAPSHOT_BATCH",
                severity="warning",
                description=(
                    f"{bar_count} bars for {trade_date} were captured before the "
                    "session settled; they are stored as suspect and stay out of "
                    "every trusted read path until a post-close sync replaces them"
                ),
                expected_value={"captured_at_or_after": session_settle_at(trade_date).isoformat()},
                actual_value={"bar_count": bar_count},
            )
            issue_count += 1

        if commit:
            db.commit()
        return MarketBuildResult(
            candidates_count=len(candidates),
            trusted_count=trusted_count,
            suspect_count=suspect_count,
            rejected_count=rejected_count,
            issue_count=issue_count,
            conflict_count=conflict_count,
            intraday_snapshot_count=sum(intraday_counts.values()),
            intraday_snapshot_dates=tuple(sorted(intraday_counts)),
        )

    def _reconcile_sources(
        self,
        db: Session,
        raw_groups: dict[tuple[str, date, str], list[RawDailyBar]],
    ) -> "_ReconciliationResult":
        conflict_issues: dict[tuple[str, date, str], list[dict]] = {}
        source_counts: dict[tuple[str, date, str], int] = {}

        for key, rows in raw_groups.items():
            sorted_rows = sorted(rows, key=lambda row: (row.source_name, row.id))

            # A bar captured inside its own session is an incomplete day, not a
            # second opinion.  It is already demoted to suspect on provenance
            # alone (see ``_assess_candidate_quality``), so letting it take
            # part in the reconciliation would let a half-day snapshot accuse
            # the settled bar of a conflict -- and the settled bar would then
            # be pushed out of every trusted read path as well.
            #
            # Measured on 2026-11-11: the only ``hithink_finance`` version for
            # that session was captured at 10:41 Shanghai time, so its close
            # was the price at that moment rather than the close.  Comparing it
            # against the settled dump produced 6,105 "conflicts" and left
            # 21 of 6,148 bars trusted.  The settled sources agreed with each
            # other on 5,573 symbols.
            settled_rows = [
                row
                for row in sorted_rows
                if not is_pre_close_capture(getattr(row, "imported_at", None), row.trade_date)
            ]
            # ``source_count`` is provenance for the selected market row and
            # stays schema-compatible at >= 1.  Extra opinions count only when
            # their latest observation is settled and actually participates in
            # reconciliation; an intraday snapshot is not a second opinion.
            source_counts[key] = max(
                1, len({row.source_name for row in settled_rows})
            )
            for left, right in combinations(settled_rows, 2):
                comparison = _compare_candidates(left, right)
                self.compare_repository.upsert_pair(db, **comparison)
                if comparison["is_conflict"]:
                    conflict_issues.setdefault(key, []).append(
                        _source_conflict_issue(left, right, comparison)
                    )

        return _ReconciliationResult(
            conflict_issues=conflict_issues,
            source_counts=source_counts,
        )


@dataclass(frozen=True)
class _ReconciliationResult:
    conflict_issues: dict[tuple[str, date, str], list[dict]]
    source_counts: dict[tuple[str, date, str], int]


@dataclass(frozen=True)
class _QualityAssessment:
    quality_score: Decimal
    quality_status: str
    is_suspect: bool
    issues: list[dict]
    captured_pre_close: bool = False


def _assess_candidate_quality(
    candidate: RawDailyBar,
    *,
    existing_symbols: set[str],
    open_dates_by_exchange: dict[str, set[date]],
) -> _QualityAssessment:
    hard_issues = _validate_candidate(
        candidate,
        existing_symbols=existing_symbols,
        open_dates_by_exchange=open_dates_by_exchange,
    )
    if hard_issues:
        return _QualityAssessment(
            quality_score=Decimal("0.00"),
            quality_status="rejected",
            is_suspect=True,
            issues=hard_issues,
        )

    warning_issues = _warning_issues(candidate)
    score = max(Decimal("0.00"), Decimal("100.00") - Decimal("20.00") * len(warning_issues))

    # A bar captured inside its own session is an incomplete day: its close is
    # the price at the capture moment and its volume is partial.  The numbers can
    # pass every sanity check and still be wrong for research, so this guard
    # demotes the bar on provenance alone.  ``suspect`` is reused deliberately --
    # the trusted read paths already exclude it, and a new ``quality_status``
    # value would need a ``market.daily_bar`` table rebuild (DuckDB 1.5 cannot
    # add or drop a CHECK constraint in place).
    captured_pre_close = is_pre_close_capture(
        getattr(candidate, "imported_at", None), candidate.trade_date
    )
    if captured_pre_close:
        warning_issues = warning_issues + [_intraday_snapshot_issue(candidate)]
        score = min(score, INTRADAY_SNAPSHOT_SCORE)

    return _QualityAssessment(
        quality_score=score,
        quality_status="suspect" if warning_issues else "trusted",
        is_suspect=bool(warning_issues),
        issues=warning_issues,
        captured_pre_close=captured_pre_close,
    )


def _intraday_snapshot_issue(candidate: RawDailyBar) -> dict:
    captured_at = getattr(candidate, "imported_at", None)
    return {
        "dataset": "daily_bar",
        "symbol": candidate.symbol,
        "trade_date": candidate.trade_date,
        "adjust_type": candidate.adjust_type,
        "source_name": candidate.source_name,
        "raw_id": candidate.id,
        "issue_type": "INTRADAY_SNAPSHOT",
        "severity": "warning",
        "description": (
            "bar was captured before the session settled, so close/volume cover "
            "only part of the trading day; re-sync after the close"
        ),
        "expected_value": {
            "captured_at_or_after": session_settle_at(candidate.trade_date).isoformat()
        },
        "actual_value": {"captured_at": captured_at.isoformat() if captured_at else None},
    }


def _validate_candidate(
    candidate: RawDailyBar,
    *,
    existing_symbols: set[str],
    open_dates_by_exchange: dict[str, set[date]],
) -> list[dict]:
    issues: list[dict] = []
    base = {
        "dataset": "daily_bar",
        "symbol": candidate.symbol,
        "trade_date": candidate.trade_date,
        "adjust_type": candidate.adjust_type,
        "source_name": candidate.source_name,
        "raw_id": candidate.id,
    }

    if candidate.symbol not in existing_symbols:
        issues.append(
            {
                **base,
                "issue_type": "SECURITY_NOT_FOUND",
                "severity": "error",
                "description": "security master data is missing",
            }
        )

    exchange = _exchange_from_symbol(candidate.symbol)
    if candidate.trade_date not in open_dates_by_exchange.get(exchange, set()):
        issues.append(
            {
                **base,
                "issue_type": "CALENDAR_MISMATCH",
                "severity": "error",
                "description": "trade_date is not an open trading day for the exchange",
                "expected_value": {"exchange": exchange, "is_open": True},
                "actual_value": {"trade_date": candidate.trade_date.isoformat()},
            }
        )

    if getattr(candidate, "is_suspended", False):
        issues.append(
            {
                **base,
                "issue_type": "SUSPENDED_BAR_REJECTED",
                "severity": "error",
                "description": (
                    "suspended-day raw bar must not enter market.daily_bar; "
                    "ingest suspension status into market.security_status_event instead"
                ),
            }
        )

    prices = {
        "open": candidate.open,
        "high": candidate.high,
        "low": candidate.low,
        "close": candidate.close,
    }
    if any(value is None or value <= 0 for value in prices.values()):
        issues.append(
            {
                **base,
                "issue_type": "PRICE_INVALID",
                "severity": "error",
                "description": "OHLC fields must be present and positive",
                "actual_value": {key: str(value) for key, value in prices.items()},
            }
        )
    elif not (
        candidate.high >= candidate.open
        and candidate.high >= candidate.close
        and candidate.high >= candidate.low
        and candidate.low <= candidate.open
        and candidate.low <= candidate.close
        and candidate.low <= candidate.high
    ):
        issues.append(
            {
                **base,
                "issue_type": "PRICE_INVALID",
                "severity": "error",
                "description": "OHLC high/low relationship is invalid",
                "actual_value": {key: str(value) for key, value in prices.items()},
            }
        )

    if candidate.volume is not None and candidate.volume < 0:
        issues.append(
            {
                **base,
                "issue_type": "VOLUME_INVALID",
                "severity": "error",
                "description": "volume must not be negative",
                "actual_value": {"volume": str(candidate.volume)},
            }
        )
    if candidate.amount is not None and candidate.amount < 0:
        issues.append(
            {
                **base,
                "issue_type": "AMOUNT_INVALID",
                "severity": "error",
                "description": "amount must not be negative",
                "actual_value": {"amount": str(candidate.amount)},
            }
        )

    return issues


def _warning_issues(candidate: RawDailyBar) -> list[dict]:
    issues: list[dict] = []
    base = {
        "dataset": "daily_bar",
        "symbol": candidate.symbol,
        "trade_date": candidate.trade_date,
        "adjust_type": candidate.adjust_type,
        "source_name": candidate.source_name,
        "raw_id": candidate.id,
    }

    pre_close = getattr(candidate, "pre_close", None)
    close = getattr(candidate, "close", None)
    change_amount = getattr(candidate, "change_amount", None)
    volume = getattr(candidate, "volume", None)
    amount = getattr(candidate, "amount", None)

    if pre_close and close and change_amount is not None:
        expected_change = close - pre_close
        if abs(expected_change - change_amount) > Decimal("0.01"):
            issues.append(
                {
                    **base,
                    "issue_type": "PRICE_CONTINUITY_WARNING",
                    "severity": "warning",
                    "description": "close - pre_close does not match change_amount",
                    "expected_value": {"change_amount": str(expected_change)},
                    "actual_value": {"change_amount": str(change_amount)},
                }
            )

    if pre_close and pre_close > 0 and close:
        pct_move = abs((close - pre_close) / pre_close)
        if pct_move > Decimal("0.30"):
            issues.append(
                {
                    **base,
                    "issue_type": "PRICE_MOVE_EXTREME",
                    "severity": "warning",
                    "description": "absolute close-to-pre-close move is unusually large",
                    "expected_value": {"abs_pct_move_lte": "0.30"},
                    "actual_value": {"abs_pct_move": str(pct_move)},
                }
            )

    if volume is None:
        issues.append(
            {
                **base,
                "issue_type": "VOLUME_MISSING",
                "severity": "warning",
                "description": "volume is missing",
            }
        )
    if amount is None:
        issues.append(
            {
                **base,
                "issue_type": "AMOUNT_MISSING",
                "severity": "warning",
                "description": "amount is missing",
            }
        )
    if volume == 0 and amount and amount > 0:
        issues.append(
            {
                **base,
                "issue_type": "TURNOVER_INCONSISTENT",
                "severity": "warning",
                "description": "amount is positive while volume is zero",
                "actual_value": {
                    "volume": str(volume),
                    "amount": str(amount),
                },
            }
        )
    if amount == 0 and volume and volume > 0:
        issues.append(
            {
                **base,
                "issue_type": "TURNOVER_INCONSISTENT",
                "severity": "warning",
                "description": "volume is positive while amount is zero",
                "actual_value": {
                    "volume": str(volume),
                    "amount": str(amount),
                },
            }
        )

    return issues


def _compare_candidates(left: RawDailyBar, right: RawDailyBar) -> dict:
    """比较同一标的同一交易日的两个来源。

    冲突判据来自 :mod:`app.services.reconciliation_rules` —— 那里固化了跨源
    对账的口径知识（舍入容差、成交量单位差归一、成交额优先）。这里只负责把
    规则应用到具体的两行上，并产出可落库的对比记录。
    """
    if left.id > right.id:
        left, right = right, left

    fields = compare_daily_bar_fields(
        left.close,
        right.close,
        left.volume,
        right.volume,
        left.amount,
        right.amount,
    )
    close_cmp = fields["close"]
    volume_cmp = fields["volume"]
    amount_cmp = fields["amount"]

    # OHLC 用与收盘价相同的可比性口径：成交额一致 ⇒ 均价一致 ⇒ 价格必须
    # 一致（只允许舍入容差）；成交额因份额折算不可比时改用相对容差。
    amount_comparable = not volume_cmp.is_unit_adjusted
    price_comparable = amount_comparable and not amount_cmp.is_conflict

    ohlc = compare_ohlc(
        {
            "open": getattr(left, "open", None),
            "high": getattr(left, "high", None),
            "low": getattr(left, "low", None),
            "close": getattr(left, "close", None),
        },
        {
            "open": getattr(right, "open", None),
            "high": getattr(right, "high", None),
            "low": getattr(right, "low", None),
            "close": getattr(right, "close", None),
        },
        comparable=price_comparable,
    )

    conflicts: list[str] = []
    if ohlc.is_conflict:
        conflicts.append(ohlc.note or "price fields differ")
    if amount_cmp.is_conflict:
        conflicts.append(amount_cmp.note or "amount differs")

    unit_adjusted = [cmp for cmp in fields.values() if cmp.is_unit_adjusted]

    if conflicts:
        conflict_reason = "; ".join(conflicts)
    elif unit_adjusted:
        conflict_reason = "; ".join(cmp.note or "unit differs" for cmp in unit_adjusted)
    else:
        conflict_reason = None

    return {
        "symbol": left.symbol,
        "trade_date": left.trade_date,
        "adjust_type": left.adjust_type,
        "source_a": left.source_name,
        "source_b": right.source_name,
        "raw_id_a": left.id,
        "raw_id_b": right.id,
        "close_diff": _diff(left.close, right.close),
        "close_diff_pct": close_cmp.raw_rel_diff,
        "volume_diff_pct": volume_cmp.raw_rel_diff,
        "amount_diff_pct": amount_cmp.raw_rel_diff,
        "is_conflict": bool(conflicts),
        "conflict_reason": conflict_reason,
    }


def _source_conflict_issue(left: RawDailyBar, right: RawDailyBar, comparison: dict) -> dict:
    return {
        "dataset": "daily_bar",
        "symbol": left.symbol,
        "trade_date": left.trade_date,
        "adjust_type": left.adjust_type,
        "source_name": f"{left.source_name},{right.source_name}",
        "raw_id": left.id,
        "issue_type": "SOURCE_CONFLICT",
        "severity": "error",
        "description": "daily bar price differs between sources",
        "expected_value": {
            left.source_name: _price_snapshot(left),
            right.source_name: _price_snapshot(right),
        },
        "actual_value": {
            "raw_id_a": left.id,
            "raw_id_b": right.id,
            "close_diff": str(comparison["close_diff"]),
            "conflict_reason": comparison["conflict_reason"],
        },
    }


def _price_snapshot(row: RawDailyBar) -> dict[str, str | None]:
    return {
        "open": _decimal_to_str(row.open),
        "high": _decimal_to_str(row.high),
        "low": _decimal_to_str(row.low),
        "close": _decimal_to_str(row.close),
    }


def _diff(left: Decimal | None, right: Decimal | None) -> Decimal | None:
    if left is None or right is None:
        return None
    return left - right


def _pct_diff(left: Decimal | None, right: Decimal | None) -> Decimal | None:
    if left is None or right in (None, Decimal("0")):
        return None
    return (left - right) / right


def _decimal_to_str(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return str(value)


def _exchange_from_symbol(symbol: str) -> str:
    if "." not in symbol:
        return "UNKNOWN"
    return symbol.rsplit(".", 1)[1]
