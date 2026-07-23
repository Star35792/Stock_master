"""Shared security-type derivation from a symbol.

Factor services filter bars by ``security_type`` (stock/etf/index). The raw
daily-bar dump does not carry that column, so the curated layer derives it from
the symbol in SQL. This mirrors ``_infer_security_type`` in
app/services/hithink_collection.py exactly so the curated value matches what the
ORM-backed pipeline would have produced.
"""

from __future__ import annotations


def security_type_case_sql(symbol_expr: str = "symbol") -> str:
    """Return a SQL CASE expression classifying ``symbol_expr`` into a type.

    ``symbol_expr`` is any SQL expression yielding a ``CODE.EXCHANGE`` string
    (e.g. ``'600519.SH'``). The ordering of the branches matches the Python
    reference: ETF prefixes first, then index, then stock, else unknown.
    """
    code = f"split_part({symbol_expr}, '.', 1)"
    exchange = f"split_part({symbol_expr}, '.', 2)"
    return f"""
        CASE
            WHEN {code}[1:2] IN ('15','16','18','51','56','58') THEN 'etf'
            WHEN {code}[1:3] = '399'
                 OR ({exchange} = 'SH' AND {code}[1:3] = '000') THEN 'index'
            WHEN {code}[1:2] IN ('00','30','60','68','83','87','92') THEN 'stock'
            ELSE 'unknown'
        END
    """
