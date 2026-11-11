"""Forward observation of immutable price/value research cohorts."""

import json
from collections import Counter
from datetime import date, datetime
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.config.settings import settings
from app.repositories.strategy.price_value_screen import PriceValueScreenRepository
from app.repositories.strategy.price_value_tracking import PriceValueTrackingRepository

SHANGHAI = ZoneInfo("Asia/Shanghai")
VERSION = "price_value_forward_observation_v1"
WINDOWS = (5, 20, 60)
ROUND_TRIP_COST_RATE = Decimal("0.001")
EXPECTED_SCREEN_VERSION = "relative_value_research_v7"
EXPECTED_SELECTION_VERSION = "value_research_shortlist_v2"


def build_cohort(report: dict) -> dict:
    rows = list(report.get("rows") or [])
    pools = {
        "shortlist": sorted(
            row["symbol"] for row in rows
            if (row.get("shortlist") or {}).get("selected") is True
        ),
        "qualified": sorted(
            row["symbol"] for row in rows
            if (row.get("shortlist") or {}).get("qualified") is True
        ),
        "research": sorted(
            row["symbol"] for row in rows
            if (row.get("research_diagnostics") or {}).get("review_eligible") is True
        ),
    }
    return {
        "version": VERSION,
        "report_status": report.get("status"),
        "global_reason_codes": list(report.get("global_reason_codes") or []),
        "snapshot_hash": report.get("snapshot_hash"),
        "screen_version": report.get("screen_version"),
        "selection_version": report.get("selection_version"),
        "target_trade_date": report.get("target_trade_date"),
        "observation_mode": report.get("observation_mode"),
        "analysis_at": report.get("analysis_at"),
        "generated_at": report.get("generated_at"),
        "rule_impact": (report.get("selection") or {}).get("rule_impact"),
        "pools": pools,
        "research_only": True,
        "pit_verified": False,
        "execution_eligible": False,
    }


def _formation_cutoff(cohort: dict) -> date:
    if cohort.get("observation_mode") == "current_research_catchup":
        value = datetime.fromisoformat(str(cohort.get("analysis_at")))
        if value.tzinfo is None:
            raise ValueError("catchup cohort analysis_at must include timezone")
        return value.astimezone(SHANGHAI).date()
    return date.fromisoformat(str(cohort["target_trade_date"]))


def _pool_window(
    members: list[str], *, entry_date: date, end_date: date,
    prices: dict[tuple[str, date], Decimal],
) -> dict:
    missing = Counter()
    returns: list[Decimal] = []
    for symbol in members:
        entry = prices.get((symbol, entry_date))
        end = prices.get((symbol, end_date))
        if entry is None or entry <= 0:
            missing["entry_unobservable"] += 1
        elif end is None or end <= 0:
            missing["endpoint_unobservable"] += 1
        else:
            returns.append(end / entry - Decimal(1))
    count = len(members)
    observed = len(returns)
    gross = sum(returns, Decimal(0)) / observed if observed else None
    return {
        "status": "complete" if count and observed == count else "incomplete",
        "member_count": count,
        "observed_count": observed,
        "missing_count": count - observed,
        "coverage_rate": str(Decimal(observed) / count) if count else None,
        "gross_return_observed": str(gross) if gross is not None else None,
        "net_return_observed": (
            str(gross - ROUND_TRIP_COST_RATE) if gross is not None else None
        ),
        "positive_member_rate": (
            str(Decimal(sum(value > 0 for value in returns)) / observed)
            if observed else None
        ),
        "maximum_member_loss": str(min(returns)) if returns else None,
        "missing_reason_counts": dict(sorted(missing.items())),
    }


