from datetime import date
from decimal import Decimal

from sqlalchemy.orm import Session

from app.repositories.factor import FactorValueRepository
from app.repositories.market import ValuationSnapshotRepository

DEFAULT_STYLE_FACTOR_MAP: dict[str, tuple[str, str]] = {
    "momentum": ("momentum_20", "v1-qfq"),
    "volatility": ("volatility_20", "v1-qfq"),
    "liquidity": ("liquidity_20", "v1-qfq"),
}


class PortfolioStyleDataService:
    def __init__(
        self,
        valuation_repository: ValuationSnapshotRepository | None = None,
        factor_repository: FactorValueRepository | None = None,
    ) -> None:
        self.valuation_repository = valuation_repository or ValuationSnapshotRepository()
        self.factor_repository = factor_repository or FactorValueRepository()

    def load_style_exposures(
        self,
        db: Session,
        *,
        as_of_date: date,
        symbols: list[str],
        style_factor_map: dict[str, tuple[str, str]] | None = None,
    ) -> dict[str, dict[str, Decimal]]:
        style_map = style_factor_map or DEFAULT_STYLE_FACTOR_MAP
        result: dict[str, dict[str, Decimal]] = {}

        market_caps = self.valuation_repository.latest_market_cap_by_symbol(
            db, trade_date=as_of_date, symbols=symbols
        )
        size_exposure: dict[str, Decimal] = {}
        value_exposure: dict[str, Decimal] = {}
        for symbol in symbols:
            cap = market_caps.get(symbol)
            if cap is not None and cap > 0:
                size_exposure[symbol] = Decimal(cap.ln()).quantize(Decimal("0.0001"))
        if size_exposure:
            result["size"] = self._z_score(size_exposure)

        valuation_rows = self.valuation_repository.list_available(
            db, start_date=as_of_date, end_date=as_of_date, symbols=symbols
        )
        for row in valuation_rows:
            if row.pb is not None and row.pb > 0:
                value_exposure[row.symbol] = -row.pb
        if value_exposure:
            result["value"] = self._z_score(value_exposure)

        for style_name, (factor_name, factor_version) in style_map.items():
            rows = self.factor_repository.list_for_cross_section(
                db,
                factor_name=factor_name,
                factor_version=factor_version,
                start_date=as_of_date,
                end_date=as_of_date,
            )
            values = {row.symbol: row.value for row in rows if row.value is not None}
            if values:
                result[style_name] = self._z_score(values)

        return result

    @staticmethod
    def _z_score(values: dict[str, Decimal]) -> dict[str, Decimal]:
        items = [(s, v) for s, v in values.items() if v.is_finite()]
        if len(items) < 2:
            return {s: Decimal() for s, _ in items}
        mean = sum(v for _, v in items) / Decimal(len(items))
        variance = sum(((v - mean) ** 2 for _, v in items), Decimal()) / Decimal(len(items) - 1)
        if variance <= 0:
            return {s: Decimal() for s, _ in items}
        std = Decimal(float(variance) ** 0.5)
        return {s: (v - mean) / std for s, v in items}

    def load_historical_prices(
        self,
        db: Session,
        *,
        symbols: list[str],
        end_date: date,
        window_days: int = 60,
        adjust_type: str = "none",
    ) -> dict[str, list[Decimal]]:
        from app.repositories.market import DailyBarRepository

        repository = DailyBarRepository()
        start_date = self._shift_trading_days(db, end_date, window_days)
        bars = repository.list_for_factor_calculation(
            db,
            start_date=start_date,
            end_date=end_date,
            adjust_type=adjust_type,
            symbols=symbols,
            quality_status="trusted",
        )
        result: dict[str, list[Decimal]] = {s: [] for s in symbols}
        for bar in bars:
            result[bar.symbol].append(bar.close)
        return result

    def load_benchmark_prices(
        self,
        db: Session,
        *,
        benchmark_symbol: str,
        end_date: date,
        window_days: int = 60,
        adjust_type: str = "none",
    ) -> list[Decimal] | None:
        from app.repositories.market import DailyBarRepository

        repository = DailyBarRepository()
        start_date = self._shift_trading_days(db, end_date, window_days)
        bars = repository.list_for_factor_calculation(
            db,
            start_date=start_date,
            end_date=end_date,
            adjust_type=adjust_type,
            symbols=[benchmark_symbol],
            quality_status="trusted",
        )
        if not bars:
            return None
        return [bar.close for bar in sorted(bars, key=lambda b: b.trade_date)]

    @staticmethod
    def _shift_trading_days(db: Session, end_date: date, window_days: int) -> date:
        from datetime import timedelta

        return end_date - timedelta(days=window_days * 2)
