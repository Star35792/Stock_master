from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models.market import Security


class SecurityRepository:
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