class PriceValueForwardTrackingService:
    def __init__(
        self, *, output_dir: str | Path | None = None,
        screen_repository: PriceValueScreenRepository | None = None,
        tracking_repository: PriceValueTrackingRepository | None = None,
    ) -> None:
        self.output_dir = Path(output_dir or settings.price_value_screen_dir)
        self.screen_repository = screen_repository or PriceValueScreenRepository()
        self.tracking_repository = tracking_repository or PriceValueTrackingRepository()

    def _load_cohort(self, name: str | None) -> dict | None:
        if not name or Path(name).name != name:
            return None
        path = self.output_dir / name
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None

    def latest(self, db: Session) -> dict | None:
        job = self.screen_repository.latest_screen_job(db)
        if job is None:
            return None
        cohort = self._load_cohort((job.metadata_ or {}).get("cohort_name"))
        if cohort is None:
            return {
                "version": VERSION, "status": "blocked",
                "reason_codes": ["frozen_cohort_missing"], "pools": {},
                "research_only": True, "pit_verified": False,
                "execution_eligible": False,
            }
        if (cohort.get("screen_version") != EXPECTED_SCREEN_VERSION
                or cohort.get("selection_version") != EXPECTED_SELECTION_VERSION):
            return {
                **cohort, "status": "blocked",
                "reason_codes": ["frozen_cohort_version_unsupported"],
                "windows": [],
            }
        if (cohort.get("report_status") == "blocked"
                or cohort.get("global_reason_codes")):
            return {
                **cohort, "status": "blocked",
                "reason_codes": ["frozen_cohort_report_blocked"],
                "windows": [],
            }
        cutoff = _formation_cutoff(cohort)
        sessions = self.tracking_repository.common_open_dates(
            db, after=cutoff, limit=max(WINDOWS) + 1,
        )
        all_symbols = sorted({
            symbol for members in cohort["pools"].values() for symbol in members
        })
        if not sessions:
            return {
                **cohort, "status": "pending", "reason_codes": ["entry_session_pending"],
                "formation_cutoff": cutoff.isoformat(), "entry_date": None,
                "windows": [], "benchmark": "research_pool_equal_weight",
                "cost_policy": self._cost_policy(),
            }
        entry_date = sessions[0]
        available_end = sessions[-1]
        price_input = self.tracking_repository.qfq_closes(
            start_date=entry_date, end_date=available_end, symbols=all_symbols,
        )
        windows = []
        for length in WINDOWS:
            if len(sessions) <= length:
                windows.append({
                    "trading_days": length, "status": "pending", "end_date": None,
                    "reason_codes": ["window_not_complete"], "pools": {},
                })
                continue
            end_date = sessions[length]
            pools = {
                name: _pool_window(
                    members, entry_date=entry_date, end_date=end_date,
                    prices=price_input["rows"],
                )
                for name, members in cohort["pools"].items()
            }
            benchmark_return = pools["research"].get("gross_return_observed")
            if benchmark_return is not None:
                benchmark = Decimal(benchmark_return)
                for result in pools.values():
                    value = result.get("gross_return_observed")
                    result["excess_return_vs_research_pool"] = (
                        str(Decimal(value) - benchmark) if value is not None else None
                    )
            windows.append({
                "trading_days": length, "status": (
                    "complete" if all(p["status"] == "complete" for p in pools.values())
                    else "incomplete"
                ),
                "end_date": end_date.isoformat(), "reason_codes": [], "pools": pools,
            })
        stable = {
            "cohort": cohort["snapshot_hash"], "sessions": [d.isoformat() for d in sessions],
            "price_input_hash": price_input["input_hash"], "windows": windows,
            "contract": VERSION,
        }
        return {
            **cohort, "status": "observing" if any(
                window["status"] == "pending" for window in windows
            ) else "complete" if all(
                window["status"] == "complete" for window in windows
            ) else "incomplete",
            "reason_codes": [], "formation_cutoff": cutoff.isoformat(),
            "entry_date": entry_date.isoformat(), "windows": windows,
            "benchmark": "research_pool_equal_weight",
            "price_dataset": price_input["dataset"],
            "price_input_hash": price_input["input_hash"],
            "observation_hash": sha256(
                json.dumps(stable, ensure_ascii=False, sort_keys=True,
                           separators=(",", ":")).encode()
            ).hexdigest(),
            "cost_policy": self._cost_policy(),
        }

    @staticmethod
    def _cost_policy() -> dict:
        return {
            "version": "fixed_proportional_round_trip_v1",
            "round_trip_cost_rate": str(ROUND_TRIP_COST_RATE),
            "description": "双边佣金与滑点的固定比例研究假设；不含最低佣金、整手或容量。",
        }
