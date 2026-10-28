"""Read-only current research inputs; never query raw or substitute factor proxies."""

from datetime import date
from typing import Any


class QualityValueDataRepository:
    def __init__(self, connection: Any):
        self.connection = connection

    def _rows(self, query: str, parameters: list | None = None) -> list[dict]:
        result = self.connection.execute(query, parameters or [])
        columns = [column[0] for column in result.description]
        return [dict(zip(columns, row, strict=True)) for row in result.fetchall()]

    def stocks(self) -> list[dict]:
        return self._rows("""
            SELECT symbol, name, exchange, industry, list_date, delist_date,
                   is_active, currency, source_name, updated_at
            FROM market.security WHERE security_type = 'stock' ORDER BY symbol
        """)

    def latest_trade_date(self, *, as_of: date) -> date | None:
        return self.connection.execute("""
            SELECT max(trade_date) FROM market.daily_bar
            WHERE trade_date <= ? AND adjust_type = 'none' AND security_type = 'stock'
              AND quality_status IN ('trusted', 'manual_fixed') AND NOT is_suspect
        """, [as_of]).fetchone()[0]

    def expected_completed_session(self, *, as_of: date) -> date | None:
        # At least the prior calendar day's fully scheduled session must exist.
        # Do not demand the current day's close before the vendor publishes it.
        return self.connection.execute("""
            SELECT max(trade_date) FROM (
                SELECT trade_date FROM market.trading_calendar
                WHERE trade_date < ? AND is_open AND exchange IN ('SH','SZ','BJ')
                GROUP BY trade_date HAVING count(DISTINCT exchange)=3
            )
        """, [as_of]).fetchone()[0]

    def bars(self, *, trade_date: date) -> list[dict]:
        return self._rows("""
            SELECT symbol, close, source_name, special_treatment_status, is_suspended,
                   quality_status, is_suspect, updated_at
            FROM market.daily_bar WHERE trade_date = ? AND adjust_type = 'none'
              AND security_type = 'stock'
        """, [trade_date])

    def liquidity(self, *, trade_date: date) -> list[dict]:
        # Require the same 20 sessions for every stock; missing sessions are not
        # silently replaced by older days or treated as zero-turnover sessions.
        return self._rows("""
            WITH sessions AS (
                SELECT trade_date FROM market.trading_calendar
                WHERE exchange='SH' AND is_open AND trade_date <= ?
                ORDER BY trade_date DESC LIMIT 20
            )
            SELECT symbol, count(*) AS sessions, sum(amount) AS amount_sum,
                   max(updated_at) AS updated_at
            FROM market.daily_bar WHERE trade_date IN (SELECT trade_date FROM sessions)
              AND adjust_type='none' AND security_type='stock'
              AND quality_status IN ('trusted','manual_fixed') AND NOT is_suspect
              AND NOT is_suspended AND amount IS NOT NULL AND amount >= 0
              AND source_name IS NOT NULL AND source_name <> ''
            GROUP BY symbol
        """, [trade_date])

    def valuation_coverage(self, *, as_of: date) -> list[dict]:
        return self._rows("""
            SELECT source_name, max(trade_date) AS latest_trade_date,
                   count(*) AS rows, count(DISTINCT symbol) AS symbols
            FROM market.valuation_snapshot WHERE trade_date <= ? GROUP BY source_name
        """, [as_of])

    def financial_coverage(self, *, as_of: date) -> list[dict]:
        return self._rows("""
            SELECT symbol, count(*) AS statement_rows, max(period_end) AS latest_period,
                   count(DISTINCT CASE WHEN month(period_end)=12 AND day(period_end)=31
                       AND year(period_end) BETWEEN ? AND ?
                       THEN year(period_end) END) AS annual_years,
                   count(DISTINCT CASE WHEN month(period_end)=12 AND day(period_end)=31
                       AND year(period_end) BETWEEN ? AND ?
                       THEN concat(year(period_end), ':', statement_type) END) AS annual_keys
            FROM fundamental.financial_statement
            WHERE report_date <= ? AND period_end <= ? AND currency='CNY'
            GROUP BY symbol
        """, [as_of.year - 3, as_of.year - 1, as_of.year - 3, as_of.year - 1,
              as_of, as_of])

    def financial_observations(self, *, as_of: date) -> list[dict]:
        return self._rows("""
            SELECT id, symbol, statement_type, period_end, report_date, metrics,
                   currency, source_name, updated_at, created_at
            FROM fundamental.financial_statement
            WHERE report_date <= ? AND period_end <= ? AND currency='CNY'
              AND statement_type IN ('income', 'cash_flow')
            ORDER BY symbol, period_end, statement_type, id
        """, [as_of, as_of])
