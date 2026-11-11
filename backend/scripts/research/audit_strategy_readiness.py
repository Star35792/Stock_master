"""Read-only preflight for the historical single-factor research strategy.

This reports missing evidence. It never upgrades a factor profile or runs a
backtest when corporate-action accounting and historical screens are absent.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import date
from pathlib import Path

from sqlalchemy import func, select

from app.database.session import SessionLocal
from app.models.market import (
    CorporateAction,
    Security,
    SecurityIndustryHistory,
    SecurityStatusEvent,
)
from app.services.factor_batch_profiler import FactorBatchProfilerService
from app.services.factor_pool_classifier import FactorPoolClassifierService


def audit(start_date: date, end_date: date) -> dict[str, object]:
    with SessionLocal() as db:
        profiler = FactorBatchProfilerService()
        classifier = FactorPoolClassifierService()
        qualifications: list[dict[str, object]] = []
        for version in ("v1-qfq", "v2-qfq-2y"):
            summaries, _ = profiler.summarize(
                db,
                factor_name="volume_stability_20",
                factor_version=version,
                limit=1,
            )
            if not summaries:
                qualifications.append(
                    {
                        "factor_version": version,
                        "pool": "frozen",
                        "reasons": ["exact-version profile missing"],
                    }
                )
                continue
            result = classifier.classify(summaries)[0]
            qualifications.append(
                {
                    "factor_version": version,
                    "pool": result.pool,
                    "reasons": result.reasons,
                    "trade_date_count": result.summary.trade_date_count,
                    "pit_status": result.summary.pit_status,
                    "evidence_eligibility": result.summary.evidence_eligibility,
                }
            )

        actions = list(
            db.scalars(
                select(CorporateAction).where(
                    CorporateAction.ex_date >= start_date,
                    CorporateAction.ex_date <= end_date,
                )
            )
        )
        zero_value_events = (
            db.scalar(
                select(func.count())
                .select_from(CorporateAction)
                .where(
                    func.coalesce(CorporateAction.cash_dividend, 0) == 0,
                    func.coalesce(CorporateAction.bonus_share_ratio, 0) == 0,
                    func.coalesce(CorporateAction.transfer_share_ratio, 0) == 0,
                    func.coalesce(CorporateAction.rights_issue_ratio, 0) == 0,
                    func.coalesce(CorporateAction.split_ratio, 0) == 0,
                )
            )
            or 0
        )
        status_events = db.scalar(select(func.count()).select_from(SecurityStatusEvent)) or 0
        industry_sources = Counter(db.scalars(select(SecurityIndustryHistory.source_name)))
        security_count = db.scalar(select(func.count()).select_from(Security)) or 0
        delisted_count = (
            db.scalar(
                select(func.count()).select_from(Security).where(Security.delist_date.is_not(None))
            )
            or 0
        )

    blockers = [
        "research_factor_version_v1_not_core",
        "selection_and_backtest_screens_not_identical",
        "corporate_action_event_accounting_not_implemented",
        "delisted_price_coverage_not_certified",
    ]
    if status_events == 0:
        blockers.append("historical_security_status_missing")
    if not industry_sources or all(
        source.startswith("current_label_backfill") for source in industry_sources
    ):
        blockers.append("historical_industry_labels_not_point_in_time")
    if any(action.record_date is None or action.payment_date is None for action in actions):
        blockers.append("corporate_action_entitlement_or_payment_date_missing")
    if zero_value_events:
        blockers.append("existing_zero_value_corporate_actions_require_audit")
    return {
        "holdout_window": [start_date.isoformat(), end_date.isoformat()],
        "factor_qualifications": qualifications,
        "corporate_actions": {
            "count": len(actions),
            "zero_value_events_in_database": zero_value_events,
            "by_type": dict(sorted(Counter(action.action_type for action in actions).items())),
            "missing_record_date": sum(action.record_date is None for action in actions),
            "missing_payment_date": sum(action.payment_date is None for action in actions),
            "missing_source_event_id": sum(action.source_event_id is None for action in actions),
        },
        "historical_status_event_count": status_events,
        "industry_history_sources": dict(sorted(industry_sources.items())),
        "security_count": security_count,
        "securities_with_delist_date": delisted_count,
        "independent_backtest_status": "blocked",
        "blockers": blockers,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start-date", type=date.fromisoformat, default=date(2026, 5, 6))
    parser.add_argument("--end-date", type=date.fromisoformat, default=date(2026, 10, 19))
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    if args.start_date > args.end_date:
        parser.error("start-date must not exceed end-date")
    payload = audit(args.start_date, args.end_date)
    output = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(output, encoding="utf-8")
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
