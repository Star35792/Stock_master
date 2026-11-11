from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    Index,
    Numeric,
    Sequence,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.models.common import TimestampMixin


class StrategyDefinition(TimestampMixin, Base):
    """版本化的内置策略定义目录。

    为什么需要这张表
    ----------------
    项目原本没有「策略定义」这个持久化实体：``app/strategies/templates.py``
    里的策略是硬编码的 Python 类，``required_margin`` 之类的参数只作为每次
    请求的入参存在。这与
    ``aiDoc/strategy/20260922-1314-price-value-margin-strategy.md`` 第 4 节
    「``required_margin`` 在训练/研究阶段预先确定并锁定版本」的要求冲突：
    参数没有被登记，就无法证明某次筛选用的是哪个口径。

    本表照抄 ``factor.factor_definition`` 的形态（``name`` + ``version``
    唯一键、``parameters_schema`` 描述参数契约、``status`` 表达生命周期），
    但语义不同：

    * ``factor_definition`` 是**因子目录**，其行会被批量画像与因子池分级器
      消费；策略定义放进去会出现一个假因子。
    * ``training_artifact`` 是**滚动窗口级的训练输入溯源锚点**，主键是
      ``window_id``，一行一窗口，无法承载可反复调用的策略定义。

    物理落库位置是主库（``Base``）而不是研究库（``ResearchBase``）：策略
    定义必须在每次调用时被读取，而研究库在研究脚本写入期间会返回 409。
    """

    __tablename__ = "strategy_definition"
    __table_args__ = (
        UniqueConstraint("name", "version"),
        CheckConstraint(
            "status in ('active', 'retired')",
            name="ck_strategy_definition_status",
        ),
        CheckConstraint(
            "required_margin is null or (required_margin > 0 and required_margin < 1)",
            name="ck_strategy_definition_required_margin_range",
        ),
        Index("ix_strategy_definition_name_version", "name", "version"),
        Index("ix_strategy_definition_active", "is_active", "status"),
        {"schema": "strategy"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger,
        Sequence("strategy_strategy_definition_id_seq"),
        server_default=text("nextval('strategy_strategy_definition_id_seq')"),
        primary_key=True,
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    version: Mapped[str] = mapped_column(String(50), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(100))
    description: Mapped[str | None] = mapped_column(Text)
    category: Mapped[str] = mapped_column(String(50), nullable=False)
    #: 参数契约（类型、约束、默认值）。参数**取值**放在 ``parameters``
    #: 与 ``required_margin`` 两处具名列里，契约 JSON 只描述形状，
    #: 避免 JSON 里出现两份可能互相矛盾的数字。
    parameters_schema: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict
    )
    #: 锁定阈值（JSON）的**取值**。
    #: The values are stored as strings for decimals (``"0.08"``) and as
    #: integers for counts.  The column is nullable because it was added to
    #: existing databases through the compatibility bridge, which cannot add
    #: a NOT NULL column; readers treat a missing value as "no extra
    #: thresholds".
    parameters: Mapped[dict[str, Any] | None] = mapped_column(
        JSON, nullable=True, default=dict
    )
    #: 锁定参数名清单。锁定参数只能改版本号，不能就地改值。
    locked_parameters: Mapped[list[str]] = mapped_column(
        JSON, nullable=False, default=list
    )
    #: 所需安全边际，锁定在定义行上。``buy_cost_ceiling = V_low * (1 - required_margin)``。
    required_margin: Mapped[Decimal | None] = mapped_column(Numeric(12, 10))
    #: 策略消费的数据来源（``schema.table.column`` 形态）。
    inputs: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    #: 定义本身的生命周期：``active`` 当前有效；``retired`` 被取代或已知有误，
    #: 保留证据供审计但不再被调用。
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'active'")
    )
