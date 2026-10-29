"""探测 HiThink REST API 各端点可用性，评估其作为备用数据源的能力边界。

用小样本真实调用 HithinkFinanceDataSource 的每个方法，输出可用性表：
- OK：端点可用，可作为 Eastmoney 等主源的备用通道
- DENIED：业务 code=2003（权限不足）或文档标注"暂未开放外部接入"
- FAIL：网络/契约错误，暂不可用

用法：
    python backend/scripts/ingest/hithink/probe_capabilities.py
"""

from __future__ import annotations

import time
from datetime import UTC, date, datetime

from app.data_sources.hithink import HithinkFinanceDataSource, HithinkFinanceError

_SAMPLE = "600519.SH"


def _ms(d: date) -> int:
    return int(datetime(d.year, d.month, d.day, tzinfo=UTC).timestamp() * 1000)


def main() -> int:
    ds = HithinkFinanceDataSource()

    probes: list[tuple[str, callable]] = [
        ("prices.snapshot", lambda: ds.fetch_price_snapshots(symbols=[_SAMPLE])),
        (
            "prices.historical",
            lambda: ds.fetch_daily_bars(
                symbol=_SAMPLE, start_date=date(2026, 10, 1), end_date=date(2026, 10, 19)
            ),
        ),
        (
            "corporate-actions.adjustment-factors",
            lambda: ds.fetch_adjustment_factors(symbol=_SAMPLE),
        ),
        (
            "financials.statements",
            lambda: ds.fetch_financial_statements(
                symbol=_SAMPLE, statement_type="income", period="quarterly", limit=1
            ),
        ),
        (
            "financials.indicators",
            lambda: ds.fetch_financial_indicators(symbol=_SAMPLE, report="2026-2"),
        ),
        ("meta.tickers.search", lambda: ds.search_tickers(keyword="贵州茅台", limit=1)),
        ("meta.tickers.list", lambda: ds.fetch_securities(limit=1)),
        ("calendar.trading-days", lambda: ds.fetch_trading_days()),
        ("auction.snapshot", lambda: ds.fetch_auction_snapshot(symbols=[_SAMPLE])),
        ("special-data.limit-up-pool", lambda: ds.fetch_limit_up_pool()),
        ("special-data.limit-up-ladder", lambda: ds.fetch_limit_up_ladder()),
        ("special-data.hot-stock", lambda: ds.fetch_hot_stock_list(period="day")),
        ("a-share-index.ths-indices", lambda: ds.fetch_ths_indices(tag="industry")),
        (
            "index.bars",
            lambda: ds.fetch_index_daily_bars(
                symbol="000300.SH", start_date=date(2026, 10, 1), end_date=date(2026, 10, 19)
            ),
        ),
        ("capital-flow.snapshot", lambda: ds.fetch_capital_flow_snapshot(symbol=_SAMPLE)),
        (
            "capital-flow.historical",
            lambda: ds.fetch_capital_flow_historical(
                symbol=_SAMPLE,
                interval="1d",
                start_ms=_ms(date(2026, 10, 1)),
                end_ms=_ms(date(2026, 10, 19)),
            ),
        ),
    ]

    print(f"{'endpoint':<42} {'status':<8} {'detail'}")
    print("-" * 90)
    ok_count = 0
    for name, probe in probes:
        t0 = time.time()
        try:
            result = probe()
            n = len(result) if hasattr(result, "__len__") else "?"
            print(f"{name:<42} {'OK':<8} rows={n} ({time.time() - t0:.1f}s)")
            ok_count += 1
        except HithinkFinanceError as exc:
            status = "DENIED" if exc.code == 2003 else "FAIL"
            print(f"{name:<42} {status:<8} code={exc.code} {exc} ({time.time() - t0:.1f}s)")
        except Exception as exc:  # noqa: BLE001 - 探测脚本需报告所有异常类型
            print(f"{name:<42} {'FAIL':<8} {type(exc).__name__}: {exc} ({time.time() - t0:.1f}s)")
    print("-" * 90)
    print(f"可用 {ok_count}/{len(probes)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
