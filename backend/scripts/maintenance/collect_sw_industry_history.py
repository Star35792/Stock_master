"""采集申万行业变迁史，写入 ``market.security_industry_history``。

这把「当前标签回填」升级成真正的时点行业事实：现有 5,579 行每标的恰好 1 条、
``effective_to`` 全 NULL，等价于假设行业从未变更；申万变动表能给出真实的
生效区间（实测 5,938 只标的中 2,024 只历史上变更过，最多 10 次）。

用法::

    cd backend && python -m scripts.maintenance.collect_sw_industry_history --dry-run
    cd backend && python -m scripts.maintenance.collect_sw_industry_history
    cd backend && python -m scripts.maintenance.collect_sw_industry_history --insecure

``--insecure`` 会跳过 TLS 证书校验。该站点（swsresearch.com）的证书链在
本机/部分企业网环境下不完整，``certifi`` 也覆盖不到；akshare 官方库对同一
站点的接口同样使用 ``verify=False``。只有在确认数据来源可信时才用。

源文件会落到 ``data/cache/sw_industry/`` 并默认复用：这样 **dry-run 审查的
计划与实跑执行的计划读的是同一份快照**。``--refresh`` 强制重新下载。代理
502 是常态，抓取自带退避重试；网络失败但本地有缓存时回退到缓存。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.config.settings import settings  # noqa: E402
from app.database.session import engine  # noqa: E402
from app.services.industry_history_collection import (  # noqa: E402
    SOURCE_NAME,
    SwIndustryHistoryCollectionService,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Collect SW industry change history into security_industry_history",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="build the plan and report it without touching the database",
    )
    parser.add_argument(
        "--insecure",
        action="store_true",
        help="skip TLS certificate verification (the SW site's chain is incomplete here)",
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="ignore the local cache and re-download the source file",
    )
    parser.add_argument("--db", help="override the control-plane database path")
    args = parser.parse_args()

    db_path = Path(args.db or settings.metadata_db_path)
    if args.db:
        from sqlalchemy import create_engine

        bind = create_engine(f"duckdb:///{db_path}")
    else:
        bind = engine

    print("=== 申万行业变迁史采集 ===")
    print(f"数据库  : {db_path}")
    print(f"来源标识: {SOURCE_NAME}")
    print(f"证书校验: {'关闭（--insecure）' if args.insecure else '开启'}")
    print(f"缓存策略: {'强制刷新' if args.refresh else '优先复用本地快照'}")

    with Session(bind) as db:
        result = SwIndustryHistoryCollectionService().collect(
            db,
            insecure=args.insecure,
            dry_run=args.dry_run,
            refresh=args.refresh,
        )
        print("\n=== 结果 ===")
        print(f"源表记录数   : {result.source_records}")
        print(f"写入区间数   : {result.written_intervals}")
        print(f"替换标的数   : {result.replaced_symbols}")
        print(f"保留回填标的 : {result.kept_backfill_symbols}")
        print(f"遗留码未命名 : {result.unresolved_level2}")
        print(f"回退标签区间 : {result.fallback_label_intervals}")
        print(f"代码歧义     : {result.ambiguous_codes}")
        print(f"未匹配代码   : {result.unmatched_codes}")
        if args.dry_run:
            print("\n--dry-run：未做任何修改")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
