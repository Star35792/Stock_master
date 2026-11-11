from __future__ import annotations

import argparse
import json
from datetime import date
from decimal import Decimal

from app.database.session import SessionLocal
from app.services import FactorBatchProfilerService, FactorPoolClassifierService, FactorPoolPolicy


def _pool_sort_key(item: tuple) -> tuple:
    pool = item[1]
    order = {"core": 0, "observation": 1, "frozen": 2}
    return (order.get(pool, 3), item[0])


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Classify active factors into core/observation/frozen pools."
    )
    parser.add_argument("--start-date")
    parser.add_argument("--end-date")
    parser.add_argument("--min-trade-dates", type=int, default=200)
    parser.add_argument("--min-coverage-rate", type=Decimal, default=Decimal("0.70"))
    parser.add_argument("--min-rank-ic-1d", type=Decimal, default=Decimal("0.01"))
    parser.add_argument("--min-rank-ic-5d", type=Decimal, default=Decimal("0.01"))
    parser.add_argument("--min-rank-ic-20d", type=Decimal, default=Decimal("0.01"))
    parser.add_argument(
        "--min-long-short-forward-return-1d",
        type=Decimal,
        default=Decimal("0"),
        help="端点闸门：1 日多空价差必须严格大于该阈值（默认 0，即要求为正）",
    )
    parser.add_argument(
        "--output-format",
        default="text",
        choices=("text", "json"),
    )
    parser.add_argument(
        "--pool",
        help="Filter output to a single pool (core, observation, frozen).",
        choices=("core", "observation", "frozen"),
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    policy = FactorPoolPolicy(
        min_trade_dates=args.min_trade_dates,
        min_coverage_rate=args.min_coverage_rate,
        min_rank_ic_1d=args.min_rank_ic_1d,
        min_rank_ic_5d=args.min_rank_ic_5d,
        min_rank_ic_20d=args.min_rank_ic_20d,
        min_long_short_forward_return_1d=args.min_long_short_forward_return_1d,
    )

    start_date = date.fromisoformat(args.start_date) if args.start_date else None
    end_date = date.fromisoformat(args.end_date) if args.end_date else None

    with SessionLocal() as db:
        profiler = FactorBatchProfilerService()
        summaries, _ = profiler.summarize(
            db,
            start_date=start_date,
            end_date=end_date,
            limit=10000,
            offset=0,
        )
        classifier = FactorPoolClassifierService(policy=policy)
        classifications = classifier.classify(summaries)

    if args.pool:
        classifications = [c for c in classifications if c.pool == args.pool]

    if args.output_format == "json":
        output = [
            {
                "factor_name": c.factor_name,
                "factor_version": c.factor_version,
                "category": c.category,
                "pool": c.pool,
                "reasons": c.reasons,
                "summary": {
                    "trade_date_count": c.summary.trade_date_count,
                    "code_version": c.summary.code_version,
                    "code_version_count": c.summary.code_version_count,
                    "mean_coverage_rate": (
                        str(c.summary.mean_coverage_rate)
                        if c.summary.mean_coverage_rate is not None
                        else None
                    ),
                    "mean_rank_ic_1d": (
                        str(c.summary.mean_rank_ic_1d)
                        if c.summary.mean_rank_ic_1d is not None
                        else None
                    ),
                    "rank_ic_1d_observation_count": (
                        c.summary.rank_ic_1d_observation_count
                    ),
                    "mean_rank_ic_5d": (
                        str(c.summary.mean_rank_ic_5d)
                        if c.summary.mean_rank_ic_5d is not None
                        else None
                    ),
                    "rank_ic_5d_observation_count": (
                        c.summary.rank_ic_5d_observation_count
                    ),
                    "mean_rank_ic_20d": (
                        str(c.summary.mean_rank_ic_20d)
                        if c.summary.mean_rank_ic_20d is not None
                        else None
                    ),
                    "rank_ic_20d_observation_count": (
                        c.summary.rank_ic_20d_observation_count
                    ),
                    "mean_long_short_forward_return_1d": (
                        str(c.summary.mean_long_short_forward_return_1d)
                        if c.summary.mean_long_short_forward_return_1d is not None
                        else None
                    ),
                    "mean_long_short_forward_return_5d": (
                        str(c.summary.mean_long_short_forward_return_5d)
                        if c.summary.mean_long_short_forward_return_5d is not None
                        else None
                    ),
                    "mean_long_short_forward_return_20d": (
                        str(c.summary.mean_long_short_forward_return_20d)
                        if c.summary.mean_long_short_forward_return_20d is not None
                        else None
                    ),
                    "endpoint_observation_count": c.summary.endpoint_observation_count,
                    "pit_status": c.summary.pit_status,
                    "evidence_eligibility": c.summary.evidence_eligibility,
                    "evidence_eligibility_reason": c.summary.evidence_eligibility_reason,
                    "evidence_eligibility_count": (
                        c.summary.evidence_eligibility_count
                    ),
                    "evidence_identity_count": c.summary.evidence_identity_count,
                    "evidence_identity_valid": (
                        c.summary.has_consistent_evidence_identity
                    ),
                    "evidence_identity_release_tag": (
                        c.summary.evidence_identity_release_tag
                    ),
                    "trade_date_sha256": c.summary.trade_date_sha256,
                },
            }
            for c in classifications
        ]
        print(json.dumps(output, indent=2, ensure_ascii=False))
    else:
        grouped: dict[str, list[tuple[str, str, list[str]]]] = {
            "core": [],
            "observation": [],
            "frozen": [],
        }
        for c in classifications:
            grouped[c.pool].append((c.factor_name, c.factor_version, c.reasons))

        for pool in ("core", "observation", "frozen"):
            items = grouped.get(pool, [])
            print(f"\n[{pool}] {len(items)} factors")
            for name, version, reasons in sorted(items, key=_pool_sort_key):
                line = f"  {name}@{version}"
                if reasons:
                    line += f"  reasons: {'; '.join(reasons)}"
                print(line)

    frozen_count = sum(1 for c in classifications if c.pool == "frozen")
    return 1 if frozen_count > 0 else 0


if __name__ == "__main__":
    raise SystemExit(main())
