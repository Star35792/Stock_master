from sqlalchemy import func, insert, or_, select
from sqlalchemy.orm import Session

from app.data_sources import SecurityRecord
from app.models.market import Security
from app.repositories.market.sequence_ids import reserve_sequence_ids


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
        # DuckDB may fail while replacing the indexes for a bulk ON CONFLICT
        # UPDATE of thousands of unchanged securities. Read current values and
        # only write real changes. Explicit ids also avoid a legacy sequence
        # lagging behind previously bulk-loaded rows.
        existing = {row.symbol: row for row in db.scalars(select(Security))}
        max_id = db.scalar(select(func.max(Security.id))) or 0
        new_rows: list[dict] = []
        changed = 0
        seen: set[str] = set()
        for record in records:
            if record.symbol in seen:
                raise ValueError(f"duplicate security in reference response: {record.symbol}")
            seen.add(record.symbol)
            row = existing.get(record.symbol)
            if (
                row is not None
                and row.list_date is not None
                and record.list_date is not None
                and row.list_date != record.list_date
            ):
                raise ValueError(f"conflicting existing listing date: {record.symbol}")
            # Some HiThink ticker names contain the Unicode replacement
            # character. Such a response cannot replace a known good name.
            name = (
                record.name if "\ufffd" not in record.name
                else (row.name if row else record.symbol)
            )
            values = {
                "raw_symbol": record.raw_symbol,
                "name": name,
                "security_type": record.security_type,
                "exchange": record.exchange,
                "currency": record.currency,
                "is_active": True,
            }
            if record.list_date is not None:
                values["list_date"] = record.list_date
            if row is None:
                new_rows.append(
                    {"symbol": record.symbol, **values,
                     "source_name": source_name}
                )
                continue
            row_changed = False
            for field, value in values.items():
                if getattr(row, field) != value:
                    setattr(row, field, value)
                    changed += 1
                    row_changed = True
            if row_changed and row.source_name != source_name:
                row.source_name = source_name
            if changed >= 100:
                db.flush()
                changed = 0
        if changed:
            db.flush()
        ids = reserve_sequence_ids(
            db, sequence_name="market_security_id_seq",
            max_existing_id=max_id, count=len(new_rows),
        )
        for row, row_id in zip(new_rows, ids, strict=True):
            row["id"] = row_id
        for start in range(0, len(new_rows), 200):
            db.execute(insert(Security), new_rows[start : start + 200])
        return len(records)

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

    def get_industries(self, db: Session, symbols: set[str]) -> dict[str, str]:
        if not symbols:
            return {}
        rows = db.execute(
            select(Security.symbol, Security.industry).where(Security.symbol.in_(symbols))
        ).all()
        return {symbol: industry or "UNKNOWN" for symbol, industry in rows}

    def get_by_symbol(self, db: Session, *, symbol: str) -> Security | None:
        return db.scalar(select(Security).where(Security.symbol == symbol))

    def get_many(self, db: Session, *, symbols: set[str]) -> list[Security]:
        if not symbols:
            return []
        return list(
            db.scalars(
                select(Security).where(Security.symbol.in_(symbols)).order_by(Security.symbol)
            )
        )

    def get_active_symbols(self, db: Session) -> list[str]:
        return list(db.scalars(select(Security.symbol).where(Security.is_active.is_(True))).all())

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
