"""Value-investing screen against the trusted layer and the warm research layer.

Where the evidence comes from
-----------------------------
* ``research.valuation_history`` (warm Parquet) — the daily PE/PB series and
  the total share count.  This dataset is a *research* observation: the rows
  for a past date were collected later, so ``pit_verified`` is false
  throughout.  It is used for the historical distribution of multiples, not
  as a trusted consumption layer.
* ``market.daily_bar`` / ``market.security`` (trusted layer) — the price on
  the screen date, the tradability and lifecycle facts, the industry label.
* ``fundamental.financial_statement`` (warm Parquet plus the control-plane
  hot window) — net profit, operating cash flow and shareholders' equity.

Why the price and the valuation history come from different places
------------------------------------------------------------------
The price is read from the trusted layer on the screen date, while the
multiple history stops at the last date the valuation sweep covered.  The
margin test does not need them on the same date: it compares the *current*
price with an intrinsic value built from the *current* trailing earnings and
a conservative multiple anchored on the stock's own history.  The current
price therefore enters as ``P`` and never as a historical PE observation.

Both dates are reported explicitly (``price_as_of`` /
``valuation_window_end``) so a reader can see how stale the distribution is.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from decimal import Decimal
from hashlib import sha256
from typing import Any
from zoneinfo import ZoneInfo

from app.config.settings import settings
from app.repositories.warm_layer import WarmLayerStore
from app.services.financial_sector import is_financial_industry
from app.services.price_value_shortlist import PARAMETERS as SHORTLIST_PARAMETERS
from app.services.value_trap_trend import (
    DEBT_RATIO_CHANGE_SQL,
    NET_PROFIT_DECLINE_STREAK_SQL,
    REVENUE_DECLINE_STREAK_SQL,
)
from app.strategies.governance_veto import apply_governance_vetoes
from app.strategies.registry import (
    VALUE_INVESTING_NAME,
    params_from_values,
)
from app.strategies.value_investing import (
    ValueInvestingEvidence,
    ValueInvestingParams,
    screen,
)

SHANGHAI = ZoneInfo("Asia/Shanghai")

#: Exchanges that must be present on the screen date.
REQUIRED_EXCHANGES = ("SH", "SZ", "BJ")

#: Trailing window used to build the historical multiple distribution.
VALUATION_HISTORY_LOOKBACK_DAYS = 1095

#: Number of trailing trading days used for the liquidity average.
LIQUIDITY_WINDOW = 20

#: Calendar days scanned backwards when looking for the liquidity window.
LIQUIDITY_LOOKBACK_DAYS = 60

CYCLICAL_INDUSTRY_TOKENS: tuple[str, ...] = tuple(
    SHORTLIST_PARAMETERS["cyclical_industry_tokens"]
)


class ValueInvestingScreenService:
    """Run the built-in value-investing screen and return the full report."""

    def __init__(self, *, warm_store: WarmLayerStore | None = None) -> None:
        self.warm_store = warm_store or WarmLayerStore()

    # ------------------------------------------------------------ helpers

    def _connect(self):
        store = self.warm_store
        base_tag = store.latest_release_tag
        if base_tag is None:
            raise ValueError("no warm-layer release is available for research reads")
        return store.connect(release_tag=base_tag, read_only=True, attach_control=True)

    def _definition(self, connection) -> tuple[str, str, ValueInvestingParams]:
        """Read the registered strategy definition back from the catalog.

        The catalog is the fact source for every locked parameter: the screen
        never takes the margin, the thresholds or the business-quality gate
        from its caller.  The active definition is the one with the highest
        version among the rows that are still active; superseded versions
        stay registered for audit but are never selected.
        """
        row = connection.execute(
            "SELECT name, version, required_margin, parameters, status, is_active "
            "FROM strategy.strategy_definition "
            "WHERE name = ? AND status = 'active' AND is_active "
            "ORDER BY version DESC LIMIT 1",
            [VALUE_INVESTING_NAME],
        ).fetchone()
        if row is None:
            raise ValueError(
                "the built-in value-investing strategy is not registered; run "
                "register_all_strategy_definitions() first"
            )
        name, version, margin, parameters, status, is_active = row
        if status != "active" or not is_active:
            raise ValueError(
                f"strategy definition {name}@{version} is not active "
                f"(status={status}, is_active={is_active})"
            )
        if margin is None:
            raise ValueError(
                f"strategy definition {name}@{version} has no required_margin"
            )
        if isinstance(parameters, str):
            # The native DuckDB handle returns a JSON column as text; the
            # SQLAlchemy handle would hand back a dict.
            parameters = json.loads(parameters) if parameters else {}
        params = params_from_values(margin, parameters)
        return name, version, params
    def _target_date(self, connection, *, as_of: date | None) -> date:
        """Return the last **completed** trading day of the screen.

        The screen date must be strictly before today in Shanghai time: the
        daily bar of the running session is a partial snapshot, and using it
        would compare a half-day price against a full-day valuation.
        """
        today = datetime.now(SHANGHAI).date()
        if as_of is not None:
            if as_of >= today:
                raise ValueError(
                    "as_of must be a completed trading day strictly before today"
                )
            return as_of
        row = connection.execute(
            "SELECT max(trade_date) FROM market.trading_calendar "
            "WHERE trade_date < ? AND trade_date IN "
            "(SELECT DISTINCT trade_date FROM market.daily_bar "
            " WHERE adjust_type = 'none' AND quality_status IN "
            "('trusted', 'manual_fixed'))",
            [today],
        ).fetchone()
        if row is None or row[0] is None:
            raise ValueError("no completed trading day is available for the screen")
        return row[0]

    # ---------------------------------------------------------- evidence

    def _build_evidence(
        self,
        connection,
        *,
        target: date,
        params: ValueInvestingParams,
    ) -> tuple[list[ValueInvestingEvidence], dict[str, Any]]:
        """Collect one evidence bundle per security."""
        year = target.year
        annual_periods = (
            date(year - 3, 12, 31),
            date(year - 2, 12, 31),
            date(year - 1, 12, 31),
        )
        # The trend window needs one period more than the quality window:
        # three year-on-year changes require four annual reports.
        trend_periods = (
            date(year - 4, 12, 31),
            date(year - 3, 12, 31),
            date(year - 2, 12, 31),
            date(year - 1, 12, 31),
        )
        cumulative_periods = (
            date(year, 3, 31),
            date(year, 6, 30),
            date(year, 9, 30),
        )
        history_start = target - timedelta(days=VALUATION_HISTORY_LOOKBACK_DAYS)
        liquidity_start = target - timedelta(days=LIQUIDITY_LOOKBACK_DAYS)

        sql = f"""
        WITH
        bars AS (
            SELECT symbol, close, amount, volume, is_suspended
            FROM market.daily_bar
            WHERE trade_date = DATE '{target}'
              AND adjust_type = 'none'
              AND quality_status IN ('trusted', 'manual_fixed')
              AND NOT is_suspect
        ),
        recent_dates AS (
            SELECT DISTINCT trade_date
            FROM market.daily_bar
            WHERE trade_date > DATE '{liquidity_start}'
              AND trade_date <= DATE '{target}'
              AND adjust_type = 'none'
              AND quality_status IN ('trusted', 'manual_fixed')
            ORDER BY trade_date DESC
            LIMIT {LIQUIDITY_WINDOW}
        ),
        liquidity AS (
            SELECT d.symbol, avg(d.amount) AS amount_20d, count(*) AS observations
            FROM market.daily_bar d
            JOIN recent_dates r ON r.trade_date = d.trade_date
            WHERE d.adjust_type = 'none'
              AND d.quality_status IN ('trusted', 'manual_fixed')
              AND NOT d.is_suspect
              AND d.amount > 0
            GROUP BY d.symbol
        ),
        valuation_history AS (
            SELECT symbol,
                   count(*) FILTER (WHERE pe_ttm > 0) AS pe_observations,
                   count(*) FILTER (WHERE pb_mrq > 0) AS pb_observations,
                   quantile_disc(pe_ttm, {params.anchor_quantile})
                       FILTER (WHERE pe_ttm > 0) AS pe_fair,
                   quantile_disc(pb_mrq, {params.anchor_quantile})
                       FILTER (WHERE pb_mrq > 0) AS pb_fair,
                   min(trade_date) AS window_start,
                   max(trade_date) AS window_end
            FROM research.valuation_history
            WHERE trade_date >= DATE '{history_start}'
              AND trade_date <= DATE '{target}'
            GROUP BY symbol
        ),
        valuation_latest AS (
            SELECT symbol, close, pe_ttm, pb_mrq, total_shares, trade_date
            FROM research.valuation_history
            WHERE trade_date <= DATE '{target}'
            QUALIFY row_number() OVER (
                PARTITION BY symbol ORDER BY trade_date DESC
            ) = 1
        ),
        statements AS (
            SELECT symbol, period_end, statement_type, report_date,
                   try_cast(json_extract_string(metrics,
                       '$.parent_holder_net_profit') AS DOUBLE) AS net_profit,
                   try_cast(json_extract_string(metrics,
                       '$.operating_income') AS DOUBLE) AS revenue,
                   try_cast(json_extract_string(metrics,
                       '$.act_cash_flow_net') AS DOUBLE) AS operating_cash_flow,
                   try_cast(json_extract_string(metrics,
                       '$.holder_equity_total') AS DOUBLE) AS equity,
                   try_cast(json_extract_string(metrics,
                       '$.assets_total') AS DOUBLE) AS assets_total
            -- Read the control plane directly, not the warm-layer view.
            -- The warm view for this table is Parquet history plus the
            -- control-plane hot window, and the Parquet snapshot of the
            -- annual reports is lossy: it carries FY2024 and FY2025 for
            -- ~5,570 securities but FY2022 and FY2023 for barely a hundred,
            -- because those rows had already been slimmed out of the
            -- control plane when the export ran and were never restored
            -- there.  The union therefore drops the control-plane rows it
            -- thinks the Parquet already covers, which silently turned the
            -- "three-year" gates into two-year gates for 5,543 of 5,586
            -- securities.  The control plane holds the complete annual set
            -- (5,560 securities for each of FY2022..FY2025), so it is the
            -- authoritative source for the annual window.  The big tables
            -- (daily bars, valuation history) still come from the warm
            -- layer.
            FROM ctrl.fundamental.financial_statement
            -- Point in time: only statements that had been disclosed on the
            -- screen date may be used.  Without this the QUALIFY below would
            -- happily pick a revision published *after* the screen date.
            -- A statement with an unknown disclosure date is excluded rather
            -- than assumed to be old.
            WHERE report_date <= DATE '{target}'
            QUALIFY row_number() OVER (
                PARTITION BY symbol, statement_type, period_end
                ORDER BY report_date DESC NULLS LAST
            ) = 1
        ),
        annual AS (
            SELECT symbol,
                   sum(net_profit) FILTER (
                       WHERE statement_type = 'income'
                         AND period_end IN (
                             DATE '{annual_periods[0]}', DATE '{annual_periods[1]}',
                             DATE '{annual_periods[2]}')
                   ) AS net_profit_3y,
                   sum(operating_cash_flow) FILTER (
                       WHERE statement_type = 'cash_flow'
                         AND period_end IN (
                             DATE '{annual_periods[0]}', DATE '{annual_periods[1]}',
                             DATE '{annual_periods[2]}')
                   ) AS ocf_3y,
                   count(*) FILTER (
                       WHERE statement_type = 'income'
                         AND period_end IN (
                             DATE '{annual_periods[0]}', DATE '{annual_periods[1]}',
                             DATE '{annual_periods[2]}')
                   ) AS annual_periods_present
            FROM statements
            GROUP BY symbol
        ),        cumulative AS (
            SELECT symbol,
                   sum(net_profit) FILTER (
                       WHERE statement_type = 'income'
                         AND period_end = DATE '{annual_periods[2]}'
                   ) AS fiscal_year_net_profit,
                   max(period_end) FILTER (
                       WHERE statement_type = 'income'
                         AND period_end IN (
                             DATE '{cumulative_periods[0]}',
                             DATE '{cumulative_periods[1]}',
                             DATE '{cumulative_periods[2]}')
                         AND period_end <= DATE '{target}'
                   ) AS current_period_end
            FROM statements
            GROUP BY symbol
        ),
        current_cumulative AS (
            SELECT c.symbol, c.current_period_end, c.fiscal_year_net_profit,
                   s.net_profit AS current_cumulative_net_profit
            FROM cumulative c
            JOIN statements s
              ON s.symbol = c.symbol
             AND s.statement_type = 'income'
             AND s.period_end = c.current_period_end
            WHERE c.current_period_end IS NOT NULL
              AND c.fiscal_year_net_profit IS NOT NULL
        ),
        prior_cumulative AS (
            SELECT cc.symbol, cc.current_period_end, cc.current_cumulative_net_profit,
                   cc.fiscal_year_net_profit,
                   p.net_profit AS prior_cumulative_net_profit
            FROM current_cumulative cc
            JOIN statements p
              ON p.symbol = cc.symbol
             AND p.statement_type = 'income'
             AND p.period_end = (cc.current_period_end - INTERVAL 1 YEAR)
            WHERE p.net_profit IS NOT NULL
        ),
        ttm_profit AS (
            SELECT pc.symbol, pc.current_period_end,
                   pc.fiscal_year_net_profit
                     + pc.current_cumulative_net_profit
                     - pc.prior_cumulative_net_profit AS ttm_profit_value
            FROM prior_cumulative pc
        ),
        equity_latest AS (
            SELECT symbol, period_end AS equity_period_end, equity
            FROM statements
            WHERE statement_type = 'balance' AND equity IS NOT NULL
            QUALIFY row_number() OVER (
                PARTITION BY symbol ORDER BY period_end DESC
            ) = 1
        ),
        -- ------------------------------------------------------ trend window
        -- Four annual reports, so three year-on-year changes can be
        -- observed.  One extra year beyond the quality window: a trend
        -- needs a base period to measure the first change against.
        annual_revenue AS (
            SELECT symbol,
                   count(*) FILTER (WHERE revenue IS NOT NULL) AS revenue_periods_present,
                   max(revenue) FILTER (
                       WHERE period_end = DATE '{trend_periods[0]}') AS rev_base,
                   max(revenue) FILTER (
                       WHERE period_end = DATE '{trend_periods[1]}') AS rev_p1,
                   max(revenue) FILTER (
                       WHERE period_end = DATE '{trend_periods[2]}') AS rev_p2,
                   max(revenue) FILTER (
                       WHERE period_end = DATE '{trend_periods[3]}') AS rev_last
            FROM statements
            WHERE statement_type = 'income'
              AND period_end IN (
                  DATE '{trend_periods[0]}', DATE '{trend_periods[1]}',
                  DATE '{trend_periods[2]}', DATE '{trend_periods[3]}')
            GROUP BY symbol
        ),
        annual_profit_trend AS (
            SELECT symbol,
                   count(*) FILTER (WHERE net_profit IS NOT NULL)
                       AS profit_periods_present,
                   max(net_profit) FILTER (
                       WHERE period_end = DATE '{trend_periods[0]}') AS np_base,
                   max(net_profit) FILTER (
                       WHERE period_end = DATE '{trend_periods[1]}') AS np_p1,
                   max(net_profit) FILTER (
                       WHERE period_end = DATE '{trend_periods[2]}') AS np_p2,
                   max(net_profit) FILTER (
                       WHERE period_end = DATE '{trend_periods[3]}') AS np_last
            FROM statements
            WHERE statement_type = 'income'
              AND period_end IN (
                  DATE '{trend_periods[0]}', DATE '{trend_periods[1]}',
                  DATE '{trend_periods[2]}', DATE '{trend_periods[3]}')
            GROUP BY symbol
        ),
        annual_balance_trend AS (
            SELECT symbol,
                   max(assets_total) FILTER (
                       WHERE period_end = DATE '{trend_periods[0]}') AS assets_base,
                   max(equity) FILTER (
                       WHERE period_end = DATE '{trend_periods[0]}') AS equity_base,
                   max(assets_total) FILTER (
                       WHERE period_end = DATE '{trend_periods[3]}') AS assets_last,
                   max(equity) FILTER (
                       WHERE period_end = DATE '{trend_periods[3]}') AS equity_last
            FROM statements
            WHERE statement_type = 'balance'
              AND period_end IN (
                  DATE '{trend_periods[0]}', DATE '{trend_periods[1]}',
                  DATE '{trend_periods[2]}', DATE '{trend_periods[3]}')
            GROUP BY symbol
        ),
        trend AS (
            -- Value-trap evidence: is the business shrinking, and is the
            -- balance sheet levering up?  A decline streak counts backwards
            -- from the latest period, so "the last two years fell" is
            -- visible without inspecting the whole series.
            SELECT ar.symbol,
                   least(ar.revenue_periods_present, ap.profit_periods_present)
                       AS trend_periods_present,
                   CASE WHEN ar.rev_base > 0 AND ar.rev_last > 0
                        THEN power(ar.rev_last / ar.rev_base, 1.0 / 3.0) - 1
                   END AS revenue_cagr_3y,
                   CASE WHEN ap.np_base > 0 AND ap.np_last > 0
                        THEN power(ap.np_last / ap.np_base, 1.0 / 3.0) - 1
                   END AS net_profit_cagr_3y,
                   -- The streak counts *observed* consecutive declines
                   -- backwards from the latest period; see
                   -- app.services.value_trap_trend for the semantics.
                   {REVENUE_DECLINE_STREAK_SQL} AS revenue_decline_streak,
                   {NET_PROFIT_DECLINE_STREAK_SQL} AS net_profit_decline_streak,
                   {DEBT_RATIO_CHANGE_SQL} AS debt_ratio_change_3y
            FROM annual_revenue ar
            JOIN annual_profit_trend ap ON ap.symbol = ar.symbol
            LEFT JOIN annual_balance_trend ab ON ab.symbol = ar.symbol
        ),
        -- ------------------------------------------------ business quality
        annual_equity AS (
            -- Shareholders' equity at each of the three annual period ends,
            -- used as the denominator of the return-on-equity measure.
            SELECT symbol,
                   count(*) AS equity_periods_present,
                   avg(equity) AS equity_avg_3y
            FROM statements
            WHERE statement_type = 'balance'
              AND period_end IN (
                  DATE '{annual_periods[0]}', DATE '{annual_periods[1]}',
                  DATE '{annual_periods[2]}')
              AND equity IS NOT NULL
            GROUP BY symbol
        ),
        roe_3y AS (
            -- Annualised three-year return on equity:
            -- sum(net profit) / average(equity) / 3.  The window is exactly
            -- the one the cash-conversion gate uses, so both quality gates
            -- read the same three annual reports.
            SELECT a.symbol,
                   ae.equity_periods_present,
                   CASE WHEN ae.equity_avg_3y IS NOT NULL
                             AND ae.equity_avg_3y > 0
                             AND ae.equity_periods_present = 3
                             AND a.annual_periods_present = 3
                        THEN a.net_profit_3y / ae.equity_avg_3y / 3
                   END AS roe_3y
            FROM annual a
            LEFT JOIN annual_equity ae ON ae.symbol = a.symbol
        ),
        statement_disclosure AS (
            SELECT symbol, period_end, min(report_date) AS report_date
            FROM statements
            GROUP BY symbol, period_end
        ),
        indicator_periods AS (
            -- The indicator table keys a report by code ("2026-2" = the
            -- half-year ended 2026-06-30) and carries no disclosure date,
            -- so the code is translated into a period end and matched back
            -- to the statement that carries the real disclosure date.
            SELECT DISTINCT report,
                   substr(report, 1, 4) ||
                       CASE substr(report, 6, 1)
                           WHEN '1' THEN '-03-31'
                           WHEN '2' THEN '-06-30'
                           WHEN '3' THEN '-09-30'
                           WHEN '4' THEN '-12-31'
                       END AS period_end
            FROM fundamental.financial_indicator
        ),
        indicator_rows AS (
            SELECT fi.symbol, fi.indicator_name, fi.value, ip.period_end
            FROM fundamental.financial_indicator fi
            JOIN indicator_periods ip ON ip.report = fi.report
            JOIN statement_disclosure sd
              ON sd.symbol = fi.symbol
             AND sd.period_end = CAST(ip.period_end AS DATE)
            WHERE sd.report_date <= DATE '{target}'
        ),
        indicator_pick AS (
            -- The window is per *indicator*: partitioning by symbol alone
            -- would keep exactly one indicator row per security and leave
            -- the others empty.
            SELECT symbol, indicator_name, value,
                   row_number() OVER (
                       PARTITION BY symbol, indicator_name ORDER BY period_end DESC
                   ) AS rn
            FROM indicator_rows
        ),
        indicator_latest AS (
            SELECT symbol,
                   max(CASE WHEN indicator_name = 'sale_gross_margin'
                            THEN value END) AS gross_margin,
                   max(CASE WHEN indicator_name = 'assets_debt_ratio'
                            THEN value END) AS debt_ratio,
                   max(CASE WHEN indicator_name =
                            'calculate_parent_holder_net_profit_yoy_growth_ratio'
                            THEN value END) AS profit_yoy
            FROM indicator_pick
            WHERE rn = 1
            GROUP BY symbol
        ),
        quality_base AS (
            SELECT s.symbol, s.industry, r.roe_3y, il.profit_yoy
            FROM market.security s
            LEFT JOIN roe_3y r ON r.symbol = s.symbol
            LEFT JOIN indicator_latest il ON il.symbol = s.symbol
            WHERE s.security_type = 'stock'
        ),
        industry_quality AS (
            -- Self-built industry aggregates: the peer group's median return
            -- on equity and median profit growth.  A relative position, not
            -- an industry forecast.
            SELECT industry,
                   median(roe_3y) AS industry_roe_median,
                   median(profit_yoy) FILTER (
                       WHERE profit_yoy IS NOT NULL AND abs(profit_yoy) < 500
                   ) AS industry_profit_yoy_median
            FROM quality_base
            WHERE industry IS NOT NULL
            GROUP BY industry
        ),
        dividends AS (
            -- Shareholder-return continuity: how many of the trailing five
            -- calendar years carried a cash dividend.
            SELECT symbol,
                   count(DISTINCT year(ex_date)) AS dividend_years_5y
            FROM market.corporate_action
            WHERE action_type = 'cash_dividend'
              AND ex_date IS NOT NULL
              AND ex_date > DATE '{target}' - INTERVAL 5 YEAR
              AND ex_date <= DATE '{target}'
            GROUP BY symbol
        )
        SELECT s.symbol, s.name, s.industry, s.list_date, s.delist_date,
               s.is_active, s.special_treatment_status AS security_treatment,
               b.close AS bar_close, b.is_suspended, b.amount AS bar_amount,
               l.amount_20d, l.observations AS liquidity_observations,
               vl.close AS valuation_close, vl.pe_ttm AS vendor_pe_ttm,
               vl.pb_mrq AS vendor_pb_mrq, vl.total_shares,
               vl.trade_date AS valuation_trade_date,
               vh.pe_observations, vh.pb_observations, vh.pe_fair, vh.pb_fair,
               vh.window_start, vh.window_end,
               t.ttm_profit_value, t.current_period_end AS ttm_period_end,
               a.net_profit_3y, a.ocf_3y, a.annual_periods_present,
               e.equity, e.equity_period_end,
               q.roe_3y, q.equity_periods_present, il.gross_margin, il.debt_ratio,
               iq.industry_roe_median, iq.industry_profit_yoy_median,
               d.dividend_years_5y,
               tr.revenue_cagr_3y, tr.net_profit_cagr_3y,
               tr.revenue_decline_streak, tr.net_profit_decline_streak,
               tr.debt_ratio_change_3y, tr.trend_periods_present
        FROM market.security s
        LEFT JOIN bars b ON b.symbol = s.symbol
        LEFT JOIN liquidity l ON l.symbol = s.symbol
        LEFT JOIN valuation_history vh ON vh.symbol = s.symbol
        LEFT JOIN valuation_latest vl ON vl.symbol = s.symbol
        LEFT JOIN ttm_profit t ON t.symbol = s.symbol
        LEFT JOIN annual a ON a.symbol = s.symbol
        LEFT JOIN equity_latest e ON e.symbol = s.symbol
        LEFT JOIN roe_3y q ON q.symbol = s.symbol
        LEFT JOIN indicator_latest il ON il.symbol = s.symbol
        LEFT JOIN industry_quality iq ON iq.industry = s.industry
        LEFT JOIN dividends d ON d.symbol = s.symbol
        LEFT JOIN trend tr ON tr.symbol = s.symbol
        WHERE s.security_type = 'stock'
        """
        rows = connection.execute(sql).fetchall()
        columns = [description[0] for description in connection.description]
        records = [dict(zip(columns, row, strict=True)) for row in rows]

        evidence: list[ValueInvestingEvidence] = []
        for record in records:
            industry = record.get("industry")
            industry_text = industry.strip() if isinstance(industry, str) else None
            listed_days = None
            list_date = record.get("list_date")
            if list_date is not None:
                listed_days = (target - list_date).days
            evidence.append(
                ValueInvestingEvidence(
                    symbol=record["symbol"],
                    name=record.get("name"),
                    industry=industry_text,
                    close=_decimal(record.get("bar_close")),
                    total_shares=_decimal(record.get("total_shares")),
                    ttm_net_profit=_decimal(record.get("ttm_profit_value")),
                    ttm_period_end=record.get("ttm_period_end"),
                    net_profit_3y=_decimal(record.get("net_profit_3y")),
                    ocf_3y=_decimal(record.get("ocf_3y")),
                    equity=_decimal(record.get("equity")),
                    equity_period_end=record.get("equity_period_end"),
                    pe_fair=_decimal(record.get("pe_fair")),
                    pb_fair=_decimal(record.get("pb_fair")),
                    pe_observations=int(record.get("pe_observations") or 0),
                    pb_observations=int(record.get("pb_observations") or 0),
                    vendor_pe_ttm=_decimal(record.get("vendor_pe_ttm")),
                    vendor_pb_mrq=_decimal(record.get("vendor_pb_mrq")),
                    listed_days=listed_days,
                    amount_20d=_decimal(record.get("amount_20d")),
                    is_suspended=record.get("is_suspended"),
                    special_treatment_status=(
                        record.get("security_treatment")
                        or record.get("bar_treatment")
                    ),
                    is_financial_sector=is_financial_industry(industry_text),
                    is_cyclic_industry=_is_cyclic(industry_text),
                    roe_3y=_decimal(record.get("roe_3y")),
                    gross_margin=_decimal(record.get("gross_margin")),
                    debt_ratio=_decimal(record.get("debt_ratio")),
                    industry_roe_median=_decimal(record.get("industry_roe_median")),
                    industry_profit_yoy_median=_decimal(
                        record.get("industry_profit_yoy_median")
                    ),
                    dividend_years_5y=(
                        None
                        if record.get("dividend_years_5y") is None
                        else int(record["dividend_years_5y"])
                    ),
                    annual_periods_present=(
                        None
                        if record.get("annual_periods_present") is None
                        else int(record["annual_periods_present"])
                    ),
                    equity_periods_present=(
                        None
                        if record.get("equity_periods_present") is None
                        else int(record["equity_periods_present"])
                    ),
                    revenue_cagr_3y=_decimal(record.get("revenue_cagr_3y")),
                    net_profit_cagr_3y=_decimal(record.get("net_profit_cagr_3y")),
                    revenue_decline_streak=(
                        None
                        if record.get("revenue_decline_streak") is None
                        else int(record["revenue_decline_streak"])
                    ),
                    net_profit_decline_streak=(
                        None
                        if record.get("net_profit_decline_streak") is None
                        else int(record["net_profit_decline_streak"])
                    ),
                    debt_ratio_change_3y=_decimal(
                        record.get("debt_ratio_change_3y")
                    ),
                    trend_periods_present=(
                        None
                        if record.get("trend_periods_present") is None
                        else int(record["trend_periods_present"])
                    ),
                    notes=_notes(record, target),
                )
            )

        coverage = {
            "valuation_window_start": min(
                (record["window_start"] for record in records
                 if record.get("window_start")),
                default=None,
            ),
            "valuation_window_end": max(
                (record["window_end"] for record in records
                 if record.get("window_end")),
                default=None,
            ),
            "annual_periods": [period.isoformat() for period in annual_periods],
            "cumulative_periods": [
                period.isoformat() for period in cumulative_periods
            ],
        }
        return evidence, coverage

    # --------------------------------------------------------------- run

    def run(self, *, as_of: date | None = None) -> dict:
        """Run the screen and return the report."""
        connection = self._connect()
        try:
            name, version, params = self._definition(connection)
            target = self._target_date(connection, as_of=as_of)
            evidence, coverage = self._build_evidence(
                connection, target=target, params=params
            )
            result = screen(evidence, params)
        finally:
            connection.close()

        # Governance vetoes are a human research judgement layered on top of
        # the kernel result: the kernel still counts every quantitative pass,
        # and the veto is recorded separately so both facts stay visible.
        governance_as_of = datetime.now(SHANGHAI).date() if as_of is None else as_of
        result = apply_governance_vetoes(result, as_of_date=governance_as_of)

        report = {
            **result,
            "strategy": name,
            "strategy_version": version,
            "kernel_version": result.get("kernel_version") or result.get("strategy"),
            "target_trade_date": target.isoformat(),
            "price_as_of": target.isoformat(),
            "governance_as_of": governance_as_of.isoformat(),
            "code_version": settings.app_code_version,
            "generated_at": datetime.now(SHANGHAI).isoformat(),
            "coverage": coverage,
        }
        stable = {key: value for key, value in report.items() if key != "generated_at"}
        report["snapshot_hash"] = sha256(
            json.dumps(
                stable, ensure_ascii=False, sort_keys=True, default=str
            ).encode()
        ).hexdigest()
        return report


def _decimal(value: object) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        return value if value.is_finite() else None
    try:
        number = Decimal(str(value))
    except Exception:  # noqa: BLE001 - any unusable value blocks the row
        return None
    return number if number.is_finite() else None


def _is_cyclic(industry: str | None) -> bool:
    if not industry:
        return False
    return any(token in industry for token in CYCLICAL_INDUSTRY_TOKENS)


def _notes(record: dict[str, Any], target: date) -> tuple[str, ...]:
    notes: list[str] = []
    if record.get("annual_periods_present") not in (None, 3):
        notes.append("annual_periods_incomplete")
    if record.get("liquidity_observations") not in (None, LIQUIDITY_WINDOW):
        notes.append("liquidity_window_incomplete")
    valuation_date = record.get("valuation_trade_date")
    if valuation_date is not None and valuation_date != target:
        notes.append("valuation_snapshot_older_than_price_date")
    return tuple(notes)
