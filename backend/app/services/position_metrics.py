"""Shared read-only position analytics.

Pure Decimal helpers used by both the real-position ledger analysis and the
paper-account position analysis. No repository or HTTP dependencies here.
"""

from collections import defaultdict
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

MONEY_QUANTUM = Decimal("0.0001")
RATIO_QUANTUM = Decimal("0.0000000001")
COST_QUANTUM = Decimal("0.000001")
UNCLASSIFIED_INDUSTRY = "unclassified"


@dataclass
class SymbolValuation:
    """Per-symbol valuation result shared by every analysis view."""

    symbol: str
    quantity: int
    cost_basis: Decimal
    average_cost: Decimal
    price: Decimal | None = None
    price_as_of: object | None = None
    price_quality_status: str | None = None
    price_stale: bool = True
    market_value: Decimal = Decimal()
    unrealized_pnl: Decimal | None = None
    unrealized_pnl_pct: Decimal | None = None
    realized_pnl: Decimal = Decimal()
    extras: dict = field(default_factory=dict)


def quantize_money(value: Decimal) -> Decimal:
    return value.quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)


def quantize_ratio(value: Decimal) -> Decimal:
    return value.quantize(RATIO_QUANTUM, rounding=ROUND_HALF_UP)


def finalize_valuation(
    valuation: SymbolValuation,
    *,
    price: Decimal | None,
    price_as_of,
    price_quality_status: str | None,
) -> SymbolValuation:
    """Attach trusted-price valuation to one holding and derive P&L.

    Without a trusted price the holding falls back to cost-basis valuation and
    ``price_stale`` stays True; unrealized P&L stays unknown (None) instead of
    pretending the cost basis is a market value. Weights are resolved by
    callers once total equity across cash and all holdings is known.
    """
    valuation.price = price
    valuation.price_as_of = price_as_of
    valuation.price_quality_status = price_quality_status
    if price is None:
        valuation.price_stale = True
        valuation.market_value = quantize_money(valuation.cost_basis)
        return valuation

    valuation.price_stale = False
    valuation.market_value = quantize_money(Decimal(valuation.quantity) * price)
    if valuation.cost_basis > 0:
        valuation.unrealized_pnl = quantize_money(
            valuation.market_value - valuation.cost_basis
        )
        valuation.unrealized_pnl_pct = quantize_ratio(
            price / valuation.average_cost - Decimal(1)
        )
    return valuation


def compute_concentration(weights: list[Decimal]) -> tuple[Decimal, Decimal, Decimal]:
    """Return (HHI, top-5 weight, max single-symbol weight) from weights."""
    ordered = sorted(weights, reverse=True)
    hhi = quantize_ratio(sum((w * w for w in ordered), Decimal()))
    top5 = quantize_ratio(sum(ordered[:5], Decimal()))
    max_weight = quantize_ratio(ordered[0]) if ordered else quantize_ratio(Decimal())
    return hhi, top5, max_weight


def group_industry_exposure(
    items: list[tuple[str | None, Decimal]],
    total_equity: Decimal,
) -> list[dict]:
    """Aggregate (industry, market_value) pairs into exposure rows."""
    buckets: dict[str, Decimal] = defaultdict(Decimal)
    counts: dict[str, int] = defaultdict(int)
    for industry, market_value in items:
        key = industry if industry else UNCLASSIFIED_INDUSTRY
        buckets[key] += market_value
        counts[key] += 1
    rows = []
    for industry, market_value in buckets.items():
        weight = (
            quantize_ratio(market_value / total_equity) if total_equity > 0 else Decimal()
        )
        rows.append(
            {
                "industry": industry,
                "market_value": quantize_money(market_value),
                "weight": weight,
                "symbol_count": counts[industry],
            }
        )
    rows.sort(key=lambda row: row["market_value"], reverse=True)
    return rows


def build_cost_basis_valuations(
    positions: list,
    bars: dict,
) -> tuple[dict[str, SymbolValuation], object | None]:
    """Value plain (quantity, average_cost) positions at trusted bars.

    Shared by the paper-account analysis and the drift service so both value
    ``trade.paper_position`` rows with one implementation. ``positions`` are
    any objects with symbol/quantity/average_cost; ``bars`` maps symbol to a
    trusted bar (or is missing the symbol for stale valuation).
    """
    valuations: dict[str, SymbolValuation] = {}
    price_as_of = None
    for position in positions:
        bar = bars.get(position.symbol)
        valuation = SymbolValuation(
            symbol=position.symbol,
            quantity=position.quantity,
            cost_basis=quantize_money(
                Decimal(position.quantity) * Decimal(position.average_cost)
            ),
            average_cost=Decimal(position.average_cost),
        )
        finalize_valuation(
            valuation,
            price=bar.close if bar else None,
            price_as_of=bar.trade_date if bar else None,
            price_quality_status=bar.quality_status if bar else None,
        )
        if bar is not None and (price_as_of is None or bar.trade_date > price_as_of):
            price_as_of = bar.trade_date
        valuations[position.symbol] = valuation
    return valuations, price_as_of
