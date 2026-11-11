"""六族因子时点性审计：查看/落库。

用法::

    python -m scripts.factors.audit_six_family_pit_status            # 只报告
    python -m scripts.factors.audit_six_family_pit_status --apply    # 写入库

报告会把三件事摆在一起：**审计台账的判定**、**判定依据**、**库里当前的值**。
三者不一致的地方就是需要人看的地方——审计台账是代码里的既定事实，库里的值
是执行结果，执行结果不听话时应当修执行，而不是偷偷改台账。
"""

from __future__ import annotations

import argparse

from app.database.session import SessionLocal
from app.factors.pit_audit_catalog import (
    SIX_FAMILY_PIT_AUDIT,
    annotate_six_family_pit_status,
)
from app.repositories.factor import FactorDefinitionRepository

_STATUS_ORDER = ("point_in_time", "look_ahead", "unknown")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Audit or apply the point-in-time status of six-family factors."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="把台账判定写入 factor.factor_definition.pit_status（默认只报告）",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    repo = FactorDefinitionRepository()

    with SessionLocal() as db:
        summary = None
        if args.apply:
            summary = annotate_six_family_pit_status(db)
            db.commit()

        print("=== 六族因子时点性审计台账 ===")
        for entry in SIX_FAMILY_PIT_AUDIT:
            definitions, _ = repo.list(db, name=entry.factor_name, limit=100)
            current = sorted({definition.pit_status for definition in definitions})
            current_text = ",".join(current) if current else "(无定义行)"
            versions = ",".join(sorted({d.version for d in definitions})) or "-"
            flag = "" if entry.pit_status in current else "  <<< 与库内不一致"
            print(f"\n[{entry.pit_status}] {entry.factor_name}  versions={versions}")
            print(f"  datasets : {', '.join(entry.datasets)}")
            print(f"  依据     : {entry.reason}")
            print(f"  库内当前 : {current_text}{flag}")

        if summary is not None:
            by_status = summary["factors_by_status"]
            print("\n=== 写入结果 ===")
            for status in _STATUS_ORDER:
                print(f"  {status:16s} {by_status.get(status, 0)} 个因子")
            print(f"  更新定义行: {summary['rows_updated']}")
            if summary["missing"]:
                print(f"  缺定义行  : {', '.join(summary['missing'])}")
        else:
            print("\n（未加 --apply，仅报告）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
