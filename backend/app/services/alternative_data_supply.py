"""``market.alternative_data_snapshot`` 的供给台账与健康判据。

背景
----
这张表曾被笼统记为「六族断供」。2026-11-11 实测把根因拆成了**四类完全不同的
东西**，处理方式也完全不同，混在一起就无法排期：

1. **端点失效**——端点本身长期返回错误或无数据。实测*不存在*这一类：
   同花顺情绪类端点（涨停池 / 热股 / 涨停速览 / 异动 / 龙虎榜 / 连板梯队）与
   东财资金流端点，在 2026-11-11 全部实测可用。
2. **从未被触发**——采集服务存在且可用，但没有任何调度入口调用它。
   ``raw.vendor_snapshot`` 里每个 alternative dataset 恰好 1 条、全部是
   2026-09-18，就是「跑过一次之后再没跑过」的物证。这是断供的主因。
3. **空集被当成成功**——归一化后 0 行时批次仍被标记 ``success``。
   **「返回空集」与「端点不可用」必须分开判定**，否则端点一旦返回空，
   断供就会伪装成正常，排查时只剩下「怎么突然没数据了」的谜题。
4. **从未接通**——因子声明了输入依赖，但根本没有任何采集实现。

时点语义
--------
每个 dataset 的**快照日期含义不同**，这是把采集数据接进因子时的硬约束：

* ``same_day_close`` —— T 日收盘后即知（涨停池、热股、异动、资金流）；
* ``next_session_publish`` —— T 日交易的事实在 T+1 才公布（龙虎榜），
  快照日期必须记成**公布日**而不是交易日，否则就是前视；
* ``history_series`` —— 端点自带历史序列（连板梯队）；
* ``unresolved`` —— 尚未确认，不得进任何闸门。
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum


class SupplyStatus(StrEnum):
    """某个 dataset 的供给状态。"""

    #: 已接通，且实测能取到非空数据。
    CONNECTED = "connected"
    #: 已接通，但归一化后没有任何行（端点返回了空集或全部被过滤）。
    EMPTY = "empty"
    #: 因子声明了依赖，但没有任何采集实现。
    NOT_CONNECTED = "not_connected"
    #: 上游已断供，补不了。
    BLOCKED = "blocked"


class PitSemantics(StrEnum):
    """快照日期的时点语义。"""

    SAME_DAY_CLOSE = "same_day_close"
    NEXT_SESSION_PUBLISH = "next_session_publish"
    HISTORY_SERIES = "history_series"
    UNRESOLVED = "unresolved"


@dataclass(frozen=True)
class DatasetSupplySpec:
    """一个 dataset 的供给登记条目。"""

    dataset: str
    #: 依赖该 dataset 的因子族；``None`` 表示它不服务于六族因子。
    factor_family: str | None
    #: 产出该 dataset 的端点（人类可读标识）。
    endpoint: str | None
    #: 归一化后的合理最小行数。低于此值视为空集异常。
    min_expected_rows: int
    pit_semantics: PitSemantics
    status: SupplyStatus
    #: 不可供给的原因（``NOT_CONNECTED`` / ``BLOCKED`` 时必填）。
    blocker: str | None = None
    notes: str = ""


#: 未在台账内登记的 dataset 使用的兜底下限。
DEFAULT_MIN_EXPECTED_ROWS = 1


DATASET_SUPPLY_REGISTRY: dict[str, DatasetSupplySpec] = {
    spec.dataset: spec
    for spec in (
        # ---- 同花顺情绪族（实测端点可用）----
        DatasetSupplySpec(
            dataset="limit_up_pool",
            factor_family="sentiment",
            endpoint="hithink /api/a-share/limit-up-pool",
            min_expected_rows=10,
            pit_semantics=PitSemantics.SAME_DAY_CLOSE,
            status=SupplyStatus.CONNECTED,
            notes="涨停池；T 日收盘后可知，炸板池（failed_limit_up）尚无端点",
        ),
        DatasetSupplySpec(
            dataset="limit_up_ladder",
            factor_family="sentiment",
            endpoint="hithink /api/a-share/limit-up-ladder",
            min_expected_rows=10,
            pit_semantics=PitSemantics.HISTORY_SERIES,
            status=SupplyStatus.CONNECTED,
            notes="连板梯队；端点自带历史序列，是唯一可回补的端点",
        ),
        DatasetSupplySpec(
            dataset="hot_stock",
            factor_family="sentiment",
            endpoint="hithink /api/a-share/hot-stock-list",
            min_expected_rows=10,
            pit_semantics=PitSemantics.SAME_DAY_CLOSE,
            status=SupplyStatus.CONNECTED,
        ),
        DatasetSupplySpec(
            dataset="skyrocket",
            factor_family="sentiment",
            endpoint="hithink /api/a-share/skyrocket-list",
            min_expected_rows=10,
            pit_semantics=PitSemantics.SAME_DAY_CLOSE,
            status=SupplyStatus.CONNECTED,
        ),
        DatasetSupplySpec(
            dataset="dragon_tiger",
            factor_family="sentiment",
            endpoint="hithink /api/a-share/dragon-tiger-list",
            min_expected_rows=1,
            pit_semantics=PitSemantics.NEXT_SESSION_PUBLISH,
            status=SupplyStatus.CONNECTED,
            notes="龙虎榜；T 日交易 T+1 公布，快照日期必须记公布日",
        ),
        DatasetSupplySpec(
            dataset="dragon_tiger_hot_money",
            factor_family="sentiment",
            endpoint="hithink /api/a-share/dragon-tiger-list?board_type=hot_money",
            min_expected_rows=1,
            pit_semantics=PitSemantics.NEXT_SESSION_PUBLISH,
            status=SupplyStatus.CONNECTED,
            notes=(
                "游资席位榜；端点用 board_type 选择榜单，board_type=all 的响应里 "
                "hot_money_items 恒为空——早期把它判成「端点无内容」是误判。"
                "数据为席位口径，与总榜 dragon_tiger 口径不同，不得混用于同一因子"
            ),
        ),
        DatasetSupplySpec(
            dataset="failed_limit_up",
            factor_family="sentiment",
            endpoint=None,
            min_expected_rows=1,
            pit_semantics=PitSemantics.SAME_DAY_CLOSE,
            status=SupplyStatus.NOT_CONNECTED,
            blocker="未找到炸板池端点；涨停池端点不含炸板列表",
            notes="limit_up_failure_rate 因子依赖它",
        ),
        # ---- 东财资金流族（实测端点可用）----
        DatasetSupplySpec(
            dataset="eastmoney_main_fund_rank",
            factor_family="capital_flow",
            endpoint="eastmoney push2 主力资金排行",
            min_expected_rows=50,
            pit_semantics=PitSemantics.SAME_DAY_CLOSE,
            status=SupplyStatus.CONNECTED,
        ),
        DatasetSupplySpec(
            dataset="eastmoney_industry_fund_flow",
            factor_family=None,
            endpoint="eastmoney push2 行业板块资金流",
            min_expected_rows=20,
            pit_semantics=PitSemantics.SAME_DAY_CLOSE,
            status=SupplyStatus.CONNECTED,
        ),
        DatasetSupplySpec(
            dataset="eastmoney_concept_fund_flow",
            factor_family=None,
            endpoint="eastmoney push2 概念板块资金流",
            min_expected_rows=20,
            pit_semantics=PitSemantics.SAME_DAY_CLOSE,
            status=SupplyStatus.CONNECTED,
        ),
        # ---- 未接通的因子依赖 ----
        DatasetSupplySpec(
            dataset="macro_indicator",
            factor_family="macro",
            endpoint=None,
            min_expected_rows=1,
            pit_semantics=PitSemantics.UNRESOLVED,
            status=SupplyStatus.NOT_CONNECTED,
            blocker="宏观指标族无采集实现",
            notes="宏观因子族全部 0 值",
        ),
        DatasetSupplySpec(
            dataset="policy_event",
            factor_family="policy",
            endpoint=None,
            min_expected_rows=1,
            pit_semantics=PitSemantics.UNRESOLVED,
            status=SupplyStatus.NOT_CONNECTED,
            blocker="政策事件需要可判定的事件抽取，现无可信端点",
            notes="policy_event_score 依赖它",
        ),
        DatasetSupplySpec(
            dataset="earnings_revision",
            factor_family="earnings_revision",
            endpoint=None,
            min_expected_rows=1,
            pit_semantics=PitSemantics.UNRESOLVED,
            status=SupplyStatus.BLOCKED,
            blocker=(
                "一致预期修正属前瞻性预测，没有首次可得时间；"
                "按项目 PIT 铁律一律落 strategy.external_observation，不入 market 闸门"
            ),
        ),
        DatasetSupplySpec(
            dataset="profit_guidance",
            factor_family="earnings_revision",
            endpoint="eastmoney datacenter 业绩预告",
            min_expected_rows=1,
            pit_semantics=PitSemantics.NEXT_SESSION_PUBLISH,
            status=SupplyStatus.NOT_CONNECTED,
            blocker="端点可用但未接入；业绩预告有公告日，可按公告日切 PIT",
            notes="a-stock-data §14.1 earnings_forecast 可提供",
        ),
        DatasetSupplySpec(
            dataset="profit_warning",
            factor_family="earnings_revision",
            endpoint="eastmoney datacenter 业绩预告（预亏子集）",
            min_expected_rows=1,
            pit_semantics=PitSemantics.NEXT_SESSION_PUBLISH,
            status=SupplyStatus.NOT_CONNECTED,
            blocker="端点可用但未接入；需与 profit_guidance 合并采集",
        ),
        DatasetSupplySpec(
            dataset="margin_financing",
            factor_family="capital_flow",
            endpoint="eastmoney datacenter 融资融券明细",
            min_expected_rows=1,
            pit_semantics=PitSemantics.NEXT_SESSION_PUBLISH,
            status=SupplyStatus.NOT_CONNECTED,
            blocker="端点可用但未接入；两融为 T+1 披露",
            notes="a-stock-data §4.1 margin_trading 可提供",
        ),
        DatasetSupplySpec(
            dataset="northbound_flow",
            factor_family="capital_flow",
            endpoint=None,
            min_expected_rows=1,
            pit_semantics=PitSemantics.UNRESOLVED,
            status=SupplyStatus.BLOCKED,
            blocker=(
                "东财全系北向数据自 2024-08 起上游断供（净买额字段返回 NaN/0）；"
                "同花顺深股通分钟序列同样不可靠，权威口径须用 HKEX 日统计"
            ),
        ),
        DatasetSupplySpec(
            dataset="etf_flow_industry",
            factor_family="capital_flow",
            endpoint=None,
            min_expected_rows=1,
            pit_semantics=PitSemantics.UNRESOLVED,
            status=SupplyStatus.NOT_CONNECTED,
            blocker="ETF 行业资金流需自建口径（ETF 份额 × 行业映射），尚无实现",
        ),
        DatasetSupplySpec(
            dataset="institutional_crowding",
            factor_family="crowding",
            endpoint=None,
            min_expected_rows=1,
            pit_semantics=PitSemantics.UNRESOLVED,
            status=SupplyStatus.NOT_CONNECTED,
            blocker="机构拥挤度需自建口径（机构持股集中度），尚无实现",
        ),
        # ---- 市场/目录类（非六族依赖，但同表同源）----
        DatasetSupplySpec(
            dataset="anomaly",
            factor_family=None,
            endpoint="hithink /api/a-share/anomaly-list",
            min_expected_rows=10,
            pit_semantics=PitSemantics.SAME_DAY_CLOSE,
            status=SupplyStatus.CONNECTED,
        ),
        DatasetSupplySpec(
            dataset="index_catalog_industry",
            factor_family=None,
            endpoint="hithink /api/a-share/ths-indices?tag=industry",
            min_expected_rows=50,
            pit_semantics=PitSemantics.SAME_DAY_CLOSE,
            status=SupplyStatus.CONNECTED,
        ),
        DatasetSupplySpec(
            dataset="index_catalog_cn_concept",
            factor_family=None,
            endpoint="hithink /api/a-share/ths-indices?tag=cn_concept",
            min_expected_rows=50,
            pit_semantics=PitSemantics.SAME_DAY_CLOSE,
            status=SupplyStatus.CONNECTED,
        ),
        DatasetSupplySpec(
            dataset="index_catalog_region",
            factor_family=None,
            endpoint="hithink /api/a-share/ths-indices?tag=region",
            min_expected_rows=10,
            pit_semantics=PitSemantics.SAME_DAY_CLOSE,
            status=SupplyStatus.CONNECTED,
        ),
        DatasetSupplySpec(
            dataset="index_catalog_tszs",
            factor_family=None,
            endpoint="hithink /api/a-share/ths-indices?tag=tszs",
            min_expected_rows=10,
            pit_semantics=PitSemantics.SAME_DAY_CLOSE,
            status=SupplyStatus.CONNECTED,
        ),
    )
}


class EmptyDatasetError(RuntimeError):
    """端点返回了空集（或数据被全部过滤），却被当成成功。"""


def minimum_rows_for(dataset: str) -> int:
    """返回某 dataset 的合理最小行数。"""

    spec = DATASET_SUPPLY_REGISTRY.get(dataset)
    return spec.min_expected_rows if spec else DEFAULT_MIN_EXPECTED_ROWS


def check_dataset_supply(dataset: str, row_count: int) -> None:
    """归一化后行数低于合理下限时抛 :class:`EmptyDatasetError`。

    这是「空集 ≠ 端点可用」的落地点：空集必须让采集批次显式失败或标记为
    partial，而不是被 ``success`` 掩盖过去。
    """

    minimum = minimum_rows_for(dataset)
    if row_count < minimum:
        raise EmptyDatasetError(
            f"dataset {dataset!r} normalized to {row_count} row(s), "
            f"expected at least {minimum}; the endpoint returned an empty set "
            "or every row was filtered out"
        )


def supply_status_of(dataset: str) -> SupplyStatus:
    """返回某 dataset 的登记状态；未登记视为未接通。"""

    spec = DATASET_SUPPLY_REGISTRY.get(dataset)
    return spec.status if spec else SupplyStatus.NOT_CONNECTED


def registered_datasets() -> Iterable[str]:
    """返回台账登记的全部 dataset 名。"""

    return tuple(DATASET_SUPPLY_REGISTRY)


def blocked_datasets() -> tuple[DatasetSupplySpec, ...]:
    """返回因上游断供而补不了的 dataset。"""

    return tuple(
        spec
        for spec in DATASET_SUPPLY_REGISTRY.values()
        if spec.status is SupplyStatus.BLOCKED
    )


def not_connected_datasets() -> tuple[DatasetSupplySpec, ...]:
    """返回从未接通的 dataset。"""

    return tuple(
        spec
        for spec in DATASET_SUPPLY_REGISTRY.values()
        if spec.status is SupplyStatus.NOT_CONNECTED
    )
