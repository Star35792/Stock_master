"""Run the built-in value-investing screen against the real database.

The script registers the built-in strategy definition if it is missing (so
the locked margin of safety always comes from the catalog), runs the screen
and writes a JSON report plus a Markdown summary.

Run from ``backend``::

    .\\.venv\\Scripts\\python.exe -m scripts.research.screen_value_investing \\
        --output outputs/value_investing_20261109.json

``--as-of`` selects a completed trading day; it must be strictly before today
in Shanghai time.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from datetime import date
from pathlib import Path

from app.database.session import SessionLocal, engine, initialize_database
from app.services.value_investing_screen import ValueInvestingScreenService
from app.strategies.registry import register_all_strategy_definitions
from app.utils.duckdb_write_lock import duckdb_write_lock


def _report_paths(output: Path) -> tuple[Path, Path]:
    json_path = output
    markdown_path = output.with_suffix(".md")
    if json_path == markdown_path:
        raise ValueError("--output must not be a .md path")
    return json_path, markdown_path


def _stage_bytes(target: Path, content: bytes) -> Path:
    """Durably stage one complete file beside its target."""
    descriptor, temporary_name = tempfile.mkstemp(
        dir=target.parent,
        prefix=f".{target.name}.",
        suffix=".tmp",
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
    except BaseException:
        try:
            os.close(descriptor)
        except OSError:
            pass
        temporary_path.unlink(missing_ok=True)
        raise
    return temporary_path


def _fsync_directory(path: Path) -> None:
    """Persist directory entries where the platform exposes directory fsync."""
    if os.name == "nt":
        return
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    descriptor = os.open(path, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _restore_markdown(path: Path, previous: bytes | None) -> None:
    if previous is None:
        path.unlink(missing_ok=True)
        return
    temporary = _stage_bytes(path, previous)
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _publish_report_files(
    json_path: Path,
    markdown_path: Path,
    *,
    json_text: str,
    markdown_text: str,
) -> None:
    """Publish complete report files without exposing truncated content.

    Both payloads are staged and synced before either target changes. Markdown
    is replaced first and JSON last, making the authoritative JSON report the
    commit point. A normal second-replace failure restores the old Markdown;
    two independent paths still cannot form a power-loss atomic transaction.
    """
    if json_path.parent.resolve() != markdown_path.parent.resolve():
        raise ValueError("report JSON and Markdown must share one directory")
    if json_path.resolve() == markdown_path.resolve():
        raise ValueError("report JSON and Markdown paths must be distinct")

    json_path.parent.mkdir(parents=True, exist_ok=True)
    staged: list[Path] = []
    try:
        staged_json = _stage_bytes(json_path, json_text.encode("utf-8"))
        staged.append(staged_json)
        staged_markdown = _stage_bytes(markdown_path, markdown_text.encode("utf-8"))
        staged.append(staged_markdown)

        previous_markdown = markdown_path.read_bytes() if markdown_path.exists() else None
        os.replace(staged_markdown, markdown_path)
        staged.remove(staged_markdown)
        try:
            os.replace(staged_json, json_path)
            staged.remove(staged_json)
        except BaseException:
            _restore_markdown(markdown_path, previous_markdown)
            raise
        _fsync_directory(json_path.parent)
    finally:
        for temporary in staged:
            temporary.unlink(missing_ok=True)


def _ensure_registered() -> None:
    """Make sure the strategy catalog holds the built-in definition.

    The registration writes to the control plane, so the engine must release
    the DuckDB file before the screen attaches it again as a warm-layer
    control plane; otherwise DuckDB refuses the second handle.
    """
    initialize_database()
    with duckdb_write_lock("data/duckdb_write.lock", timeout_seconds=30.0):
        with SessionLocal() as db:
            register_all_strategy_definitions(db)
            db.commit()
    engine.dispose()


def _format_money(value: object) -> str:
    if value is None:
        return "-"
    try:
        return f"{float(value):.4f}"
    except (TypeError, ValueError):
        return str(value)


def _format_percent(value: object) -> str:
    if value is None:
        return "-"
    try:
        return f"{float(value) * 100:.1f}%"
    except (TypeError, ValueError):
        return str(value)


def _format_ratio(value: object) -> str:
    """Format a Decimal ratio (0.08) as a percentage."""
    if value is None:
        return "-"
    try:
        return f"{float(value) * 100:.2f}%"
    except (TypeError, ValueError):
        return str(value)


def _quality_markdown(report: dict) -> list[str]:
    """The business-quality section: coverage, gate and the cost of the gate."""
    profile = report.get("quality_profile") or {}
    params = report.get("params") or {}
    lines = [
        "",
        "## 企业质地体检",
        "",
        f"- 质地硬闸门：3 年年化 ROE ≥ {_format_ratio(params.get('min_roe_3y'))}"
        + ("（未启用）" if params.get("min_roe_3y") is None else ""),
        f"- 3 年年化 ROE 口径：{profile.get('roe_formula', '')}",
        "",
        "### 六个企业研究维度的覆盖状态",
        "",
        "| 维度 | 覆盖 | 是否硬闸门 | 用到的字段 | 说明 |",
        "|---|---|---|---|---|",
    ]
    status_label = {"covered": "有数据", "proxy": "仅代理", "unavailable": "无数据"}
    for dimension in profile.get("dimensions", []):
        fields = "、".join(f"`{name}`" for name in dimension.get("fields") or [])
        lines.append(
            f"| {dimension.get('label')} | {status_label.get(dimension.get('status'), '-')} "
            f"| {'是' if dimension.get('gate') else '否'} | {fields or '-'} "
            f"| {dimension.get('note')} |"
        )
    lines += ["", f"> {profile.get('disclaimer', '')}", ""]
    return lines


def _price_only_markdown(report: dict) -> list[str]:
    """What the price gate alone would have produced, and what the gate cost."""
    price_only = report.get("price_gate_only") or {}
    removed = price_only.get("removed_by_quality_gate") or []
    lines = [
        "",
        "## 仅价格闸门对照（关闭企业质地闸门）",
        "",
        f"- 仅价格闸门达标：{price_only.get('qualified_count', 0)} 只",
        f"- 施加企业质地闸门后达标：{report.get('qualified_count', 0)} 只",
        f"- 被质地闸门剔除：{len(removed)} 只"
        + (f" —— {'、'.join(removed)}" if removed else ""),
        "",
        "| 代码 | 名称 | 行业 | 3 年年化 ROE | 收盘价 | 历史倍数保守锚 | 买入成本上限 |",
        "|---|---|---|---:|---:|---:|---:|",
    ]
    by_symbol = {row["symbol"]: row for row in report.get("rows", [])}
    for symbol in removed:
        row = by_symbol.get(symbol) or {}
        metrics = row.get("metrics") or {}
        quality = metrics.get("quality") or {}
        valuation = row.get("valuation") or {}
        lines.append(
            f"| {symbol} | {row.get('name') or '-'} | {row.get('industry') or '-'} "
            f"| {_format_ratio(quality.get('roe_3y'))} "
            f"| {_format_money(metrics.get('close'))} "
            f"| {_format_money(valuation.get('v_low'))} "
            f"| {_format_money(valuation.get('buy_cost_ceiling'))} |"
        )
    if not removed:
        lines.append("| - | - | - | - | - | - | - |")
    return lines


def _trend_markdown(report: dict) -> list[str]:
    """The value-trap section: what the gate is, what it removed."""
    params = report.get("params") or {}
    removed = report.get("value_trap_removed_symbols") or []
    lines = [
        "",
        "## 价值陷阱闸门",
        "",
        "> 低 PE 不等于便宜：营收连跌、净利连跌、资产负债率上行三者任一命中，"
        "即判为「便宜是因为生意本身在失去价值」。",
        "",
        f"- 连续下滑阈值：{params.get('decline_streak_years')} 年"
        + ("（未启用）" if params.get("decline_streak_years") is None else ""),
        f"- 资产负债率上升上限：{_format_ratio(params.get('max_debt_ratio_rise_3y'))}"
        + ("（未启用）" if params.get("max_debt_ratio_rise_3y") is None else ""),
        f"- 命中陷阱闸门的标的：{report.get('value_trap_removed_count', 0)} 只",
        f"- 其中因 ROE 不足、但成长达标而被成长路径放行的标的："
        f"{report.get('growth_path_count', 0)} 只",
        "",
    ]
    reason_counts = report.get("reason_counts") or {}
    lines += [
        "| 陷阱信号 | 证券数 |",
        "|---|---:|",
    ]
    for code, label in (
        ("net_profit_declining", "归母净利连年下滑"),
        ("revenue_declining", "营收连年下滑"),
        ("leverage_rising", "资产负债率上行"),
        ("trend_window_incomplete", "趋势窗口不完整（证据缺失，阻断）"),
    ):
        lines.append(f"| {label} | {reason_counts.get(code, 0)} |")
    if removed:
        preview = "、".join(removed[:20])
        more = f" 等 {len(removed)} 只" if len(removed) > 20 else ""
        lines += ["", f"被剔除标的（部分）：{preview}{more}"]
    return lines


def _veto_markdown(report: dict) -> list[str]:
    """The governance-veto section: which passing names a human rejected."""
    vetoes = report.get("governance_vetoes") or []
    if not vetoes:
        return []
    lines = [
        "",
        "## 人工治理否决",
        "",
        "> 以下标的通过了全部量化闸门，但基于管理层 / 治理事件的公开证据被人工否决，"
        "移出优先研究名单。否决是研究判断而非量化闸门，带证据可审计、可撤销。",
        "",
        "| 代码 | 名称 | 否决理由 | 否决日期 |",
        "|---|---|---|---|",
    ]
    for row in vetoes:
        veto = row.get("governance_veto") or {}
        lines.append(
            f"| {row['symbol']} | {row.get('name') or veto.get('name') or '-'} | "
            f"{veto.get('reason') or '-'} | {veto.get('vetoed_at') or '-'} |"
        )
    for row in vetoes:
        veto = row.get("governance_veto") or {}
        heading = f"### {row['symbol']} {row.get('name') or veto.get('name') or ''}".rstrip()
        lines += ["", heading, "", f"- **事件**：{veto.get('event') or '-'}"]
        evidence = veto.get("evidence") or []
        if evidence:
            lines.append("- **证据来源**：")
            for url in evidence:
                lines.append(f"  - {url}")
    return lines


def _summary_markdown(report: dict) -> str:
    lines = [
        "# 价值投资安全边际选股报告",
        "",
        f"- 策略：`{report['strategy']}@{report['strategy_version']}`"
        f"（内核 `{report.get('kernel_version')}`）",
        f"- 锁定安全边际：`{report['required_margin']}`",
        f"- 行情交易日：{report['target_trade_date']}",
        f"- 人工治理判断日：{report.get('governance_as_of', '-')}",
        f"- 估值分布窗口：{report['coverage']['valuation_window_start']} ~ "
        f"{report['coverage']['valuation_window_end']}",
        f"- 年报期：{'、'.join(report['coverage']['annual_periods'])}",
        f"- 股票池：{report['universe_count']}；达标：{report['qualified_count']}；"
        f"{'治理否决：' + str(report['vetoed_count']) + '；' if report.get('vetoed_count') else ''}"
        f"优先研究名单：{report.get('active_shortlist_count', report['shortlist_count'])}；"
        f"递延：{report.get('active_deferred_count', report['deferred_count'])}",
        "",
        "> 本报告为只读研究输出，不产生权重、订单或收益承诺；"
        "`research_only=true / pit_verified=false / execution_eligible=false`。",
        "",
        "## 优先研究名单",
        "",
        "| # | 代码 | 名称 | 行业 | 收盘价 | PE | PB | 历史倍数保守锚 V_low | "
        "买入成本上限 | 安全边际 | 3 年年化 ROE | 3 年营收 CAGR | 3 年净利 CAGR | 准入路径 |",
        "|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for row in report.get("active_shortlist", report["shortlist"]):
        metrics = row["metrics"]
        valuation = row["valuation"]
        quality = metrics.get("quality") or {}
        trend = metrics.get("trend") or {}
        margin = valuation.get("gross_margin")
        margin_text = f"{float(margin) * 100:.1f}%" if margin is not None else "-"
        growth_path = metrics.get("growth_path")
        roe = quality.get("roe_3y")
        min_roe = metrics.get("min_roe_3y")
        roe_ok = (
            roe is not None and min_roe is not None and roe >= min_roe
        )
        growth_ok = growth_path == "qualified"
        if roe_ok and growth_ok:
            path = "ROE + 成长双路径"
        elif roe_ok:
            path = "ROE 路径"
        elif growth_ok:
            path = "成长路径"
        else:
            path = "-"
        lines.append(
            f"| {row['rank']} | {row['symbol']} | {row.get('name') or '-'} | "
            f"{row.get('industry') or '-'} | {_format_money(metrics.get('close'))} | "
            f"{_format_money(metrics.get('pe_current'))} | "
            f"{_format_money(metrics.get('pb_current'))} | "
            f"{_format_money(valuation.get('v_low'))} | "
            f"{_format_money(valuation.get('buy_cost_ceiling'))} | {margin_text} | "
            f"{_format_ratio(quality.get('roe_3y'))} | "
            f"{_format_ratio(trend.get('revenue_cagr_3y'))} | "
            f"{_format_ratio(trend.get('net_profit_cagr_3y'))} | {path} |"
        )
    if not report.get("active_shortlist", report["shortlist"]):
        lines.append(
            "| - | - | - | - | - | - | - | - | - | 本次没有达标标的 | - | - | - | - |"
        )

    lines += _veto_markdown(report)

    lines += [
        "",
        "## 递延（达标但未进入研究容量）",
        "",
        "| # | 代码 | 名称 | 行业 | 收盘价 | 历史倍数保守锚 | 买入成本上限 |",
        "|---|---|---|---|---:|---:|---:|",
    ]
    for row in report.get("active_deferred", report["deferred"]):
        metrics = row["metrics"]
        valuation = row["valuation"]
        lines.append(
            f"| {row['rank']} | {row['symbol']} | {row.get('name') or '-'} | "
            f"{row.get('industry') or '-'} | {_format_money(metrics.get('close'))} | "
            f"{_format_money(valuation.get('v_low'))} | "
            f"{_format_money(valuation.get('buy_cost_ceiling'))} |"
        )
    if not report.get("active_deferred", report["deferred"]):
        lines.append("| - | - | - | - | - | - | - |")

    lines += _quality_markdown(report)
    lines += _trend_markdown(report)
    lines += _price_only_markdown(report)

    lines += [
        "",
        "## 未通过原因统计",
        "",
        "| 原因 | 证券数 |",
        "|---|---:|",
    ]
    for code, count in sorted(
        report["reason_counts"].items(), key=lambda item: (-item[1], item[0])
    ):
        lines.append(f"| `{code}` | {count} |")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--as-of",
        type=date.fromisoformat,
        default=None,
        help="Completed trading day (ISO). Defaults to the latest one before today.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("outputs/value_investing_latest.json"),
        help="JSON report path; a Markdown summary is written next to it.",
    )
    args = parser.parse_args(argv)
    json_path, markdown_path = _report_paths(args.output)

    _ensure_registered()
    report = ValueInvestingScreenService().run(as_of=args.as_of)

    json_text = json.dumps(report, ensure_ascii=False, indent=2, default=str)
    markdown_text = _summary_markdown(report)
    _publish_report_files(
        json_path,
        markdown_path,
        json_text=json_text,
        markdown_text=markdown_text,
    )

    print(
        json.dumps(
            {
                "strategy": report["strategy"],
                "strategy_version": report["strategy_version"],
                "required_margin": report["required_margin"],
                "target_trade_date": report["target_trade_date"],
                "universe_count": report["universe_count"],
                "qualified_count": report["qualified_count"],
                "shortlist_count": report["shortlist_count"],
                "active_shortlist_count": report.get(
                    "active_shortlist_count", report["shortlist_count"]
                ),
                "vetoed_count": report.get("vetoed_count", 0),
                "active_shortlist_symbols": [
                    row["symbol"]
                    for row in report.get("active_shortlist", report.get("shortlist", []))
                ],
                "qualified_symbols": report["qualified_symbols"],
                "min_roe_3y": report.get("params", {}).get("min_roe_3y"),
                "price_gate_only_count": report.get("price_gate_only", {}).get(
                    "qualified_count"
                ),
                "removed_by_quality_gate": report.get("price_gate_only", {}).get(
                    "removed_by_quality_gate"
                ),
                "snapshot_hash": report["snapshot_hash"],
                "output": str(json_path),
                "markdown": str(markdown_path),
            },
            ensure_ascii=False,
            indent=2,
            default=str,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
