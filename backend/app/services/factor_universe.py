"""因子服务的「价格宇宙」守卫。

为什么需要
----------
控制面 ``market.daily_bar`` 只存 ``adjust_type='none'`` 的日线（复权序列在
温层 Parquet 里，走研究读路径）。而因子服务的 ``--adjust-type`` 默认是
``qfq``。于是 ``list_for_factor_calculation(adjust_type='qfq')`` 返回空列表，
因子服务遍历空宇宙、写出 0 行、**状态标记 success**——

    factor.total_rows=0
    factor.hot_rank_score.rows=0
    factor.limit_up_breadth.rows=0

不报任何错。这是最坏的一种失败模式：看起来跑成功了，其实什么都没算。
2026-11-11 实测中，这个静默 0 行让人先怀疑「数据断供」，
绕了一圈才定位到参数与控制面实际持有的复权口径不匹配。

守卫
----
``require_price_universe`` 在价格宇宙为空时**直接抛错**，并在错误里说清楚
两件事：请求的复权口径是什么、控制面实际有哪几种。宁可炸掉，也不要静默地
交一份空结果。
"""

from __future__ import annotations

from typing import Any


class EmptyPriceUniverseError(RuntimeError):
    """因子请求的价格宇宙为空——通常是复权口径与控制面实际持有量不匹配。"""


def available_adjust_types(bars: list[Any]) -> tuple[str, ...]:
    """返回给定的 bar 列表里出现过的复权口径。"""

    return tuple(sorted({str(getattr(bar, "adjust_type", "")) for bar in bars}))


def require_price_universe(
    bars_by_symbol: dict[str, list[Any]],
    *,
    adjust_type: str,
    start_date: Any,
    end_date: Any,
    factor_family: str,
    all_bars: list[Any] | None = None,
) -> None:
    """价格宇宙为空时抛 :class:`EmptyPriceUniverseError`。

    参数
    ----
    bars_by_symbol
        按标的分组的可信 bar。
    adjust_type
        本次请求的复权口径。
    start_date / end_date
        请求的交易日区间。
    factor_family
        因子族名，用于错误信息定位。
    all_bars
        未按 security_type 过滤的原始 bar 列表。用于在错误信息里报出
        控制面**实际**持有的复权口径，帮助直接定位口径不匹配。
    """

    if bars_by_symbol:
        return
    hints: list[str] = []
    if all_bars is not None:
        available = available_adjust_types(all_bars)
        if available:
            hints.append(
                f"控制面实际持有的复权口径：{', '.join(available)}"
                f"（本次请求的是 {adjust_type}）"
            )
    hint = "；".join(hints)
    raise EmptyPriceUniverseError(
        f"{factor_family}: 价格宇宙为空 —— {start_date}..{end_date} 区间内没有"
        f"adjust_type={adjust_type!r} 的可信日线，因子无法计算。"
        + (f" {hint}。" if hint else "")
        + " 请用与控制面一致的 --adjust-type 重跑；不要把这次结果当成「无信号」。"
    )
