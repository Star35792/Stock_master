from dataclasses import dataclass
from datetime import date

from sqlalchemy import text
from sqlalchemy.orm import Session


@dataclass(frozen=True)
class AdjustmentBuildResult:
    factor_rows: int
    qfq_bar_rows: int
    version: str
    release_id: str | None = None


class AdjustmentBuildService:
    def rebuild_qfq(
        self,
        db: Session,
        *,
        source_name: str = "hithink_finance_dump",
        version: str = "v1",
        qfq_start_date: date | None = None,
        release_id: str | None = None,
    ) -> AdjustmentBuildResult:
        if not source_name.strip():
            raise ValueError("source_name must not be empty")
        if not version.strip():
            raise ValueError("version must not be empty")

        params = {
            "source_name": source_name,
            "version": version,
            "qfq_start_date": qfq_start_date,
            "release_id": release_id,
        }
        try:
            # Use upsert instead of DELETE+INSERT. DuckDB does not make rows
            # deleted in the same transaction invisible to a subsequent INSERT
            # into the same unique index, so an upsert avoids the resulting
            # "duplicate key" errors while still refreshing every computed row.
            db.execute(text(_BUILD_FACTORS_SQL), params)
            factor_rows = int(db.scalar(text(_COUNT_FACTORS_SQL), params) or 0)
            # QFQ bars are no longer materialized into market.daily_bar.
            # The curated layer computes forward-adjusted prices on demand from
            # adjust_type='none' rows multiplied by these factors.
            qfq_bar_rows = 0
            db.commit()
        except Exception:
            db.rollback()
            raise
        return AdjustmentBuildResult(
            factor_rows=factor_rows,
            qfq_bar_rows=qfq_bar_rows,
            version=version,
            release_id=release_id,
        )


_BUILD_FACTORS_SQL = """
INSERT INTO market.adjustment_factor (
    symbol, trade_date, adjust_type, factor, source_name, version, release_id, quality_status
)
WITH effective_events AS (
    SELECT
        e.symbol,
        e.cash_dividend,
        e.bonus_share_ratio,
        e.rights_issue_ratio,
        e.rights_issue_price,
        next_bar.trade_date AS effective_date,
        prev_bar.close AS previous_close
    FROM market.corporate_action e
    JOIN LATERAL (
        SELECT d.trade_date
        FROM market.daily_bar d
        WHERE d.symbol = e.symbol
          AND d.adjust_type = 'none'
          AND d.trade_date >= e.ex_date
        ORDER BY d.trade_date
        LIMIT 1
    ) next_bar ON true
    JOIN LATERAL (
        SELECT d.close
        FROM market.daily_bar d
        WHERE d.symbol = e.symbol
          AND d.adjust_type = 'none'
          AND d.trade_date < next_bar.trade_date
        ORDER BY d.trade_date DESC
        LIMIT 1
    ) prev_bar ON true
    WHERE e.source_name = :source_name
), event_ratios AS (
    SELECT
        symbol,
        effective_date AS trade_date,
        (
            previous_close * (
                1
                + COALESCE(bonus_share_ratio, 0)
                + COALESCE(rights_issue_ratio, 0)
            )
        ) / NULLIF(
            previous_close
            - COALESCE(cash_dividend, 0)
            + COALESCE(rights_issue_ratio, 0) * COALESCE(rights_issue_price, 0),
            0
        ) AS ratio
    FROM effective_events
), ratio_per_day AS (
    SELECT symbol, trade_date, EXP(SUM(LN(ratio))) AS day_ratio
    FROM event_ratios
    WHERE ratio > 0
    GROUP BY symbol, trade_date
), cumulative AS (
    SELECT
        d.symbol,
        d.trade_date,
        EXP(SUM(LN(COALESCE(r.day_ratio, 1))) OVER (
            PARTITION BY d.symbol
            ORDER BY d.trade_date
            ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
        )) AS backward_factor
    FROM market.daily_bar d
    LEFT JOIN ratio_per_day r
      ON r.symbol = d.symbol AND r.trade_date = d.trade_date
    WHERE d.adjust_type = 'none'
      AND (
          CAST(:qfq_start_date AS date) IS NULL
          OR d.trade_date >= CAST(:qfq_start_date AS date)
      )
), normalized AS (
    SELECT
        symbol,
        trade_date,
        backward_factor,
        LAST_VALUE(backward_factor) OVER (
            PARTITION BY symbol
            ORDER BY trade_date
            ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING
        ) AS last_factor
    FROM cumulative
)
SELECT
    symbol,
    trade_date,
    'qfq',
    backward_factor / NULLIF(last_factor, 0),
    :source_name,
    :version,
    :release_id,
    'trusted'
FROM normalized
WHERE backward_factor > 0 AND last_factor > 0
ON CONFLICT (symbol, trade_date, adjust_type, source_name, version)
DO UPDATE SET
    factor = excluded.factor,
    release_id = excluded.release_id,
    quality_status = excluded.quality_status,
    updated_at = now()
"""

_COUNT_FACTORS_SQL = """
SELECT COUNT(*) FROM market.adjustment_factor
WHERE source_name = :source_name
  AND version = :version
  AND adjust_type = 'qfq'
"""
