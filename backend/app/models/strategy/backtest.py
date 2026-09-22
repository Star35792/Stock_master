from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    Sequence,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.models.common import CreatedAtMixin


class TrainingArtifact(CreatedAtMixin, Base):
    """Immutable record of the training inputs used for one walk-forward window.

    The artifact captures the factor specification and a content hash of the
    training-period factor observations.  It is created once per window and
    referenced by every backtest run that depends on it, giving the validation
    report a stable provenance anchor even before full artifact verification is
    implemented.
    """

    __tablename__ = "training_artifact"
    __table_args__ = (
        UniqueConstraint("window_id"),
        Index("ix_training_artifact_window_id", "window_id"),
        {"schema": "strategy"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger, Sequence("strategy_training_artifact_id_seq"), primary_key=True
    )
    window_id: Mapped[str] = mapped_column(String(200), nullable=False)
    strategy_name: Mapped[str] = mapped_column(String(100), nullable=False)
    factor_specs: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)
    training_start_date: Mapped[date] = mapped_column(Date, nullable=False)
    training_end_date: Mapped[date] = mapped_column(Date, nullable=False)
    test_start_date: Mapped[date] = mapped_column(Date, nullable=False)
    test_end_date: Mapped[date] = mapped_column(Date, nullable=False)
    training_input_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    code_version: Mapped[str] = mapped_column(String(100), nullable=False)
    data_version: Mapped[str] = mapped_column(String(100), nullable=False)


class BacktestRun(CreatedAtMixin, Base):
    __tablename__ = "backtest_run"
    __table_args__ = (
        CheckConstraint(
            "status in ('running', 'success', 'failed')",
            name="ck_backtest_run_status",
        ),
        CheckConstraint("initial_cash > 0", name="ck_backtest_run_initial_cash"),
        CheckConstraint("start_date <= end_date", name="ck_backtest_run_date_order"),
        Index("ix_backtest_run_strategy_started", "strategy_name", "started_at"),
        Index("ix_backtest_run_training_artifact", "training_artifact_id"),
        {"schema": "strategy"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger, Sequence("strategy_backtest_run_id_seq"), primary_key=True
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    strategy_name: Mapped[str] = mapped_column(String(100), nullable=False)
    parameters: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    code_version: Mapped[str] = mapped_column(String(100), nullable=False)
    data_version: Mapped[str] = mapped_column(String(100), nullable=False)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    initial_cash: Mapped[Decimal] = mapped_column(Numeric(24, 4), nullable=False)
    final_equity: Mapped[Decimal | None] = mapped_column(Numeric(24, 4))
    total_return: Mapped[Decimal | None] = mapped_column(Numeric(18, 10))
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'running'")
    )
    training_artifact_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("strategy.training_artifact.id"),
        nullable=True,
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_message: Mapped[str | None] = mapped_column(Text)


class BacktestTradeRecord(CreatedAtMixin, Base):
    __tablename__ = "backtest_trade"
    __table_args__ = (
        CheckConstraint("side in ('buy', 'sell')", name="ck_backtest_trade_side"),
        CheckConstraint("quantity > 0", name="ck_backtest_trade_quantity"),
        Index("ix_backtest_trade_run_date", "run_id", "trade_date"),
        {"schema": "strategy"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger, Sequence("strategy_backtest_trade_id_seq"), primary_key=True
    )
    run_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    signal_date: Mapped[date] = mapped_column(Date, nullable=False)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    symbol: Mapped[str] = mapped_column(String(20), nullable=False)
    side: Mapped[str] = mapped_column(String(10), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    reference_price: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    fill_price: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    commission: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    cash_after: Mapped[Decimal] = mapped_column(Numeric(24, 4), nullable=False)


class BacktestEquity(CreatedAtMixin, Base):
    __tablename__ = "backtest_equity"
    __table_args__ = (
        UniqueConstraint("run_id", "trade_date"),
        Index("ix_backtest_equity_run_date", "run_id", "trade_date"),
        {"schema": "strategy"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger, Sequence("strategy_backtest_equity_id_seq"), primary_key=True
    )
    run_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    cash: Mapped[Decimal] = mapped_column(Numeric(24, 4), nullable=False)
    market_value: Mapped[Decimal] = mapped_column(Numeric(24, 4), nullable=False)
    total_equity: Mapped[Decimal] = mapped_column(Numeric(24, 4), nullable=False)


class BacktestPosition(CreatedAtMixin, Base):
    __tablename__ = "backtest_position"
    __table_args__ = (
        UniqueConstraint("run_id", "trade_date", "symbol"),
        Index("ix_backtest_position_run_date", "run_id", "trade_date"),
        {"schema": "strategy"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger, Sequence("strategy_backtest_position_id_seq"), primary_key=True
    )
    run_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    symbol: Mapped[str] = mapped_column(String(20), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    close: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    market_value: Mapped[Decimal] = mapped_column(Numeric(24, 4), nullable=False)


class BacktestPerformance(CreatedAtMixin, Base):
    __tablename__ = "backtest_performance"
    __table_args__ = (
        UniqueConstraint("run_id"),
        {"schema": "strategy"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger, Sequence("strategy_backtest_performance_id_seq"), primary_key=True
    )
    run_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    total_return: Mapped[Decimal] = mapped_column(Numeric(18, 10), nullable=False)
    annualized_return: Mapped[Decimal] = mapped_column(Numeric(18, 10), nullable=False)
    maximum_drawdown: Mapped[Decimal] = mapped_column(Numeric(18, 10), nullable=False)
    annualized_volatility: Mapped[Decimal] = mapped_column(Numeric(18, 10), nullable=False)
    sharpe_ratio: Mapped[Decimal | None] = mapped_column(Numeric(18, 10))
    calmar_ratio: Mapped[Decimal | None] = mapped_column(Numeric(18, 10))
    daily_win_rate: Mapped[Decimal] = mapped_column(Numeric(18, 10), nullable=False)
    profit_loss_ratio: Mapped[Decimal | None] = mapped_column(Numeric(18, 10))
    turnover_rate: Mapped[Decimal] = mapped_column(Numeric(18, 10), nullable=False)
    total_commission: Mapped[Decimal] = mapped_column(Numeric(24, 6), nullable=False)
    symbol_contributions: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    industry_exposure: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    style_exposure: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    code_version: Mapped[str] = mapped_column(String(100), nullable=False)
