from sqlalchemy import (
    JSON,
    BigInteger,
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


class DataSource(TimestampMixin, Base):
    __tablename__ = "data_source"
    __table_args__ = (
        UniqueConstraint("source_name"),
        CheckConstraint(
            "source_type in ('csv', 'api', 'akshare', 'qmt', 'manual', 'vendor')",
            name="ck_data_source_type",
        ),
        CheckConstraint("priority >= 0", name="ck_data_source_priority_non_negative"),
        Index("ix_data_source_active_priority", "is_active", "priority"),
        {"schema": "metadata"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger, Sequence("system_data_source_id_seq"),
        server_default=text("nextval('system_data_source_id_seq')"), primary_key=True
    )
    source_name: Mapped[str] = mapped_column(String(50), nullable=False)
    source_type: Mapped[str] = mapped_column(String(20), nullable=False)
    priority: Mapped[int] = mapped_column(Integer, nullable=False)
    is_active: Mapped[bool] = mapped_column(nullable=False, server_default=text("true"))
    description: Mapped[str | None] = mapped_column(Text)
    config: Mapped[dict | None] = mapped_column(JSON)
