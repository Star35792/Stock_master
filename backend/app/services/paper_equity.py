from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config.settings import settings
from app.repositories.market import (
    CorporateActionRepository,
    DailyBarRepository,
    SecurityRepository,
    TradingCalendarRepository,
)
from app.repositories.trade import (
    PaperEquitySnapshotRepository,
    PaperTradingRepository,
)
from app.schemas.trade import (
    PaperEquityComponentRead,
    PaperEquitySnapshotRead,
)
from app.services.snapshot_hashes import (
    hash_equity_market_snapshot,
    hash_paper_account_snapshot,
    hash_payload,
)

MONEY_QUANTUM = Decimal("0.0001")
RATIO_QUANTUM = Decimal("0.0000000001")
VALUATION_POLICY = {
    "name": "paper_account_daily_close",
    "version": "v1",
    "adjust_type": "none",
    "accepted_quality": "trusted,manual_fixed",
    "price_field": "close",
    "history_scope": "since_monitoring_start",
}


class PaperEquityConflict(ValueError):
    pass


class PaperEquityValidationError(ValueError):
    pass


class PaperAccountNotFound(ValueError):
    pass


class PaperEquitySnapshotNotFound(ValueError):
    pass


class PaperEquitySnapshotService:
    def __init__(
        self,
        *,
        paper_repository: PaperTradingRepository | None = None,
        equity_repository: PaperEquitySnapshotRepository | None = None,
        security_repository: SecurityRepository | None = None,
        daily_bar_repository: DailyBarRepository | None = None,
        calendar_repository: TradingCalendarRepository | None = None,
        corporate_action_repository: CorporateActionRepository | None = None,
    ) -> None:
        self.paper_repository = paper_repository or PaperTradingRepository()
        self.equity_repository = equity_repository or PaperEquitySnapshotRepository()
        self.security_repository = security_repository or SecurityRepository()
        self.daily_bar_repository = daily_bar_repository or DailyBarRepository()
        self.calendar_repository = calendar_repository or TradingCalendarRepository()
        self.corporate_action_repository = (
            corporate_action_repository or CorporateActionRepository()
        )

    def capture(
        self,
        db: Session,
        *,
        account_id: int,
        valuation_date: date,
        idempotency_key: str,
    ) -> PaperEquitySnapshotRead:
        request_hash = hash_payload(
            {"account_id": account_id, "valuation_date": valuation_date.isoformat()}
        )
        existing = self.equity_repository.get_by_idempotency_key(
            db,
            idempotency_key=idempotency_key,
        )
        if existing is not None:
            return self._replay(db, existing, request_hash=request_hash)
        if valuation_date > date.today():
            raise PaperEquityValidationError("valuation_date must not be in the future")

        account = self.paper_repository.get_account_for_update(
            db,
            account_id=account_id,
        )
        if account is None:
            raise PaperAccountNotFound("paper account not found")
        natural_existing = self.equity_repository.get_by_account_date(
            db,
            account_id=account_id,
            valuation_date=valuation_date,
        )
        if natural_existing is not None:
            raise PaperEquityConflict(
                "an equity snapshot already exists for this account and valuation_date"
            )

        previous = self.equity_repository.get_latest(db, account_id=account_id)
        if previous is not None and valuation_date <= previous.valuation_date:
            raise PaperEquityConflict(
                "equity snapshots are append-only and valuation_date must increase"
            )
        latest_fill_date = self.paper_repository.get_latest_fill_date(
            db,
            account_id=account_id,
        )
        if latest_fill_date is not None and latest_fill_date > valuation_date:
            raise PaperEquityValidationError("paper account contains fills after valuation_date")
        if account.cash_balance < 0:
            raise PaperEquityValidationError("paper account cash must be non-negative")

        positions = self.paper_repository.list_positions(db, account_id=account_id)
        if any(position.quantity < 0 for position in positions):
            raise PaperEquityValidationError("paper account positions must not be negative")
        symbols = {position.symbol for position in positions}
        securities = {
            security.symbol: security
            for security in self.security_repository.get_many(db, symbols=symbols)
        }
        if set(securities) != symbols:
            raise PaperEquityValidationError(
                "paper account position security is missing from market master data"
            )
        exchanges = {security.exchange for security in securities.values()} or {"SH"}
        calendars = {
            calendar.exchange: calendar
            for calendar in self.calendar_repository.get_many_on_date(
                db,
                exchanges=exchanges,
                trade_date=valuation_date,
            )
        }
        if any(
            exchange not in calendars or not calendars[exchange].is_open for exchange in exchanges
        ):
            raise PaperEquityValidationError(
                "valuation_date must be an open trading date for every position exchange"
            )

        bars = {
            bar.symbol: bar
            for bar in self.daily_bar_repository.list_by_symbols_on_date_for_update(
                db,
                symbols=symbols,
                trade_date=valuation_date,
                adjust_type="none",
            )
        }
        if set(bars) != symbols:
            raise PaperEquityValidationError(
                "every position requires an exact-date unadjusted market bar"
            )
        if any(
            bar.quality_status not in {"trusted", "manual_fixed"}
            or bar.is_suspect
            or bar.close <= 0
            for bar in bars.values()
        ):
            raise PaperEquityValidationError(
                "every valuation bar must be trusted, non-suspect, and positive"
            )

        action_start = (
            valuation_date if previous is None else previous.valuation_date + timedelta(days=1)
        )
        actions = self.corporate_action_repository.list_for_symbols_date_range(
            db,
            symbols=symbols,
            start_date=action_start,
            end_date=valuation_date,
        )
        if actions:
            raise PaperEquityValidationError(
                "corporate actions in the monitoring interval require paper ledger handling"
            )

        components = []
        for position in sorted(positions, key=lambda item: item.symbol):
            bar = bars[position.symbol]
            market_value = (Decimal(position.quantity) * bar.close).quantize(MONEY_QUANTUM)
            components.append(
                {
                    "symbol": position.symbol,
                    "quantity": position.quantity,
                    "average_cost": position.average_cost,
                    "price": bar.close,
                    "market_value": market_value,
                    "daily_bar_id": bar.id,
                    "source_name": bar.source_name,
                    "quality_status": bar.quality_status,
                    "bar_trade_date": bar.trade_date,
                    "adjust_type": bar.adjust_type,
                    "is_suspect": bar.is_suspect,
                    "bar_updated_at": bar.updated_at,
                }
            )
        positions_market_value = sum(
            (component["market_value"] for component in components),
            Decimal(),
        ).quantize(MONEY_QUANTUM)
        cash_balance = Decimal(account.cash_balance).quantize(MONEY_QUANTUM)
        total_equity = (cash_balance + positions_market_value).quantize(MONEY_QUANTUM)

        history_status = self._history_status(
            db,
            previous=previous,
            valuation_date=valuation_date,
            exchanges=exchanges,
        )
        high_watermark = (
            total_equity
            if previous is None
            else max(Decimal(previous.high_watermark), total_equity)
        ).quantize(MONEY_QUANTUM)
        drawdown = (
            Decimal()
            if high_watermark == 0
            else (total_equity / high_watermark - Decimal(1)).quantize(RATIO_QUANTUM)
        )
        monitoring_start_date = (
            valuation_date if previous is None else previous.monitoring_start_date
        )
        observation_count = 1 if previous is None else previous.observation_count + 1

        account_snapshot_hash = hash_paper_account_snapshot(account, positions)
        market_snapshot_hash = hash_equity_market_snapshot(
            valuation_date,
            components,
        )
        valuation_hash = hash_payload(
            {
                "account_snapshot_hash": account_snapshot_hash,
                "market_snapshot_hash": market_snapshot_hash,
                "cash_balance": str(cash_balance),
                "positions_market_value": str(positions_market_value),
                "total_equity": str(total_equity),
                "high_watermark": str(high_watermark),
                "drawdown": str(drawdown),
                "previous_snapshot_id": None if previous is None else previous.id,
                "history_status": history_status,
                "valuation_policy": VALUATION_POLICY,
                "code_version": settings.app_code_version,
            }
        )
        snapshot_values = {
            "account_id": account.id,
            "valuation_date": valuation_date,
            "cash_balance": cash_balance,
            "positions_market_value": positions_market_value,
            "total_equity": total_equity,
            "high_watermark": high_watermark,
            "drawdown": drawdown,
            "previous_snapshot_id": None if previous is None else previous.id,
            "monitoring_start_date": monitoring_start_date,
            "observation_count": observation_count,
            "history_status": history_status,
            "account_snapshot_hash": account_snapshot_hash,
            "market_snapshot_hash": market_snapshot_hash,
            "valuation_hash": valuation_hash,
            "valuation_policy": VALUATION_POLICY,
            "code_version": settings.app_code_version,
            "idempotency_key": idempotency_key,
            "request_hash": request_hash,
        }
        try:
            with db.begin_nested():
                snapshot = self.equity_repository.save(
                    db,
                    snapshot_values=snapshot_values,
                    component_values=components,
                )
        except IntegrityError as exc:
            existing = self.equity_repository.get_by_idempotency_key(
                db,
                idempotency_key=idempotency_key,
            )
            if existing is not None:
                return self._replay(db, existing, request_hash=request_hash)
            raise PaperEquityConflict(
                "an equity snapshot already exists for this account and valuation_date"
            ) from exc
        return self._to_read(snapshot, components)

    def get_snapshot(
        self,
        db: Session,
        *,
        account_id: int,
        snapshot_id: int,
    ) -> PaperEquitySnapshotRead:
        snapshot, components = self.equity_repository.get_with_components(
            db,
            account_id=account_id,
            snapshot_id=snapshot_id,
        )
        if snapshot is None:
            raise PaperEquitySnapshotNotFound("paper equity snapshot not found")
        return self._to_read(snapshot, components)

    def _replay(
        self,
        db: Session,
        snapshot,
        *,
        request_hash: str,
    ) -> PaperEquitySnapshotRead:
        if snapshot.request_hash != request_hash:
            raise PaperEquityConflict("idempotency key was already used with a different request")
        _, components = self.equity_repository.get_with_components(
            db,
            account_id=snapshot.account_id,
            snapshot_id=snapshot.id,
        )
        return self._to_read(snapshot, components)

    def _history_status(
        self,
        db: Session,
        *,
        previous,
        valuation_date: date,
        exchanges: set[str],
    ) -> str:
        if previous is None:
            return "bootstrap"
        for exchange in exchanges:
            open_dates = self.calendar_repository.list_open_dates(
                db,
                exchange=exchange,
                start_date=previous.valuation_date + timedelta(days=1),
                end_date=valuation_date,
            )
            if open_dates != [valuation_date]:
                return "gap"
        return "continuous" if previous.history_status != "gap" else "gap"

    @staticmethod
    def _to_read(snapshot, components) -> PaperEquitySnapshotRead:
        return PaperEquitySnapshotRead(
            snapshot_id=snapshot.id,
            account_id=snapshot.account_id,
            valuation_date=snapshot.valuation_date,
            cash_balance=snapshot.cash_balance,
            positions_market_value=snapshot.positions_market_value,
            total_equity=snapshot.total_equity,
            observed_high_watermark=snapshot.high_watermark,
            drawdown_since_monitoring_start=snapshot.drawdown,
            previous_snapshot_id=snapshot.previous_snapshot_id,
            monitoring_start_date=snapshot.monitoring_start_date,
            observation_count=snapshot.observation_count,
            history_status=snapshot.history_status,
            account_snapshot_hash=snapshot.account_snapshot_hash,
            market_snapshot_hash=snapshot.market_snapshot_hash,
            valuation_hash=snapshot.valuation_hash,
            valuation_policy=snapshot.valuation_policy,
            code_version=snapshot.code_version,
            created_at=snapshot.created_at,
            components=[
                PaperEquityComponentRead.model_validate(component) for component in components
            ],
        )
