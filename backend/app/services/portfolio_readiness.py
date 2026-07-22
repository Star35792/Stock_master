import json
from datetime import UTC, date, datetime
from decimal import Decimal
from hashlib import sha256
from typing import Any

from sqlalchemy.orm import Session

from app.config.settings import settings
from app.portfolio import PortfolioAsset, PortfolioBuilder, PortfolioConstraints
from app.repositories.market import (
    DailyBarRepository,
    SecurityRepository,
    TradingCalendarRepository,
)
from app.repositories.portfolio import PortfolioPlanRepository
from app.repositories.trade import PaperTradingRepository
from app.schemas.portfolio import (
    PortfolioReadinessCheck,
    PortfolioRiskReadinessRead,
)
from app.services.portfolio import hash_portfolio_snapshot
from app.services.snapshot_hashes import hash_paper_account_snapshot


class PortfolioPlanNotFound(ValueError):
    pass


class PaperAccountNotFound(ValueError):
    pass


class PortfolioRiskReadinessService:
    def __init__(
        self,
        *,
        plan_repository: PortfolioPlanRepository | None = None,
        paper_repository: PaperTradingRepository | None = None,
        security_repository: SecurityRepository | None = None,
        daily_bar_repository: DailyBarRepository | None = None,
        trading_calendar_repository: TradingCalendarRepository | None = None,
        builder: PortfolioBuilder | None = None,
    ) -> None:
        self.plan_repository = plan_repository or PortfolioPlanRepository()
        self.paper_repository = paper_repository or PaperTradingRepository()
        self.security_repository = security_repository or SecurityRepository()
        self.daily_bar_repository = daily_bar_repository or DailyBarRepository()
        self.trading_calendar_repository = (
            trading_calendar_repository or TradingCalendarRepository()
        )
        self.builder = builder or PortfolioBuilder()

    def check(
        self,
        db: Session,
        *,
        plan_id: int,
        paper_account_id: int,
    ) -> PortfolioRiskReadinessRead:
        plan, targets = self.plan_repository.get_with_targets(db, plan_id=plan_id)
        if plan is None:
            raise PortfolioPlanNotFound("portfolio plan not found")
        account = self.paper_repository.get_account(db, account_id=paper_account_id)
        if account is None:
            raise PaperAccountNotFound("paper account not found")

        positions = self.paper_repository.list_positions(
            db,
            account_id=paper_account_id,
        )
        symbols = {target.symbol for target in targets} | {
            position.symbol for position in positions
        }
        securities = {
            security.symbol: security
            for security in self.security_repository.get_many(db, symbols=symbols)
        }
        bars = {
            bar.symbol: bar
            for bar in self.daily_bar_repository.list_by_symbols_on_date(
                db,
                symbols=symbols,
                trade_date=plan.as_of_date,
                adjust_type="none",
            )
        }
        latest_dates = self.daily_bar_repository.get_latest_trusted_none_dates(
            db,
            symbols=symbols,
        )
        calendars = {
            calendar.exchange: calendar
            for calendar in self.trading_calendar_repository.get_many_on_date(
                db,
                exchanges={security.exchange for security in securities.values()},
                trade_date=plan.as_of_date,
            )
        }

        checks: list[PortfolioReadinessCheck] = []
        self._check_plan(checks, plan=plan, targets=targets)
        self._check_account(
            checks,
            plan=plan,
            targets=targets,
            account=account,
            positions=positions,
        )
        self._check_securities(
            checks,
            plan=plan,
            targets=targets,
            securities=securities,
            calendars=calendars,
        )
        self._check_market(
            checks,
            plan=plan,
            targets=targets,
            bars=bars,
            latest_dates=latest_dates,
        )
        blockers = [check for check in checks if not check.passed]
        return PortfolioRiskReadinessRead(
            plan_id=plan.id,
            paper_account_id=account.id,
            ready_for_risk_check=not blockers,
            checked_at=datetime.now(UTC),
            account_snapshot_hash=hash_paper_account_snapshot(account, positions),
            market_snapshot_hash=self._hash(
                {
                    "securities": [
                        {
                            "symbol": security.symbol,
                            "security_type": security.security_type,
                            "industry": security.industry,
                            "is_active": security.is_active,
                            "list_date": self._iso(security.list_date),
                            "delist_date": self._iso(security.delist_date),
                            "updated_at": self._iso(security.updated_at),
                        }
                        for security in securities.values()
                    ],
                    "bars": [
                        {
                            "id": bar.id,
                            "symbol": bar.symbol,
                            "trade_date": self._iso(bar.trade_date),
                            "close": str(bar.close),
                            "source_name": bar.source_name,
                            "quality_status": bar.quality_status,
                            "is_suspect": bar.is_suspect,
                            "updated_at": self._iso(bar.updated_at),
                        }
                        for bar in bars.values()
                    ],
                }
            ),
            checks=checks,
            blockers=blockers,
        )

    def _check_plan(self, checks: list[PortfolioReadinessCheck], *, plan, targets) -> None:
        snapshot = plan.input_snapshot
        audit_ok = (
            isinstance(snapshot, dict)
            and snapshot.get("input_source") == "operator_provided"
            and bool(plan.idempotency_key)
            and plan.request_hash == hash_portfolio_snapshot(snapshot)
            and snapshot.get("name") == plan.name
            and snapshot.get("as_of_date") == plan.as_of_date.isoformat()
        )
        self._add(
            checks,
            code="draft_snapshot_integrity",
            scope="plan",
            passed=audit_ok,
            message="draft input snapshot and request hash are consistent",
        )
        self._add(
            checks,
            code="draft_code_version",
            scope="plan",
            passed=plan.code_version == settings.app_code_version,
            message="draft code version matches the running service",
        )
        self._add(
            checks,
            code="future_trade_date",
            scope="plan",
            passed=plan.as_of_date <= date.today(),
            message="draft date is not in the future",
        )
        self._add(
            checks,
            code="orders_required",
            scope="plan",
            passed=any(target.order_quantity != 0 for target in targets),
            message="draft contains at least one order for later risk evaluation",
        )
        if not audit_ok:
            return
        try:
            assets = [
                PortfolioAsset(
                    symbol=asset["symbol"],
                    security_type=asset["security_type"],
                    industry=asset["industry"],
                    price=Decimal(asset["price"]),
                    current_quantity=asset["current_quantity"],
                    requested_weight=Decimal(asset["requested_weight"]),
                    lot_size=asset["lot_size"],
                )
                for asset in snapshot["assets"]
            ]
            raw_constraints = snapshot["constraints"]
            rebuilt = self.builder.build(
                assets=assets,
                cash=Decimal(snapshot["cash"]),
                constraints=PortfolioConstraints(
                    cash_reserve_rate=Decimal(raw_constraints["cash_reserve_rate"]),
                    max_symbol_weight=Decimal(raw_constraints["max_symbol_weight"]),
                    max_industry_weight=Decimal(raw_constraints["max_industry_weight"]),
                    security_type_budgets={
                        key: Decimal(value)
                        for key, value in raw_constraints["security_type_budgets"].items()
                    },
                ),
            )
            target_by_symbol = {target.symbol: target for target in targets}
            calculation_ok = (
                rebuilt.total_equity == plan.total_equity
                and rebuilt.cash_before == plan.cash_before
                and rebuilt.target_cash == plan.target_cash
                and rebuilt.cash_after == plan.cash_after
                and len(rebuilt.targets) == len(targets)
                and all(
                    self._target_matches(target, target_by_symbol.get(target.symbol))
                    for target in rebuilt.targets
                )
            )
        except (KeyError, TypeError, ValueError):
            calculation_ok = False
        self._add(
            checks,
            code="draft_calculation_consistency",
            scope="plan",
            passed=calculation_ok,
            message="stored targets reproduce from the immutable draft snapshot",
        )

    def _check_account(
        self,
        checks: list[PortfolioReadinessCheck],
        *,
        plan,
        targets,
        account,
        positions,
    ) -> None:
        self._add(
            checks,
            code="account_cash_non_negative",
            scope="account",
            passed=account.cash_balance >= 0,
            message="paper account cash is non-negative",
        )
        self._add(
            checks,
            code="account_cash_match",
            scope="account",
            passed=account.cash_balance == plan.cash_before,
            message="draft cash matches the selected paper account",
        )
        target_by_symbol = {target.symbol: target for target in targets}
        position_by_symbol = {position.symbol: position for position in positions}
        for symbol in sorted(set(target_by_symbol) | set(position_by_symbol)):
            target = target_by_symbol.get(symbol)
            position = position_by_symbol.get(symbol)
            if target is None:
                self._add(
                    checks,
                    code="position_missing_from_plan",
                    scope="position",
                    symbol=symbol,
                    passed=False,
                    message="paper account position is absent from the draft",
                )
                continue
            account_quantity = 0 if position is None else position.quantity
            self._add(
                checks,
                code="position_quantity_match",
                scope="position",
                symbol=symbol,
                passed=target.current_quantity == account_quantity,
                message="draft quantity matches the selected paper account",
            )

    def _check_securities(
        self,
        checks: list[PortfolioReadinessCheck],
        *,
        plan,
        targets,
        securities,
        calendars,
    ) -> None:
        for target in targets:
            security = securities.get(target.symbol)
            if security is None:
                self._add(
                    checks,
                    code="security_missing",
                    scope="security",
                    symbol=target.symbol,
                    passed=False,
                    message="security is missing from market master data",
                )
                continue
            conditions = (
                ("security_active", security.is_active, "security is active"),
                (
                    "security_listed",
                    security.list_date is None or security.list_date <= plan.as_of_date,
                    "security is listed on the draft date",
                ),
                (
                    "security_not_delisted",
                    security.delist_date is None or security.delist_date >= plan.as_of_date,
                    "security is not delisted on the draft date",
                ),
                (
                    "supported_security_type",
                    security.security_type in {"stock", "etf"},
                    "security type is supported for paper risk checks",
                ),
                (
                    "security_type_match",
                    security.security_type == target.security_type,
                    "draft security type matches market master data",
                ),
                (
                    "security_industry_present",
                    bool(security.industry),
                    "market master data contains an industry",
                ),
                (
                    "security_industry_match",
                    security.industry == target.industry,
                    "draft industry matches current market master data",
                ),
                (
                    "trading_calendar_open",
                    (security.exchange in calendars and calendars[security.exchange].is_open),
                    "security exchange is open on the draft date",
                ),
            )
            for code, passed, message in conditions:
                self._add(
                    checks,
                    code=code,
                    scope="security",
                    symbol=target.symbol,
                    passed=passed,
                    message=message,
                )

    def _check_market(
        self,
        checks: list[PortfolioReadinessCheck],
        *,
        plan,
        targets,
        bars,
        latest_dates,
    ) -> None:
        for target in targets:
            bar = bars.get(target.symbol)
            if bar is None:
                self._add(
                    checks,
                    code="trusted_none_bar_missing",
                    scope="market",
                    symbol=target.symbol,
                    passed=False,
                    message="exact-date unadjusted market bar is missing",
                )
            else:
                self._add(
                    checks,
                    code="market_bar_trusted",
                    scope="market",
                    symbol=target.symbol,
                    passed=(
                        bar.quality_status in {"trusted", "manual_fixed"} and not bar.is_suspect
                    ),
                    message="market bar is trusted or manually fixed and not suspect",
                )
                self._add(
                    checks,
                    code="market_price_match",
                    scope="market",
                    symbol=target.symbol,
                    passed=bar.close == target.price,
                    message="draft price matches the exact-date unadjusted close",
                )
            latest_date = latest_dates.get(target.symbol)
            self._add(
                checks,
                code="market_snapshot_current",
                scope="market",
                symbol=target.symbol,
                passed=latest_date == plan.as_of_date,
                message="draft date is the latest trusted unadjusted date for the symbol",
            )

    @staticmethod
    def _target_matches(expected, actual) -> bool:
        if actual is None:
            return False
        comparable_fields = (
            "security_type",
            "industry",
            "requested_weight",
            "constrained_weight",
            "final_weight",
            "current_quantity",
            "target_quantity",
            "order_quantity",
            "price",
        )
        return all(
            getattr(expected, field) == getattr(actual, field) for field in comparable_fields
        ) and tuple(expected.reasons) == tuple(actual.reasons)

    @staticmethod
    def _add(
        checks: list[PortfolioReadinessCheck],
        *,
        code: str,
        scope: str,
        passed: bool,
        message: str,
        symbol: str | None = None,
    ) -> None:
        checks.append(
            PortfolioReadinessCheck(
                code=code,
                scope=scope,
                passed=passed,
                symbol=symbol,
                message=message,
            )
        )

    @staticmethod
    def _hash(value: dict[str, Any]) -> str:
        canonical = json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        return sha256(canonical.encode("utf-8")).hexdigest()

    @staticmethod
    def _iso(value: Any) -> str | None:
        return None if value is None else value.isoformat()
