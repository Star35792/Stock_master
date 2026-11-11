"""Bind research diagnostics to trusted price and adjustment observations."""

import json
from collections import defaultdict
from datetime import date
from decimal import Decimal
from hashlib import sha256


def _fingerprint(value: object) -> str:
    return sha256(json.dumps(value, default=str, sort_keys=True,
                             separators=(",", ":")).encode()).hexdigest()


def prepare_inputs(inputs: dict, exchanges: dict[str, str]) -> tuple[dict, dict]:
    """Never mix adjustment source/version/release or fill missing factors with 1.

    All calculations are current research observations, not certified historical
    PIT. Source observations and hashes travel into the immutable report.
    """
    prices, adjustments, financial = defaultdict(list), defaultdict(list), defaultdict(list)
    for row in inputs["prices"]:
        prices[row["symbol"]].append(row)
    for row in inputs["adjustments"]:
        adjustments[row["symbol"]].append(row)
    for row in inputs["financial_rows"]:
        financial[row["symbol"]].append(row)
    prepared = {}
    for symbol, exchange in exchanges.items():
        sessions = inputs["sessions"].get(exchange, [])
        expected = set(sessions)
        selected = [r for r in prices[symbol] if r["trade_date"] in expected]
        direct = [r for r in selected if r["adjust_type"] == "qfq"]
        # A partial or invalid direct series is deliberately not patched with
        # another source. The pure diagnostics function explains its gap.
        if direct:
            sources = {r["source_name"] for r in direct}
            prepared[symbol] = direct if len(sources) == 1 and all(sources) else []
            continue
        none = [r for r in selected if r["adjust_type"] == "none"]
        groups = defaultdict(list)
        for factor in adjustments[symbol]:
            value = factor.get("factor")
            if (factor["trade_date"] in expected and factor["quality_status"] == "trusted"
                    and isinstance(value, Decimal) and value.is_finite() and value > 0
                    and factor["source_name"] and factor["version"]):
                key = (factor["source_name"], factor["version"], factor["release_id"])
                groups[key].append(factor)
        complete = [rows for rows in groups.values() if len(rows) == len(expected) == 21
                    and {r["trade_date"] for r in rows} == expected]
        if not complete:
            prepared[symbol] = []
            continue
        # Most recently observed COMPLETE series, tie broken deterministically.
        chosen = max(complete, key=lambda rows: (
            max(str(r["updated_at"]) for r in rows),
            rows[0]["source_name"], rows[0]["version"], str(rows[0]["release_id"]),
        ))
        by_day = {r["trade_date"]: r for r in chosen}
        anchor = by_day[max(expected)]["factor"]
        bound = []
        for row in none:
            factor = by_day[row["trade_date"]]
            value = row.get("close")
            close = value * factor["factor"] / anchor if (
                isinstance(value, Decimal) and value.is_finite()
            ) else None
            bound.append({
                **row, "adjust_type": "qfq", "close": close,
                "source_name": (
                    f"{row['source_name']}+market.adjustment_factor:{factor['id']}:"
                    f"{factor['source_name']}:{factor['version']}:{factor['release_id']}"
                ),
                "input_hash": _fingerprint([row, factor]),
                "source_ref": (
                    f"market.daily_bar:{row['id']}:{symbol}:{row['trade_date']}:none:"
                    f"{row['source_name']}+market.adjustment_factor:{factor['id']}:"
                    f"{factor['version']}:{factor['release_id']}"
                ),
            })
        prepared[symbol] = bound
    return prepared, dict(financial)


def input_hash(*, sessions: list[date], prices: list[dict], financial: list[dict]) -> str:
    """Bind actual values, collection/revision dates and IDs, not just source names."""
    return _fingerprint({
        "sessions": sessions,
        "prices": sorted(prices, key=lambda r: (r["trade_date"], r["id"])),
        "financial": sorted(financial, key=lambda r: r["id"]),
    })


def prepare_shortlist_liquidity(inputs: dict, exchanges: dict[str, str]) -> dict:
    """Use exactly the last 20 scheduled unadjusted observations, without filling gaps."""
    grouped = defaultdict(list)
    for row in inputs["prices"]:
        if row["adjust_type"] == "none":
            grouped[row["symbol"]].append(row)
    result = {}
    for symbol, exchange in exchanges.items():
        days = inputs["sessions"].get(exchange, [])
        expected = set(sorted(days)[-20:]) if len(days) == 21 else set()
        rows = [r for r in grouped[symbol] if r["trade_date"] in expected]
        valid = (
            len(expected) == len(rows) == 20
            and {r["trade_date"] for r in rows} == expected
            and len({r["source_name"] for r in rows}) == 1
            and all(
                r["quality_status"] in {"trusted", "manual_fixed"}
                and r["is_suspect"] is False and r["is_suspended"] is False
                and r["source_name"] and isinstance(r.get("amount"), Decimal)
                and r["amount"].is_finite() and r["amount"] > 0
                for r in rows
            )
        )
        result[symbol] = {
            "amount_mean_20": str(sum((r["amount"] for r in rows), Decimal(0)) / 20)
            if valid else None,
            "input_hash": _fingerprint(sorted(
                rows, key=lambda r: (r["trade_date"], str(r["id"])),
            )),
            "source_refs": [
                f"market.daily_bar:{r['id']}:{symbol}:{r['trade_date']}:none:{r['source_name']}"
                for r in sorted(rows, key=lambda r: (r["trade_date"], str(r["id"])))
            ] if valid else [],
        }
    return result
