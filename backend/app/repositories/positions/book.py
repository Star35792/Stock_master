from sqlalchemy import delete as sa_delete, select
from sqlalchemy.orm import Session

from app.models.positions import PositionBook, PositionTrade


class PositionBookRepository:
    def list_books(self, db: Session) -> list[PositionBook]:
        return list(
            db.scalars(select(PositionBook).order_by(PositionBook.id))
        )

    def get_book(self, db: Session, *, book_id: int) -> PositionBook | None:
        return db.get(PositionBook, book_id)

    def get_book_by_name(self, db: Session, *, name: str) -> PositionBook | None:
        return db.scalar(select(PositionBook).where(PositionBook.name == name))

    def save_book(self, db: Session, *, book: PositionBook) -> PositionBook:
        db.add(book)
        db.flush()
        return book

    def list_trades(
        self,
        db: Session,
        *,
        book_id: int,
        symbol: str | None = None,
    ) -> list[PositionTrade]:
        filters = [PositionTrade.book_id == book_id]
        if symbol is not None:
            filters.append(PositionTrade.symbol == symbol)
        return list(
            db.scalars(
                select(PositionTrade)
                .where(*filters)
                .order_by(PositionTrade.trade_date, PositionTrade.id)
            )
        )

    def get_trade(self, db: Session, *, book_id: int, trade_id: int) -> PositionTrade | None:
        return db.scalar(
            select(PositionTrade).where(
                PositionTrade.book_id == book_id,
                PositionTrade.id == trade_id,
            )
        )

    def get_trade_by_idempotency_key(
        self, db: Session, *, idempotency_key: str
    ) -> PositionTrade | None:
        return db.scalar(
            select(PositionTrade).where(PositionTrade.idempotency_key == idempotency_key)
        )

    def save_trade(self, db: Session, *, trade: PositionTrade) -> PositionTrade:
        db.add(trade)
        db.flush()
        return trade

    def delete_trade(self, db: Session, *, trade: PositionTrade) -> None:
        db.execute(sa_delete(PositionTrade).where(PositionTrade.id == trade.id))
        db.flush()
