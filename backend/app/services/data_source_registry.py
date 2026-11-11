"""数据源注册表：项目能信哪些源、各自多高优先级。

为什么要有这张表
----------------
``raw.daily_bar.source_priority`` 早已存在，但每个采集服务都把自己的优先级
写死在调用处（40 / 50 / 80 各不相同），``metadata.data_source`` 则是**空表**。
结果是：没有任何地方能回答"这个数字是谁给的、这个源可不可信、两个源冲突时
听谁的"。

本服务把优先级收敛成一份注册表，并且是**唯一事实来源**：

- ``DATA_SOURCE_CATALOG`` 声明每个源的名称、类型、优先级与说明；
- 采集服务通过 :func:`resolve_source_priority` 取优先级，不再各自硬编码；
- 冲突对账时，优先级决定谁是权威源。

优先级约定
----------
**数字越小越权威**（与 ``raw.daily_bar.source_priority`` 既有语义一致）：
盘后包（40）是厂商落盘的收盘全量数据，实时接口（50）次之，兜底补录（80）
最次。跨厂商对照源（通达信 70）排在接口之后、兜底之前 —— 它参与对账但不
抢占主源。
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.repositories.system import DataSourceRepository


@dataclass(frozen=True)
class DataSourceSpec:
    """一个数据源的注册信息。"""

    source_name: str
    source_type: str
    priority: int
    description: str
    #: 是否参与跨源对账。只有真正独立的厂商才置 True —— 同一家公司的
    #: 三个接口共享同一个上游，互相"对账"证明不了任何东西。
    is_independent: bool = False


#: 项目已知的全部数据源。
#:
#: ``is_independent`` 标注这个源是否来自**独立的上游**：只有它们对账时才有
#: 交叉验证价值。三个同花顺源共享一个上游，即使数值完全一致也只说明同一个
#: 上游重复得一致。
DATA_SOURCE_CATALOG: tuple[DataSourceSpec, ...] = (
    DataSourceSpec(
        source_name="hithink_finance_dump",
        source_type="vendor",
        priority=40,
        description="同花顺盘后数据包（厂商落盘的收盘全量）",
        is_independent=False,
    ),
    DataSourceSpec(
        source_name="hithink_finance",
        source_type="api",
        priority=50,
        description="同花顺日线接口",
        is_independent=False,
    ),
    DataSourceSpec(
        source_name="hithink_finance_index",
        source_type="api",
        priority=50,
        description="同花顺指数日线接口",
        is_independent=False,
    ),
    DataSourceSpec(
        source_name="tdx_official",
        source_type="vendor",
        priority=70,
        description="通达信官网盘后包（独立厂商，作为跨源对照标尺）",
        is_independent=True,
    ),
    DataSourceSpec(
        source_name="tencent",
        source_type="api",
        priority=70,
        description=(
            "腾讯行情（独立厂商，估值对照源；提供真正的 TTM PE，"
            "东财 f9 是静态 PE 不可直接比较）"
        ),
        is_independent=True,
    ),
    DataSourceSpec(
        source_name="csv",
        source_type="csv",
        priority=40,
        description="人工 CSV 导入",
        is_independent=False,
    ),
    DataSourceSpec(
        source_name="manual",
        source_type="manual",
        priority=10,
        description="人工订正（最高优先级，且不被自动流程覆盖）",
        is_independent=False,
    ),
)


def resolve_source_priority(source_name: str) -> int:
    """返回某个源的优先级；未注册的源按最不可信处理。

    未注册的源拿到 ``DEFAULT_UNREGISTERED_PRIORITY`` 而不是 0 —— 一个刚接入
    的源不应该因为"没登记"就自动成为最权威的。
    """
    for spec in DATA_SOURCE_CATALOG:
        if spec.source_name == source_name:
            return spec.priority
    return DEFAULT_UNREGISTERED_PRIORITY


#: 未注册源的兜底优先级：比任何已知源都低。
DEFAULT_UNREGISTERED_PRIORITY = 90


class DataSourceRegistryService:
    """把 :data:`DATA_SOURCE_CATALOG` 写入 ``metadata.data_source``。"""

    def __init__(self, *, repository: DataSourceRepository | None = None) -> None:
        self.repository = repository or DataSourceRepository()

    def sync_catalog(self, db: Session) -> dict[str, int]:
        """注册所有已知源，返回 ``{source_name: priority}``。

        幂等：重复执行只更新登记信息，不产生重复行。
        """
        for spec in DATA_SOURCE_CATALOG:
            self.repository.upsert(
                db,
                source_name=spec.source_name,
                source_type=spec.source_type,
                priority=spec.priority,
                is_active=True,
                description=spec.description,
                config={"is_independent": spec.is_independent},
            )
        db.commit()
        return {spec.source_name: spec.priority for spec in DATA_SOURCE_CATALOG}

    def independent_source_names(self, db: Session) -> set[str]:
        """返回真正独立上游的源名集合。

        对账只应该在这些源之间做；同一家公司的多个接口互相印证没有意义。
        """
        registered = {
            row.source_name: row
            for row in self.repository.list(db)
        }
        return {
            name
            for name, row in registered.items()
            if (row.config or {}).get("is_independent")
        }
