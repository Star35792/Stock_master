"""把申万行业变迁史写入 ``market.security_industry_history``。

现状与目标
----------
``market.security_industry_history`` 现有 5,579 行，每标的恰好 1 条，
``effective_from`` 等于上市日、``effective_to`` 全为 NULL、``industry`` 与
当前标签逐字相同——等价于假设「行业从未变更」。这是**假历史**：任何按目标
交易日做 as-of 匹配的读取，拿到的都是今天的行业标签。

本服务用申万归属变动表把每个标的展开成真实的生效区间：
第 i 条记录的 ``effective_to`` = 第 i+1 条的 ``effective_from``，最后一条开放。

标签名映射
----------
申万只发布 6 位行业代码，不发布中文名；而项目的 ``market.security.industry``
存的就是申万二级行业名。所以映射用**当前标签反推**：取「当前归属该二级
代码」的标的在项目里的行业名众数。众数占比过低的代码标记为未解析，回退成
「申万-<二级代码>」以保持可追溯，而不是静默编一个名字。

代码歧义
--------
申万表只给 6 位代码、不带交易所，而 6 位代码在 SH/SZ/BJ 存在碰撞
（实测 108 个）。碰撞码无法从申万侧消歧，只能用「首次计入日期 = 上市日」
来匹配；仍无法确定的**跳过并计入报告**，绝不猜。

写入方式
--------
用 ORM 删除 + 插入（不裸 SQL），并只对申万覆盖到的标的动手；申万未覆盖的
标的保留原有「当前标签回填」行不动，它们的 ``source_name`` 已经把它标成
回填，不会被误当成时点事实。
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.data_sources.sw_industry import SwIndustryDataSource, SwIndustryRecord
from app.models.market import Security, SecurityIndustryHistory
from app.repositories.market import SecurityRepository

#: 本次写入的来源标识。与 ``current_label_backfill_*`` 明确区分。
SOURCE_NAME = "sw_industry_history"

#: 二级代码 → 名称映射时，众数占比低于此值就判定为「未解析」。
MIN_LABEL_AGREEMENT = 0.5

#: upsert 每批的行数，避免单条语句参数过多。
UPSERT_BATCH_SIZE = 2000


def _chunks(items: list[Any], size: int) -> Iterator[list[Any]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


@dataclass(frozen=True)
class SymbolUniverse:
    """一个标的在 ``market.security`` 里的信息。"""

    symbol: str
    raw_symbol: str
    exchange: str
    industry: str | None
    list_date: date | None


@dataclass
class SwIndustryPlan:
    """写库前的完整计划（可先 dry-run 审查再落库）。"""

    intervals: list[dict] = field(default_factory=list)
    replaced_symbols: set[str] = field(default_factory=set)
    unresolved_level2: dict[str, str] = field(default_factory=dict)
    ambiguous_codes: dict[str, list[str]] = field(default_factory=dict)
    unmatched_codes: set[str] = field(default_factory=set)
    covered_symbols: set[str] = field(default_factory=set)

    @property
    def fallback_label_intervals(self) -> int:
        """用「申万-<代码>」回退标签的区间数。

        这些区间落在 2021 版分类之前的遗留行业上，中文名无从反推。数量必须
        可见：它直接决定了「历史行业口径」与「当前行业口径」不可比的范围。
        """

        return sum(
            1
            for interval in self.intervals
            if interval["industry"].startswith("申万-")
        )


@dataclass(frozen=True)
class SwIndustryCollectionResult:
    """采集结果。"""

    source_records: int
    written_intervals: int
    replaced_symbols: int
    kept_backfill_symbols: int
    unresolved_level2: int
    ambiguous_codes: int
    unmatched_codes: int
    fallback_label_intervals: int
    dry_run: bool


class SwIndustryHistoryCollectionService:
    """采集申万行业变迁史并写入时点行业事实表。"""

    def __init__(
        self,
        *,
        data_source: SwIndustryDataSource | None = None,
        security_repository: SecurityRepository | None = None,
    ) -> None:
        self.data_source = data_source or SwIndustryDataSource()
        self.security_repository = security_repository or SecurityRepository()

    def collect(
        self,
        db: Session,
        *,
        insecure: bool = False,
        dry_run: bool = False,
        refresh: bool = False,
    ) -> SwIndustryCollectionResult:
        records = self.data_source.fetch_history(insecure=insecure, refresh=refresh)
        plan = self._build_plan(db, records)

        if not dry_run:
            self._apply(db, plan)

        return SwIndustryCollectionResult(
            source_records=len(records),
            written_intervals=len(plan.intervals),
            replaced_symbols=len(plan.replaced_symbols),
            kept_backfill_symbols=self._kept_backfill_count(db, plan),
            unresolved_level2=len(plan.unresolved_level2),
            ambiguous_codes=len(plan.ambiguous_codes),
            unmatched_codes=len(plan.unmatched_codes),
            fallback_label_intervals=plan.fallback_label_intervals,
            dry_run=dry_run,
        )

    # ------------------------------------------------------------------ 计划

    def _build_plan(
        self, db: Session, records: list[SwIndustryRecord]
    ) -> SwIndustryPlan:
        universe = self._load_universe(db)
        by_raw: dict[str, list[SymbolUniverse]] = defaultdict(list)
        for item in universe:
            by_raw[item.raw_symbol].append(item)

        # 1) 消歧：6 位代码可能对应多个交易所的标的。
        resolved: dict[str, SymbolUniverse] = {}
        ambiguous: dict[str, list[str]] = {}
        unmatched: set[str] = set()
        for record in records:
            candidates = by_raw.get(record.symbol_code, [])
            if not candidates:
                unmatched.add(record.symbol_code)
                continue
            if len(candidates) == 1:
                resolved[record.symbol_code] = candidates[0]
                continue
            # 多候选：用「最早计入日期」与上市日的吻合度消歧。
            first_dates = {
                code: min(
                    item.effective_from
                    for item in records
                    if item.symbol_code == code
                )
                for code in {record.symbol_code}
            }
            first_date = first_dates[record.symbol_code]
            exact = [
                item
                for item in candidates
                if item.list_date is not None and item.list_date == first_date
            ]
            if len(exact) == 1:
                resolved[record.symbol_code] = exact[0]
            else:
                ambiguous[record.symbol_code] = sorted(
                    item.symbol for item in candidates
                )

        # 2) 二级代码 → 中文名：当前标签众数反推。
        current_level2 = self._current_level2(records)
        history_level2 = {
            record.level2_code for record in records if record.symbol_code in resolved
        }
        label_map = self._build_label_map(current_level2, resolved, history_level2)

        # 3) 组装区间。
        by_symbol: dict[str, list[SwIndustryRecord]] = defaultdict(list)
        for record in records:
            item = resolved.get(record.symbol_code)
            if item is None:
                continue
            by_symbol[item.symbol].append(record)

        intervals: list[dict] = []
        replaced: set[str] = set()
        for symbol, items in by_symbol.items():
            item = resolved[items[0].symbol_code]
            intervals.extend(self._intervals_for(symbol, items, item, label_map))
            replaced.add(symbol)

        return SwIndustryPlan(
            intervals=intervals,
            replaced_symbols=replaced,
            unresolved_level2={
                code: name
                for code, name in label_map.items()
                if name.startswith("申万-")
            },
            ambiguous_codes=ambiguous,
            unmatched_codes=unmatched,
            covered_symbols=set(by_symbol),
        )

    def _load_universe(self, db: Session) -> list[SymbolUniverse]:
        rows = db.execute(
            select(
                Security.symbol,
                Security.raw_symbol,
                Security.exchange,
                Security.industry,
                Security.list_date,
            )
        ).all()
        return [
            SymbolUniverse(
                symbol=row[0],
                raw_symbol=str(row[1] or ""),
                exchange=str(row[2] or ""),
                industry=row[3],
                list_date=row[4],
            )
            for row in rows
            if row[0] and row[1]
        ]

    def _current_level2(
        self, records: list[SwIndustryRecord]
    ) -> dict[str, str]:
        """每个标的**当前**所属的二级行业代码。"""

        latest: dict[str, SwIndustryRecord] = {}
        for record in records:
            current = latest.get(record.symbol_code)
            if current is None or record.effective_from >= current.effective_from:
                latest[record.symbol_code] = record
        return {code: record.level2_code for code, record in latest.items()}

    def _build_label_map(
        self,
        current_level2: dict[str, str],
        resolved: dict[str, SymbolUniverse],
        history_level2: set[str],
    ) -> dict[str, str]:
        """二级代码 → 中文名（用当前标签众数反推）。

        申万只发布代码、不发布中文名，所以名字只能从项目里已有的行业标签反推。
        但**反推只对「当前仍有人在用」的二级代码可行**：2021 版分类把「银行」
        拆成了银行Ⅰ/Ⅱ/Ⅲ，历史区间里的老代码（实测 194 个历史二级码中有 63 个
        是 2021 版之前的遗留码）当前没有任何标的在用，无从反推。

        这些遗留码回退成「申万-<二级代码>」：**保留可追溯的编码，而不是静默
        编一个名字**。它们是真实的历史分类（上市初期确实属于那个老行业），
        丢掉会让时间线出现空洞；保留则行业分组里会出现一个稀疏的伪行业，
        这在口径上是诚实的。

        众数占比过低的代码同样回退——名字不确定就不该硬套。
        """

        votes: dict[str, Counter[str]] = defaultdict(Counter)
        for raw_code, level2 in current_level2.items():
            item = resolved.get(raw_code)
            if item is None or not item.industry:
                continue
            votes[level2][item.industry] += 1

        label_map: dict[str, str] = {}
        for level2 in history_level2:
            counter = votes.get(level2)
            if counter:
                name, count = counter.most_common(1)[0]
                total = sum(counter.values())
                if total and count / total >= MIN_LABEL_AGREEMENT:
                    label_map[level2] = name
                    continue
            label_map[level2] = f"申万-{level2}"
        return label_map

    def _intervals_for(
        self,
        symbol: str,
        items: list[SwIndustryRecord],
        universe: SymbolUniverse,
        label_map: dict[str, str],
    ) -> list[dict]:
        ordered = sorted(items, key=lambda item: item.effective_from)

        # 先钳位到上市日，再按钳位后的日期去重。
        #
        # 顺序很关键：申万历史可能早于本项目记录的上市日（退市/借壳/口径差），
        # 钳位会把这些记录压到同一天。实测 30 只标的（如 002259.SZ、
        # 300214.SZ）因此产生同一个 effective_from，直接写会撞唯一索引
        # ``uq_security_industry_history_from``。
        #
        # 压到同一天时保留**原始计入日期最晚**的那条：那是上市时点实际生效的
        # 分类；更早的那条区间被压缩成零长度，本来就不该存在。
        clamped: list[tuple[date, SwIndustryRecord]] = []
        for item in ordered:
            effective_from = item.effective_from
            if universe.list_date is not None and effective_from < universe.list_date:
                effective_from = universe.list_date
            if clamped and clamped[-1][0] == effective_from:
                clamped[-1] = (effective_from, item)
            else:
                clamped.append((effective_from, item))

        intervals: list[dict] = []
        for index, (effective_from, item) in enumerate(clamped):
            effective_to = clamped[index + 1][0] if index + 1 < len(clamped) else None
            if effective_to is not None and effective_to <= effective_from:
                continue
            intervals.append(
                {
                    "symbol": symbol,
                    "industry": label_map.get(item.level2_code, f"申万-{item.level2_code}"),
                    "effective_from": effective_from,
                    "effective_to": effective_to,
                    "source_name": SOURCE_NAME,
                    "level2_code": item.level2_code,
                }
            )
        return intervals

    # ------------------------------------------------------------------ 写入

    def _apply(self, db: Session, plan: SwIndustryPlan) -> None:
        """写入计划区间。

        为什么不用「先 DELETE 再 INSERT」
        ------------------------------
        DuckDB 的唯一索引在**同一事务内不会释放已删除的键**：删掉一行后立刻
        插入同键的新行，必然报 ``Duplicate key ... violates unique constraint``，
        即使表里已经查不到那一行。跨事务才正常。

        这不是本项目的偶发脏数据，是 DuckDB 的索引行为。因此写入改成两步、
        两笔事务，**任何一步都不做「同事务 delete + 同键 insert」**：

        1. **upsert 计划区间**（``ON CONFLICT (symbol, effective_from) DO
           UPDATE``）。唯一索引 ``uq_security_industry_history_from`` 保证
           每个标的的每个生效日只有一行；冲突时覆盖行业名与区间终点。
        2. **清理孤儿行**：申万覆盖到的标的里，旧「当前标签回填」行的
           ``effective_from`` 可能不在计划内（例如申万历史晚于上市日），
           留着它等于在早期区间继续用今天的标签——那正是本次要消除的前视
           偏差。这些行按「不在计划键集合内」判定后单独删除。

        两步都可重复执行（幂等）：重复跑不会产生重复行，也不会丢数据。
        """

        plan_keys = {
            (interval["symbol"], interval["effective_from"])
            for interval in plan.intervals
        }

        # 第一步：upsert 计划区间。
        for chunk in _chunks(plan.intervals, UPSERT_BATCH_SIZE):
            statement = insert(SecurityIndustryHistory).values(
                [
                    {
                        "symbol": item["symbol"],
                        "industry": item["industry"],
                        "effective_from": item["effective_from"],
                        "effective_to": item["effective_to"],
                        "source_name": item["source_name"],
                    }
                    for item in chunk
                ]
            )
            statement = statement.on_conflict_do_update(
                index_elements=[
                    SecurityIndustryHistory.symbol,
                    SecurityIndustryHistory.effective_from,
                ],
                set_={
                    "industry": statement.excluded.industry,
                    "effective_to": statement.excluded.effective_to,
                    "source_name": statement.excluded.source_name,
                    "updated_at": func.now(),
                },
            )
            db.execute(statement)
        db.commit()

        # 第二步：清理申万覆盖标的下的孤儿行。
        orphans: list[SecurityIndustryHistory] = []
        for symbol in sorted(plan.replaced_symbols):
            rows = db.scalars(
                select(SecurityIndustryHistory).where(
                    SecurityIndustryHistory.symbol == symbol
                )
            ).all()
            for row in rows:
                if (row.symbol, row.effective_from) not in plan_keys:
                    orphans.append(row)
        for row in orphans:
            db.delete(row)
        db.commit()

    def _kept_backfill_count(self, db: Session, plan: SwIndustryPlan) -> int:
        """申万未覆盖、仍保留「当前标签回填」行的标的数。"""

        rows = db.execute(
            select(SecurityIndustryHistory.symbol).where(
                SecurityIndustryHistory.source_name != SOURCE_NAME
            )
        ).all()
        return len({row[0] for row in rows})
