"""Known-minimum decline-streak and leverage SQL for the value-trap gate.

The screen service and ``scripts.research.measure_value_trap_thresholds``
must agree on what "net profit declined two years in a row" means, because
the strategy contract quotes both a market-wide threshold basis and a run
count and the two have to tie out.  Sharing the SQL fragments is what keeps
them tied out.

Semantics: a decline streak counts *observed* consecutive year-on-year
declines backwards from the latest period.  A missing period stops the
count.  Falling through to "3 years down" just because a middle year is
absent would invent a collapse the evidence never showed -- a bug that
used to inflate the run counts (1,366/1,307) above the market basis
(1,363/1,305) by exactly the rows with gaps in their series.

The fragments are ``CASE`` expressions that reference the aliases
``ar`` (annual revenue), ``ap`` (annual profit) and ``ab`` (annual balance)
with the column names used by both callers, so they can be pasted into any
query that provides those aliases.
"""

from __future__ import annotations

#: Consecutive revenue declines observed at the year-end points
#: ``rev_base`` (year-4) .. ``rev_last`` (year-1).  ``0``..``3``, or NULL
#: when even the latest year-on-year change cannot be observed.
REVENUE_DECLINE_STREAK_SQL = """CASE
                     WHEN ar.rev_last IS NULL OR ar.rev_p2 IS NULL THEN NULL
                     WHEN ar.rev_last >= ar.rev_p2 THEN 0
                     WHEN ar.rev_p1 IS NULL THEN 1
                     WHEN ar.rev_p2 >= ar.rev_p1 THEN 1
                     WHEN ar.rev_base IS NULL THEN 2
                     WHEN ar.rev_p1 >= ar.rev_base THEN 2
                     ELSE 3
                   END"""

#: Consecutive parent-holder net-profit declines, same semantics.
NET_PROFIT_DECLINE_STREAK_SQL = """CASE
                     WHEN ap.np_last IS NULL OR ap.np_p2 IS NULL THEN NULL
                     WHEN ap.np_last >= ap.np_p2 THEN 0
                     WHEN ap.np_p1 IS NULL THEN 1
                     WHEN ap.np_p2 >= ap.np_p1 THEN 1
                     WHEN ap.np_base IS NULL THEN 2
                     WHEN ap.np_p1 >= ap.np_base THEN 2
                     ELSE 3
                   END"""

#: Change in the debt ratio (total liabilities / total assets) between the
#: base and the latest annual period end.  NULL unless both endpoints and
#: their equity figures are present and the asset bases are positive.
DEBT_RATIO_CHANGE_SQL = """CASE
                     WHEN ab.assets_base IS NULL OR ab.equity_base IS NULL
                          OR ab.assets_last IS NULL OR ab.equity_last IS NULL
                          OR ab.assets_base <= 0 OR ab.assets_last <= 0
                     THEN NULL
                     ELSE (ab.assets_last - ab.equity_last) / ab.assets_last
                        - (ab.assets_base - ab.equity_base) / ab.assets_base
                   END"""
