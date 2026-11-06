from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    Index,
    Integer,
    Sequence,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.models.common import TimestampMixin


class FactorDefinition(TimestampMixin, Base):
    __tablename__ = "factor_definition"
    __table_args__ = (
        UniqueConstraint("name", "version"),
        CheckConstraint(
            "frequency in ('daily', 'weekly', 'monthly')",
            name="ck_factor_definition_frequency",
        ),
        CheckConstraint(
            "window_days is null or window_days > 0",
            name="ck_factor_definition_window_positive",
        ),
        Index("ix_factor_definition_name_version", "name", "version"),
        Index("ix_factor_definition_active_frequency", "is_active", "frequency"),
        Index("ix_factor_definition_category", "category"),
        {"schema": "factor"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger, Sequence("factor_factor_definition_id_seq"),
        server_default=text("nextval('factor_factor_definition_id_seq')"), primary_key=True
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(100))
    description: Mapped[str | None] = mapped_column(Text)
    category: Mapped[str] = mapped_column(String(50), nullable=False)
    frequency: Mapped[str] = mapped_column(String(20), nullable=False)
    inputs: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    applicable_security_types: Mapped[list[str]] = mapped_column(
        JSON,
        nullable=False,
        default=lambda: ["stock", "etf", "index"],
    )
    window_days: Mapped[int | None] = mapped_column(Integer)
    version: Mapped[str] = mapped_column(String(50), nullable=False)
    dependencies: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    parameters_schema: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    # Point-in-time status of the factor definition:
    #
    # * ``point_in_time`` -- every input was knowable on the trade date itself;
    # * ``look_ahead``   -- at least one input leaks future information;
    # * ``unknown``      -- not yet audited, treated as unusable by the pool
    #   classifier (fail closed).
    #
    # The column defaults to ``unknown`` on purpose: a factor that was never
    # audited must not silently enter the core pool.
    pit_status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'unknown'")
    )
    # 定义本身的生命周期状态：
    #
    # * ``active``  -- 当前有效版本，可参与因子池分级；
    # * ``retired`` -- 已被取代或已知有误，只保留证据供审计，硬冻结。
    #
    # 与 ``is_active`` 的区别：``is_active`` 表示「是否参与批量画像」，
    # ``status`` 表示「这个定义是否还是活的证据」。价值因子的证据由专用
    # 脚本产出，因此 ``is_active=False`` 但 ``status='active'``。
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'active'")
    )
