"""Symbol helpers shared by services, repositories and market utilities."""

from __future__ import annotations

#: Exchanges the backtest / execution calendar path can resolve.
SUPPORTED_EXCHANGES = frozenset({"SH", "SZ", "BJ", "HK"})


def symbol_exchange(symbol: str) -> str | None:
    """Resolve the exchange code from a ``NNNNNN.XX`` symbol.

    Returns ``None`` for symbols without a suffix or with an unsupported
    exchange so callers can fail-closed instead of guessing.
    """
    if "." not in symbol:
        return None
    exchange = symbol.rsplit(".", 1)[1].upper()
    return exchange if exchange in SUPPORTED_EXCHANGES else None
