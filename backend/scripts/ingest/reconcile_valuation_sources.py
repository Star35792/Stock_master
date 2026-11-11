"""Cross-source valuation reconciliation between Eastmoney and Tencent.

Both vendors are queried at the *same moment*, so the comparison is between
two opinions about the same market state rather than between two different
days.  This is what makes the verdicts meaningful.

The caliber rules live in :mod:`app.services.valuation_reconciliation_rules`.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.data_sources.eastmoney import EastmoneyDataSource  # noqa: E402
from app.data_sources.tencent import TencentQuoteDataSource  # noqa: E402
from app.services.valuation_reconciliation_rules import (  # noqa: E402
    PeCaliber,
    ValuationComparison,
    compare_market_cap,
    compare_pb,
    compare_pe,
    compare_price,
)


@dataclass
class CrossSourceReport:
    """Aggregate outcome of one cross-source comparison."""

    compared: int = 0
    field_stats: dict[str, dict[str, int]] = field(default_factory=dict)
    conflicts: list[dict[str, object]] = field(default_factory=list)
    collisions: list[dict[str, object]] = field(default_factory=list)

    def record(self, name: str, comparison: ValuationComparison) -> None:
        stats = self.field_stats.setdefault(
            name,
            {"match": 0, "conflict": 0, "not_comparable": 0, "coverage_gap": 0},
        )
        stats[comparison.value] += 1


def _decimal(value: object) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        number = Decimal(str(value))
    except Exception:
        return None
    return number if number.is_finite() else None


def main() -> int:
    eastmoney = EastmoneyDataSource()
    tencent = TencentQuoteDataSource()

    print("=== 抓取东财当前估值（clist 分页）===")
    em_rows: list[dict[str, object]] = []
    for _page, _total, items, _raw in eastmoney.iter_valuation_snapshot_pages(
        limit=10000
    ):
        em_rows.extend(items)
    print(f"  东财 {len(em_rows)} 行")

    codes = [str(row.get("f12")) for row in em_rows if row.get("f12")]
    print(f"=== 抓取腾讯当前报价（每批 {tencent.batch_size}）===")
    quotes = tencent.fetch_quotes(codes)
    print(f"  腾讯 {len(quotes)} 只")

    report = CrossSourceReport()
    for row in em_rows:
        code = str(row.get("f12"))
        payload = quotes.get(code)
        if payload is None:
            report.record("close", ValuationComparison.COVERAGE_GAP)
            continue
        report.compared += 1

        em_close = _decimal(row.get("f2"))
        tx_close = _decimal(payload.get("price"))
        em_pb = _decimal(row.get("f23"))
        tx_pb = _decimal(payload.get("pb"))
        em_pe = _decimal(row.get("f9"))
        tx_pe_static = _decimal(payload.get("pe_static"))
        tx_pe_ttm = _decimal(payload.get("pe_ttm"))
        em_cap = _decimal(row.get("f20"))
        tx_cap_yi = _decimal(payload.get("mcap_yi"))

        verdicts = {
            "close": compare_price(em_close, tx_close),
            "pb": compare_pb(em_pb, tx_pb),
            "market_cap": compare_market_cap(
                em_cap,
                tx_cap_yi * Decimal("100000000") if tx_cap_yi is not None else None,
            ),
            # Eastmoney f9 is a static PE; only the static pair is comparable.
            "static_pe": compare_pe(
                em_pe,
                tx_pe_static,
                left_caliber=PeCaliber.STATIC,
                right_caliber=PeCaliber.STATIC,
            ),
        }
        for name, verdict in verdicts.items():
            report.record(name, verdict.comparison)

        # The two PE calibers are deliberately *not* compared with each other;
        # the check below confirms that they really are different quantities.
        ttm_verdict = compare_pe(
            em_pe,
            tx_pe_ttm,
            left_caliber=PeCaliber.STATIC,
            right_caliber=PeCaliber.TTM,
        )
        report.record("pe_across_caliber", ttm_verdict.comparison)

        if verdicts["close"].is_conflict:
            # A wildly different close with a different name means the code
            # spaces collided: the vendor quoted a company under an index code.
            report.collisions.append(
                {
                    "code": code,
                    "eastmoney_close": str(em_close),
                    "tencent_close": str(tx_close),
                    "tencent_name": payload.get("name"),
                }
            )
        if verdicts["pb"].is_conflict:
            report.conflicts.append(
                {
                    "code": code,
                    "name": payload.get("name"),
                    "eastmoney_pb": str(em_pb),
                    "tencent_pb": str(tx_pb),
                    "reason": verdicts["pb"].note,
                }
            )

    print("\n=== 跨源对账结果（同一时刻）===")
    print(f"  可比标的: {report.compared}")
    print("\n  分字段统计:")
    for name, stats in report.field_stats.items():
        total = sum(stats.values())
        print(
            f"    {name:18s} 一致={stats['match']:>5} 冲突={stats['conflict']:>5} "
            f"不可比={stats['not_comparable']:>5} 覆盖缺口={stats['coverage_gap']:>5}"
            f"  (一致率 {stats['match']/total:.2%})"
        )

    if report.collisions:
        print(f"\n  代码空间碰撞 {len(report.collisions)} 个（指数代码被当个股报价）:")
        for item in report.collisions[:8]:
            print(f"    {item['code']} em_close={item['eastmoney_close']} "
                  f"tx_close={item['tencent_close']} tx_name={item['tencent_name']}")

    if report.conflicts:
        print(f"\n  PB 冲突 {len(report.conflicts)} 个（净资产分母口径差，前 8）:")
        for item in report.conflicts[:8]:
            print(f"    {item['code']} {item['name']}: em_pb={item['eastmoney_pb']} "
                  f"tx_pb={item['tencent_pb']} ({item['reason']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
