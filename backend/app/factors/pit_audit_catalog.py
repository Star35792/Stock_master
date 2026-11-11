"""六族因子的时点性（PIT）审计台账。

为什么需要它
------------
``pit_status`` 是因子池分级器的**硬门槛**，只有三种语义：

* ``point_in_time`` —— 输入在目标交易日之前即已知；
* ``look_ahead``   —— 至少一项输入泄漏未来信息，**硬冻结**（任何池都进不去）；
* ``unknown``      —— 未完成审计，不得进入核心池（fail-closed 默认值）。

六族因子（sentiment / macro / policy / earnings_revision / capital_flow /
crowding）的定义行此前**全部**是 ``unknown``，于是分级器对它们一律
fail-closed：数据补齐了也进不了核心池。

本台账把「凭什么算 point_in_time」写成代码而不是靠人手改库，并且**逐因子
记录依据**。依据只有两类：

1. **数据语义** —— 快照记的是什么日期、什么时候公布；
2. **消费规则** —— 因子取值时是否强制 ``snapshot_date < 目标交易日``。

没有依据的因子一律留在 ``unknown``，不靠「目前没发现问题」来放行。
``look_ahead`` 与 ``unknown`` 也不是同一件事：前者是**判定有毒**（任何池都
不能进），后者是**尚未判定**（只挡核心池，观察池仍可留）。
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.repositories.factor import FactorDefinitionRepository

#: 时点性状态取值。与 ``app.factors.evidence_catalog`` 同名常量语义一致。
PIT_POINT_IN_TIME = "point_in_time"
PIT_LOOK_AHEAD = "look_ahead"
PIT_UNKNOWN = "unknown"


@dataclass(frozen=True)
class PitAuditEntry:
    """一条时点性审计结论。"""

    #: 因子名。
    factor_name: str
    #: 判定结果，取值同上面的三个常量。
    pit_status: str
    #: 该因子的输入数据集（用于复核依据）。
    datasets: tuple[str, ...]
    #: 判定依据。必须能回答「凭什么」。
    reason: str


# 六族服务里统一的取值规则：只接受严格早于目标交易日的快照行。
_SNAPSHOT_RULE = "因子取值只接受 snapshot_date < 目标交易日的快照行"

SIX_FAMILY_PIT_AUDIT: tuple[PitAuditEntry, ...] = (
    # ---------------- sentiment ----------------
    PitAuditEntry(
        factor_name="hot_rank_score",
        pit_status=PIT_POINT_IN_TIME,
        datasets=("hot_stock",),
        reason=(
            "热股榜是收盘后快照，采集记 snapshot_date=该榜单对应的交易日；"
            f"{_SNAPSHOT_RULE}，故 T 日取到的正是 T-1 日收盘后的榜单。"
        ),
    ),
    PitAuditEntry(
        factor_name="hot_heat",
        pit_status=PIT_POINT_IN_TIME,
        datasets=("hot_stock",),
        reason=(
            "热股榜收盘后快照，snapshot_date=榜单对应交易日；"
            f"{_SNAPSHOT_RULE}。"
        ),
    ),
    PitAuditEntry(
        factor_name="hot_rank_change",
        pit_status=PIT_POINT_IN_TIME,
        datasets=("hot_stock",),
        reason=(
            "热股榜收盘后快照，snapshot_date=榜单对应交易日；"
            f"{_SNAPSHOT_RULE}，且取的是相邻两个历史快照的差分。"
        ),
    ),
    PitAuditEntry(
        factor_name="limit_up_strength",
        pit_status=PIT_POINT_IN_TIME,
        datasets=("limit_up_pool",),
        reason=(
            "涨停池收盘后发布，snapshot_date=该池对应交易日；"
            f"{_SNAPSHOT_RULE}。"
        ),
    ),
    PitAuditEntry(
        factor_name="limit_up_breadth",
        pit_status=PIT_POINT_IN_TIME,
        datasets=("limit_up_pool",),
        reason=(
            "涨停池收盘后发布，snapshot_date=该池对应交易日；"
            f"{_SNAPSHOT_RULE}。分母取当日价格宇宙，不引入未来信息。"
        ),
    ),
    PitAuditEntry(
        factor_name="dragon_tiger_net_buy_ratio",
        pit_status=PIT_POINT_IN_TIME,
        datasets=("dragon_tiger",),
        reason=(
            "龙虎榜 T 日交易、T+1 公布，采集记 snapshot_date=交易日；"
            f"{_SNAPSHOT_RULE}，故最早在 T+1 取用 —— 恰是公布日当天，不前视。"
            "游资席位榜（dragon_tiger_hot_money）是**席位口径**，已排除在本因子之外。"
        ),
    ),
    PitAuditEntry(
        factor_name="consecutive_limit_up_height",
        pit_status=PIT_POINT_IN_TIME,
        datasets=("limit_up_ladder",),
        reason=(
            "连板梯队端点自带历史序列，每行 snapshot_date=该梯队对应的交易日；"
            f"{_SNAPSHOT_RULE}，取到的都是历史值。"
        ),
    ),
    PitAuditEntry(
        factor_name="market_sentiment_score",
        pit_status=PIT_POINT_IN_TIME,
        datasets=("hot_stock", "skyrocket", "limit_up_pool"),
        reason=(
            "三个输入都是收盘后快照（热股榜 / 火箭榜 / 涨停池），"
            f"各自 snapshot_date=对应交易日；{_SNAPSHOT_RULE}。"
        ),
    ),
    PitAuditEntry(
        factor_name="sentiment_temperature_percentile",
        pit_status=PIT_POINT_IN_TIME,
        datasets=("hot_stock", "skyrocket", "limit_up_pool"),
        reason=(
            "三个输入都是收盘后快照，各自 snapshot_date=对应交易日；"
            f"{_SNAPSHOT_RULE}。历史分位只用截止目标交易日的序列。"
        ),
    ),
    PitAuditEntry(
        factor_name="limit_up_failure_rate",
        pit_status=PIT_POINT_IN_TIME,
        datasets=("failed_limit_up", "limit_up_pool"),
        reason=(
            "炸板池（limit-break-pool）端点已接通并实测落库：2026-10-20 落库 29 条，"
            "与当日 limit-up-statistics 的 open_stock_count=29 交叉验算一致。"
            "炸板池是**收盘后快照**（涨停后又打开），snapshot_date=该池对应交易日；"
            f"{_SNAPSHOT_RULE}。"
        ),
    ),
    PitAuditEntry(
        factor_name="overheat_reversal_signal",
        pit_status=PIT_POINT_IN_TIME,
        datasets=("hot_stock", "skyrocket", "limit_up_pool", "failed_limit_up"),
        reason=(
            "四个输入都是收盘后快照（热股榜 / 火箭榜 / 涨停池 / 炸板池），"
            f"各自 snapshot_date=对应交易日；{_SNAPSHOT_RULE}。"
        ),
    ),
    # ---------------- capital_flow ----------------
    PitAuditEntry(
        factor_name="main_fund_net_inflow",
        pit_status=PIT_POINT_IN_TIME,
        datasets=("eastmoney_main_fund_rank",),
        reason=(
            "东财主力资金榜收盘后发布，snapshot_date=该榜对应交易日；"
            f"{_SNAPSHOT_RULE}。"
        ),
    ),
    PitAuditEntry(
        factor_name="main_fund_net_inflow_ratio",
        pit_status=PIT_POINT_IN_TIME,
        datasets=("eastmoney_main_fund_rank",),
        reason=(
            "分子取主力资金榜（收盘后快照，snapshot_date=对应交易日），"
            f"分母取当日成交额；{_SNAPSHOT_RULE}。"
        ),
    ),
    PitAuditEntry(
        factor_name="main_fund_rank_score",
        pit_status=PIT_POINT_IN_TIME,
        datasets=("eastmoney_main_fund_rank",),
        reason=(
            "主力资金榜收盘后发布，snapshot_date=该榜对应交易日；"
            f"{_SNAPSHOT_RULE}。"
        ),
    ),
    PitAuditEntry(
        factor_name="main_fund_inflow_change_5",
        pit_status=PIT_POINT_IN_TIME,
        datasets=("eastmoney_main_fund_rank",),
        reason=(
            "主力资金榜收盘后发布，snapshot_date=该榜对应交易日；"
            f"{_SNAPSHOT_RULE}，差分取的是相邻历史快照。"
        ),
    ),
    PitAuditEntry(
        factor_name="main_fund_inflow_percentile_20",
        pit_status=PIT_POINT_IN_TIME,
        datasets=("eastmoney_main_fund_rank",),
        reason=(
            "主力资金榜收盘后发布，snapshot_date=该榜对应交易日；"
            f"{_SNAPSHOT_RULE}，历史分位只用截止目标交易日的序列。"
        ),
    ),
    PitAuditEntry(
        factor_name="northbound_flow_ratio",
        pit_status=PIT_UNKNOWN,
        datasets=("northbound_flow",),
        reason=(
            "北向资金上游自 2024-08 断供（权威口径须 HKEX），既无数据也无法验证 "
            "as-of 语义；fail-closed 保持 unknown。"
        ),
    ),
    PitAuditEntry(
        factor_name="margin_financing_change_20",
        pit_status=PIT_UNKNOWN,
        datasets=("margin_financing",),
        reason=(
            "两融数据集从未采集（market 与 raw 两层都没有记录），"
            "无法验证 as-of 语义；fail-closed 保持 unknown。"
        ),
    ),
    PitAuditEntry(
        factor_name="etf_flow_industry_20",
        pit_status=PIT_UNKNOWN,
        datasets=("etf_flow_industry",),
        reason=(
            "ETF 行业资金流数据集从未采集，无法验证 as-of 语义；"
            "fail-closed 保持 unknown。"
        ),
    ),
    PitAuditEntry(
        factor_name="institutional_crowding_score",
        pit_status=PIT_UNKNOWN,
        datasets=("institutional_crowding",),
        reason=(
            "机构拥挤度数据集从未采集，无法验证 as-of 语义；"
            "fail-closed 保持 unknown。"
        ),
    ),
    # ---------------- crowding ----------------
    PitAuditEntry(
        factor_name="crowding_score",
        pit_status=PIT_POINT_IN_TIME,
        datasets=(
            "turnover_percentile_120",
            "abnormal_volume_pressure",
            "market_sentiment_score",
        ),
        reason=(
            "三个成分因子本身都时点安全：换手率分位与异常成交量压力只用当日及历史"
            "价量；市场情绪分位只用收盘后快照（见 sentiment 族）。"
            "crowding_score 按成分因子的 trade_date 对齐，不做二次时间平移。"
        ),
    ),
    # ---------------- macro ----------------
    PitAuditEntry(
        factor_name="rate_trend_10y",
        pit_status=PIT_UNKNOWN,
        datasets=("macro_indicator",),
        reason=(
            "宏观指标数据集从未采集（market 与 raw 两层都没有记录）。"
            "即便补上，也要先确认**发布滞后**口径：社融/M1/M2 等在次月 10–15 日才发布，"
            "若 snapshot_date 记的是观测期而非公布日，按 snapshot_date < 目标交易日取用"
            "就是前视。口径未确认前 fail-closed。"
        ),
    ),
    PitAuditEntry(
        factor_name="yield_curve_slope",
        pit_status=PIT_UNKNOWN,
        datasets=("macro_indicator",),
        reason=(
            "宏观指标数据集从未采集；发布滞后口径（观测期 vs 公布日）未确认，"
            "fail-closed 保持 unknown。"
        ),
    ),
    PitAuditEntry(
        factor_name="social_financing_impulse",
        pit_status=PIT_UNKNOWN,
        datasets=("macro_indicator",),
        reason=(
            "宏观指标数据集从未采集；社融的发布滞后尤其显著（次月 10–15 日），"
            "口径未确认前 fail-closed。"
        ),
    ),
    PitAuditEntry(
        factor_name="m1_m2_gap",
        pit_status=PIT_UNKNOWN,
        datasets=("macro_indicator",),
        reason=(
            "宏观指标数据集从未采集；发布滞后口径未确认，fail-closed 保持 unknown。"
        ),
    ),
    PitAuditEntry(
        factor_name="usd_cnh_trend",
        pit_status=PIT_UNKNOWN,
        datasets=("macro_indicator",),
        reason=(
            "宏观指标数据集从未采集；发布滞后口径未确认，fail-closed 保持 unknown。"
        ),
    ),
    PitAuditEntry(
        factor_name="commodity_inflation_proxy",
        pit_status=PIT_UNKNOWN,
        datasets=("macro_indicator",),
        reason=(
            "宏观指标数据集从未采集；发布滞后口径未确认，fail-closed 保持 unknown。"
        ),
    ),
    PitAuditEntry(
        factor_name="credit_spread_change",
        pit_status=PIT_UNKNOWN,
        datasets=("macro_indicator",),
        reason=(
            "宏观指标数据集从未采集；发布滞后口径未确认，fail-closed 保持 unknown。"
        ),
    ),
    # ---------------- policy ----------------
    PitAuditEntry(
        factor_name="policy_event_score",
        pit_status=PIT_UNKNOWN,
        datasets=("policy_event",),
        reason=(
            "政策事件数据集从未采集；且事件的「发生日」与「公布日」可能不同，"
            "落库记的是哪一个尚未确认，fail-closed 保持 unknown。"
        ),
    ),
    # ---------------- earnings_revision ----------------
    PitAuditEntry(
        factor_name="earnings_revision_1m",
        pit_status=PIT_LOOK_AHEAD,
        datasets=("earnings_revision",),
        reason=(
            "输入是分析师一致预期修正——**前瞻性预测**。项目 PIT 铁律：一致预期/"
            "评级/研报没有首次可得时间，一律不入 market 闸门，故硬冻结。"
        ),
    ),
    PitAuditEntry(
        factor_name="earnings_revision_breadth",
        pit_status=PIT_LOOK_AHEAD,
        datasets=("earnings_revision",),
        reason=(
            "输入是分析师一致预期修正——前瞻性预测，无首次可得时间，"
            "按项目 PIT 铁律硬冻结。"
        ),
    ),
    PitAuditEntry(
        factor_name="forecast_dispersion",
        pit_status=PIT_LOOK_AHEAD,
        datasets=("earnings_revision",),
        reason=(
            "输入是分析师一致预期（分歧度）——前瞻性预测，无首次可得时间，"
            "按项目 PIT 铁律硬冻结。"
        ),
    ),
    PitAuditEntry(
        factor_name="guidance_surprise",
        pit_status=PIT_UNKNOWN,
        datasets=("profit_guidance",),
        reason=(
            "业绩预告是**公司公告**，原则上公告日即可得日，PIT 可控；"
            "但本项目既无采集实现、也无公告日字段，落库记的日期语义无法审计，"
            "fail-closed 保持 unknown。"
        ),
    ),
    PitAuditEntry(
        factor_name="profit_warning_risk",
        pit_status=PIT_UNKNOWN,
        datasets=("profit_warning",),
        reason=(
            "业绩预警是**公司公告**的事实，公告日即可得日，PIT 可控；"
            "但同样没有采集实现与公告日字段，日期语义无法审计，"
            "fail-closed 保持 unknown。"
        ),
    ),
)


def annotate_six_family_pit_status(db: Session) -> dict[str, object]:
    """把台账里的时点性结论写进 ``factor.factor_definition.pit_status``。

    只改 ``pit_status``，不碰 ``is_active`` / ``status`` / 版本 / 描述。

    返回摘要::

        {
          "factors_by_status": {"point_in_time": n, "look_ahead": n, "unknown": n},
          "rows_updated": n,
          "missing": ["没有定义行的因子名", ...],
        }
    """
    repo = FactorDefinitionRepository()
    factors_by_status: dict[str, int] = {
        PIT_POINT_IN_TIME: 0,
        PIT_LOOK_AHEAD: 0,
        PIT_UNKNOWN: 0,
    }
    rows_updated = 0
    missing: list[str] = []

    for entry in SIX_FAMILY_PIT_AUDIT:
        definitions, _ = repo.list(db, name=entry.factor_name, limit=100)
        if not definitions:
            missing.append(entry.factor_name)
            continue
        factors_by_status[entry.pit_status] = factors_by_status.get(entry.pit_status, 0) + 1
        for definition in definitions:
            if definition.pit_status == entry.pit_status:
                continue
            definition.pit_status = entry.pit_status
            rows_updated += 1
    db.flush()

    return {
        "factors_by_status": factors_by_status,
        "rows_updated": rows_updated,
        "missing": missing,
    }
