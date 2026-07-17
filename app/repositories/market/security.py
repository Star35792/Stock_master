from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.data_sources import SecurityRecord
from app.models.market import Security


class SecurityRepository:
    def upsert_many(
        self,
        db: Session,
        *,
        records: list[SecurityRecord],
        source_name: str,
    ) -> int:
        if not records:
            return 0

        rows = [
            {
                "symbol": record.symbol,
                "raw_symbol": record.raw_symbol,
                "name": record.name,
                "security_type": record.security_type,
                "exchange": record.exchange,
                "currency": record.currency,
                "is_active": True,
                "source_name": source_name,
            }
            for record in records
        ]
        statement = insert(Security).values(rows)
        statement = statement.on_conflict_do_update(
            index_elements=[Security.symbol],
            set_={
                "raw_symbol": statement.excluded.raw_symbol,
                "name": statement.excluded.name,
                "security_type": statement.excluded.security_type,
                "exchange": statement.excluded.exchange,
                "currency": statement.excluded.currency,
                "is_active": True,
                "source_name": statement.excluded.source_name,
            },
        )
        db.execute(statement)
        return len(rows)

    def get_symbols(self, db: Session, symbols: set[str]) -> set[str]:
        if not symbols:
            return set()
        return set(db.scalars(select(Security.symbol).where(Security.symbol.in_(symbols))).all())

    def get_security_types(self, db: Session, symbols: set[str]) -> dict[str, str]:
        if not symbols:
            return {}
        rows = db.execute(
            select(Security.symbol, Security.security_type).where(Security.symbol.in_(symbols))
        ).all()
        return dict(rows)

    def get_by_symbol(self, db: Session, *, symbol: str) -> Security | None:
        return db.scalar(select(Security).where(Security.symbol == symbol))

    def list(
        self,
        db: Session,
        *,
        security_type: str | None = None,
        exchange: str | None = None,
        is_active: bool | None = True,
        keyword: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[Security], int]:
        filters = []
        if security_type:
            filters.append(Security.security_type == security_type)
        if exchange:
            filters.append(Security.exchange == exchange)
        if is_active is not None:
            filters.append(Security.is_active == is_active)
        if keyword:
            filters.append(
                or_(
                    Security.symbol.ilike(f"%{keyword}%"),
                    Security.name.ilike(f"%{keyword}%"),
                )
            )

        total = db.scalar(select(func.count()).select_from(Security).where(*filters)) or 0
        items = list(
            db.scalars(
                select(Security)
                .where(*filters)
                .order_by(Security.symbol)
                .limit(limit)
                .offset(offset)
            )
        )
        return items, total
