"""Bounded synthetic inputs; all execution, research and ledger services remain real."""

from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database.agent_session import create_agent_engine, initialize_agent_database
from app.database.positions_session import initialize_positions_database
from app.database.research_session import initialize_research_database
from app.database.session import initialize_database
from app.models.market import DailyBar, Security, TradingCalendar
from app.schemas.position_book import PositionBookCreateRequest, PositionTradeCreateRequest
from app.services.basic_price_volume_factors import BasicPriceVolumeFactorService
from app.services.position_book import PositionBookService


def business_databases(root):
    root.mkdir(parents=True, exist_ok=True)
    engines = {
        "market": create_engine(f"duckdb:///{root / 'market_core.duckdb'}"),
        "research": create_engine(f"duckdb:///{root / 'research.duckdb'}"),
        "positions": create_engine(
            f"sqlite:///{root / 'positions.db'}", connect_args={"check_same_thread": False}
        ),
        "agent": create_agent_engine(str(root / "agent.db")),
    }
    initialize_database(engines["market"])
    initialize_research_database(engines["research"])
    initialize_positions_database(engines["positions"])
    initialize_agent_database(engines["agent"])
    return engines, {
        key: sessionmaker(bind=engine, expire_on_commit=False) for key, engine in engines.items()
    }


def seed_business_inputs(factories):
    days = []
    day = date(2026, 1, 5)
    while len(days) < 45:
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    symbols = [f"60000{index}.SH" for index in range(1, 5)]
    with factories["market"]() as db:
        for symbol in symbols:
            db.add(
                Security(
                    symbol=symbol,
                    name="隔离验收样本",
                    security_type="stock",
                    exchange="SH",
                    currency="CNY",
                    industry="样本行业",
                    list_date=date(2000, 1, 1),
                )
            )
        for offset, day in enumerate(days):
            db.add(TradingCalendar(exchange="SH", trade_date=day, is_open=True))
            for index, symbol in enumerate(symbols):
                close = Decimal(10 + index) + Decimal(offset * (index + 1)) / 100
                for adjust_type in ("none", "qfq"):
                    db.add(
                        DailyBar(
                            symbol=symbol,
                            security_type="stock",
                            trade_date=day,
                            adjust_type=adjust_type,
                            open=close,
                            close=close,
                            high=close,
                            low=close,
                            volume=Decimal("1000000"),
                            amount=Decimal("20000000"),
                            limit_up=close * Decimal("1.1"),
                            limit_down=close * Decimal("0.9"),
                            special_treatment_status="normal",
                            is_suspended=False,
                            source_name="synthetic_agent_acceptance",
                            quality_status="trusted",
                            is_suspect=False,
                        )
                    )
        db.commit()
        BasicPriceVolumeFactorService().run(
            db,
            start_date=days[20],
            end_date=days[-1],
            symbols=symbols,
            factor_names=["momentum_20"],
            adjust_type="qfq",
            code_version="synthetic-fixture",
        )
        db.commit()
        with factories["positions"]() as positions:
            service = PositionBookService()
            book = service.create_book(
                positions,
                payload=PositionBookCreateRequest(
                    name="隔离验收账本",
                    cash=Decimal("100000"),
                ),
            )
            service.add_trade(
                positions,
                db,
                book_id=book.id,
                idempotency_key="fixture-trade",
                payload=PositionTradeCreateRequest(
                    side="buy",
                    symbol=symbols[0],
                    quantity=100,
                    price=Decimal("10"),
                    fee=Decimal("5"),
                    trade_date=days[0],
                ),
            )
            positions.commit()
    return {
        "start_date": days[20].isoformat(),
        "end_date": days[-1].isoformat(),
        "book_id": book.id,
        "symbol": symbols[0],
    }
