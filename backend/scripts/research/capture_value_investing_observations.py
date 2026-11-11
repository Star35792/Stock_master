"""Capture external observations into the value-investing research card.

The observations come from the MCP connectors (NeoData for company profiles
and vendor financial indicators, the WeStock connector for sell-side
consensus, ratings and research notes).  This script does **not** call them:
an agent session calls the connectors and writes the raw payloads to a JSON
file, and this script persists them into the research database and renders
the card.

The separation matters.  A connector call is a live network request whose
answer changes over time; the persisted observation is a dated, auditable
snapshot.  Running the screen twice must produce the same result, and the
card must not depend on a live connection at screen time.

Usage:
    python -m scripts.research.capture_value_investing_observations \\
        --observations observations.json \\
        --screen outputs/value_investing_20261109.json \\
        --output outputs/value_investing_card_20261109.md
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select

from app.database.research_session import (
    ResearchSessionLocal,
    initialize_research_database,
)
from app.models.strategy import ExternalObservation
from app.services.value_investing_research_card import (
    build_cards,
    card_to_markdown,
)

#: Bump when the shape of a stored payload changes.
PAYLOAD_VERSION = 1


def _parse_date(value: Any, fallback: date) -> date:
    if isinstance(value, str):
        for fmt in ("%Y%m%d", "%Y-%m-%d"):
            try:
                return datetime.strptime(value, fmt).date()
            except ValueError:
                continue
    if isinstance(value, int):
        try:
            return datetime.strptime(str(value), "%Y%m%d").date()
        except ValueError:
            pass
    return fallback


def _capture_at(value: Any) -> datetime:
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return datetime.now(UTC)
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=UTC)
        return parsed
    return datetime.now(UTC)


def _upsert(
    db,
    *,
    symbol: str,
    source: str,
    observation_type: str,
    as_of_date: date,
    captured_at: datetime,
    payload: dict[str, Any],
) -> bool:
    existing = db.execute(
        select(ExternalObservation).where(
            ExternalObservation.symbol == symbol,
            ExternalObservation.source == source,
            ExternalObservation.observation_type == observation_type,
            ExternalObservation.as_of_date == as_of_date,
        )
    ).scalar_one_or_none()
    if existing is None:
        db.add(
            ExternalObservation(
                symbol=symbol,
                source=source,
                observation_type=observation_type,
                as_of_date=as_of_date,
                captured_at=captured_at,
                payload=payload,
                payload_version=PAYLOAD_VERSION,
            )
        )
        return True
    existing.captured_at = captured_at
    existing.payload = payload
    existing.payload_version = PAYLOAD_VERSION
    return False


def main() -> None:
    parser = argparse.ArgumentParser(
        description="把外部连接器观测写入研究卡（只读研究产物，不参与闸门）"
    )
    parser.add_argument(
        "--observations",
        type=Path,
        required=True,
        help="连接器原始观测 JSON（agent 侧调用连接器后落盘）",
    )
    parser.add_argument(
        "--screen",
        type=Path,
        help="筛选结果 JSON；提供时只为名单内标的建卡",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="研究卡 Markdown 输出路径",
    )
    parser.add_argument(
        "--json-output",
        type=Path,
        help="研究卡 JSON 输出路径",
    )
    parser.add_argument(
        "--include-trap-examples",
        action="store_true",
        help=(
            "同时为被价值陷阱闸门剔除的标的建卡。"
            "闸门剔除是系统自己的判断，研究卡要能被用来复核这个判断，"
            "否则剔除就只剩一个看不见的数字。"
        ),
    )
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    raw = json.loads(args.observations.read_text(encoding="utf-8"))
    default_capture = _capture_at(raw.get("captured_at"))

    screen_report: dict[str, Any] = {}
    if args.screen is not None:
        screen_report = json.loads(args.screen.read_text(encoding="utf-8"))
    allowed: set[str] | None = None
    names: dict[str, str] = {}
    trap_removed: set[str] = set()
    if screen_report:
        allowed = set(screen_report.get("qualified_symbols") or [])
        for row in screen_report.get("rows") or []:
            if row.get("name"):
                names[row["symbol"]] = row["name"]
        trap_removed = set(screen_report.get("value_trap_removed_symbols") or [])
    if args.include_trap_examples and allowed is not None:
        # Only the securities the trap gate is the *sole* reason to reject.
        # A security that fails the price gate anyway tells us nothing about
        # whether the trap gate is right; these are the ones where its
        # verdict actually changed the outcome.
        price_failures = {
            "margin_of_safety_not_met",
            "pb_above_ceiling",
            "pe_above_ceiling",
            "pe_history_insufficient",
            "pb_history_insufficient",
            "pe_fair_anchor_unavailable",
            "pb_fair_anchor_unavailable",
        }
        trap_codes = {
            "net_profit_declining",
            "revenue_declining",
            "leverage_rising",
        }
        trap_removed = {
            row["symbol"]
            for row in screen_report.get("rows") or []
            if set(row.get("excluded_reasons") or []) & trap_codes
            and not (set(row.get("excluded_reasons") or []) & price_failures)
        }
        allowed = allowed | trap_removed

    initialize_research_database()
    db = ResearchSessionLocal()
    created = 0
    updated = 0
    try:
        for record in raw.get("observations") or []:
            symbol = str(record.get("symbol") or "").strip()
            source = str(record.get("source") or "").strip()
            observation_type = str(record.get("observation_type") or "").strip()
            payload = record.get("payload")
            if not symbol or not source or not observation_type:
                continue
            if not isinstance(payload, dict):
                continue
            if allowed is not None and symbol not in allowed:
                continue
            as_of_date = _parse_date(
                record.get("as_of_date"), default_capture.date()
            )
            captured_at = _capture_at(record.get("captured_at")) if record.get(
                "captured_at"
            ) else default_capture
            if _upsert(
                db,
                symbol=symbol,
                source=source,
                observation_type=observation_type,
                as_of_date=as_of_date,
                captured_at=captured_at,
                payload=payload,
            ):
                created += 1
            else:
                updated += 1
        db.commit()

        symbols = sorted(allowed) if allowed is not None else sorted(
            {
                str(record.get("symbol"))
                for record in raw.get("observations") or []
                if record.get("symbol")
            }
        )
        cards = build_cards(db, symbols, names=names)

        if args.output is not None:
            lines = [
                "# 价值投资研究卡（外部信息）",
                "",
                "> 本文件是**研究备忘**，不是闸门输入。机构一致预期是前瞻性预测，",
                "> 不是已实现事实；外部数据不参与策略的任何筛选判定。",
                "",
                f"- 生成时间：{default_capture.isoformat()}",
                f"- 覆盖标的：{len(cards)}",
                "",
            ]
            if trap_removed:
                lines += [
                    "## 达标标的",
                    "",
                ]
            for card in cards:
                if card.symbol in trap_removed:
                    continue
                lines.append(card_to_markdown(card))
            if trap_removed:
                lines += [
                    "## 被价值陷阱闸门剔除的标的",
                    "",
                    "> 这些标的因营收/净利连年下滑或资产负债率上行被判为价值陷阱。",
                    "> 列出它们是为了让剔除可以被复核，而不是只留一个数字。",
                    "",
                ]
                for card in cards:
                    if card.symbol not in trap_removed:
                        continue
                    lines.append(card_to_markdown(card))
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text("\n".join(lines), encoding="utf-8")

        if args.json_output is not None:
            args.json_output.parent.mkdir(parents=True, exist_ok=True)
            args.json_output.write_text(
                json.dumps(
                    {
                        "generated_at": default_capture.isoformat(),
                        "research_only": True,
                        "gate_inputs": False,
                        "forward_looking": True,
                        "cards": [card.to_dict() for card in cards],
                    },
                    ensure_ascii=False,
                    indent=2,
                    default=str,
                ),
                encoding="utf-8",
            )

        print(
            json.dumps(
                {
                    "created": created,
                    "updated": updated,
                    "cards": len(cards),
                    "symbols": symbols,
                    "markdown": str(args.output) if args.output else None,
                    "json": str(args.json_output) if args.json_output else None,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    finally:
        db.close()


if __name__ == "__main__":
    main()
