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
            db.execute(text(_DELETE_FACTORS_SQL), params)
            db.execute(text(_BUILD_FACTORS_SQL), params)
            db.execute(text(_BUILD_QFQ_BARS_SQL), params)
            factor_rows = int(db.scalar(text(_COUNT_FACTORS_SQL), params) or 0)
            qfq_bar_rows = int(db.scalar(text(_COUNT_QFQ_BARS_SQL), params) or 0)
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


_DELETE_FACTORS_SQL = """
DELETE FROM market.adjustment_factor
WHERE source_name = :source_name
  AND version = :version
  AND adjust_type = 'qfq'
"""

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
"""

_BUILD_QFQ_BARS_SQL = """
INSERT INTO market.daily_bar (
    symbol, security_type, trade_date, adjust_type,
    open, high, low, close, volume, amount, turnover_rate,
    source_name, source_count, quality_score, quality_status,
    is_suspect, selected_raw_id
)
SELECT
    d.symbol,
    d.security_type,
    d.trade_date,
    'qfq',
    d.open * f.factor,
    d.high * f.factor,
    d.low * f.factor,
    d.close * f.factor,
    d.volume,
    d.amount,
    d.turnover_rate,
    :source_name || '_qfq',
    d.source_count,
    d.quality_score,
    d.quality_status,
    d.is_suspect,
    d.selected_raw_id
FROM market.daily_bar d
JOIN market.adjustment_factor f
  ON f.symbol = d.symbol
 AND f.trade_date = d.trade_date
 AND f.adjust_type = 'qfq'
 AND f.source_name = :source_name
 AND f.version = :version
WHERE d.adjust_type = 'none'
  AND (
      CAST(:qfq_start_date AS date) IS NULL
      OR d.trade_date >= CAST(:qfq_start_date AS date)
  )
ON CONFLICT (symbol, trade_date, adjust_type) DO UPDATE SET
    security_type = EXCLUDED.security_type,
    open = EXCLUDED.open,
    high = EXCLUDED.high,
    low = EXCLUDED.low,
    close = EXCLUDED.close,
    volume = EXCLUDED.volume,
    amount = EXCLUDED.amount,
    turnover_rate = EXCLUDED.turnover_rate,
    source_name = EXCLUDED.source_name,
    source_count = EXCLUDED.source_count,
    quality_score = EXCLUDED.quality_score,
    quality_status = EXCLUDED.quality_status,
    is_suspect = EXCLUDED.is_suspect,
    selected_raw_id = EXCLUDED.selected_raw_id
"""

_COUNT_FACTORS_SQL = """
SELECT COUNT(*) FROM market.adjustment_factor
WHERE source_name = :source_name
  AND version = :version
  AND adjust_type = 'qfq'
"""

_COUNT_QFQ_BARS_SQL = """
SELECT COUNT(*) FROM market.daily_bar
WHERE adjust_type = 'qfq'
  AND source_name = :source_name || '_qfq'
  AND (
      CAST(:qfq_start_date AS date) IS NULL
      OR trade_date >= CAST(:qfq_start_date AS date)
  )
"""
