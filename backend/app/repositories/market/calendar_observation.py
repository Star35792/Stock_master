"""Read auditable calendar observations and detect publication conflicts."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.market import TradingCalendar
from app.models.raw import ImportBatch, RawVendorSnapshot


class CalendarObservationRepository:
    def latest(self, db: Session):
        batch = db.scalar(select(ImportBatch).where(
            ImportBatch.dataset == "security_and_trading_calendar",
            ImportBatch.status == "success",
        ).order_by(ImportBatch.id.desc()).limit(1))
        if batch is None:
            raise ValueError("successful reference batch missing")
        pages = list(db.scalars(select(RawVendorSnapshot).where(
            RawVendorSnapshot.batch_id == batch.id,
            RawVendorSnapshot.dataset == "hithink_calendar_reference",
        )))
        return batch, pages

    def existing(self, db: Session, *, start, end):
        return list(db.scalars(select(TradingCalendar).where(
            TradingCalendar.trade_date.between(start, end),
            TradingCalendar.exchange.in_(("SH", "SZ", "BJ")),
        )))
