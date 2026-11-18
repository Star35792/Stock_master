from datetime import date
from decimal import Decimal

import duckdb
from sqlalchemy.orm import Session

from app.repositories.factor import FactorRunRepository, FactorValueRepository
from app.repositories.lake import ReleaseRepository
from app.repositories.market import (
    DailyBarRepository,
    TradingCalendarRepository,
    ValuationSnapshotRepository,
)
from app.repositories.warm_layer import WarmLayerStore
from app.services.release import ReleaseService
from app.utils.symbol import symbol_exchange

DEFAULT_STYLE_FACTOR_MAP: dict[str, tuple[str, str]] = {
    "momentum": ("momentum_20", "v1-qfq"),
    "volatility": ("volatility_20", "v1-qfq"),
    "liquidity": ("liquidity_20", "v1-qfq"),
}
MIN_FACTOR_CROSS_SECTION = 100
MIN_FACTOR_MARKET_COVERAGE = Decimal("0.99")


class PortfolioStyleDataService:
    def __init__(
        self,
        valuation_repository: ValuationSnapshotRepository | None = None,
        factor_repository: FactorValueRepository | None = None,
        factor_run_repository: FactorRunRepository | None = None,
        daily_bar_repository: DailyBarRepository | None = None,
        warm_store: WarmLayerStore | None = None,
    ) -> None:
        self.valuation_repository = valuation_repository or ValuationSnapshotRepository()
        self.factor_repository = factor_repository or FactorValueRepository()
        self.factor_run_repository = factor_run_repository or FactorRunRepository()
        self.daily_bar_repository = daily_bar_repository or DailyBarRepository()
        self.warm_store = warm_store or WarmLayerStore()

    def load_style_exposures(
        self,
        db: Session,
        *,
        as_of_date: date,
        symbols: list[str],
        style_factor_map: dict[str, tuple[str, str]] | None = None,
        include_valuation_styles: bool = True,
        strict_budget: bool = False,
        factor_evidence: dict[str, dict[str, int | str]] | None = None,
    ) -> dict[str, dict[str, Decimal]]:
        style_map = style_factor_map or DEFAULT_STYLE_FACTOR_MAP
        result: dict[str, dict[str, Decimal]] = {}
        expected_symbols = (
            self.daily_bar_repository.list_trusted_stock_universe(db, trade_date=as_of_date)
            if strict_budget else set()
        )
        expected_count = len(expected_symbols)
        if strict_budget and expected_count < MIN_FACTOR_CROSS_SECTION:
            raise ValueError("trusted stock universe is too small for style budget")
        if strict_budget and not set(symbols).issubset(expected_symbols):
            raise ValueError("style budget currently supports trusted stock holdings only")
        observed_symbols: set[str] | None = None

        if include_valuation_styles:
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
            if strict_budget:
                rows = [row for row in rows if row.symbol in expected_symbols]
            values = {row.symbol: row.value for row in rows if row.value is not None}
            if strict_budget:
                finite = {symbol: value for symbol, value in values.items() if value.is_finite()}
                if (
                    len(finite) < MIN_FACTOR_CROSS_SECTION
                    or Decimal(len(finite)) / Decimal(expected_count)
                    < MIN_FACTOR_MARKET_COVERAGE
                    or len(set(finite.values())) < 2
                ):
                    raise ValueError(
                        f"{style_name} factor cross section is incomplete or degenerate "
                        f"on {as_of_date}: {len(finite)}/{expected_count}"
                    )
                if observed_symbols is not None and set(finite) != observed_symbols:
                    raise ValueError("style factor cross sections use different symbol universes")
                observed_symbols = set(finite)
                run_ids = {row.run_id for row in rows}
                if len(run_ids) != 1:
                    raise ValueError(f"{style_name} factor cross section mixes runs")
                run_id = run_ids.pop()
                run = self.factor_run_repository.get_by_id(db, run_id)
                parameters = run.parameters if run is not None else {}
                requested_symbols = parameters.get("symbols")
                if (
                    run is None or run.status != "success"
                    or run.factor_name != factor_name or run.factor_version != factor_version
                    or run.input_end_date is None or run.input_end_date < as_of_date
                    or parameters.get("adjust_type") != "qfq"
                    or parameters.get("quality_status") != "trusted"
                    or (requested_symbols is not None and
                        not expected_symbols.issubset(set(requested_symbols)))
                ):
                    raise ValueError(f"{style_name} factor run provenance is not qualified")
                if factor_evidence is not None:
                    factor_evidence[style_name] = {
                        "run_id": run_id,
                        "observed_count": len(finite),
                        "expected_count": expected_count,
                    }
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
        std = variance.sqrt()
        return {s: (v - mean) / std for s, v in items}

    def load_historical_prices(
        self,
        db: Session,
        *,
        symbols: list[str],
        end_date: date,
        window_days: int = 60,
        adjust_type: str = "qfq",
    ) -> dict[str, dict[date, Decimal]]:
        if adjust_type != "qfq":
            raise ValueError("portfolio risk returns require curated qfq prices")
        prices, _benchmark = self.load_risk_prices(
            db, symbols=symbols, benchmark_symbol=None, end_date=end_date,
            window_days=window_days,
        )
        return prices

    def load_benchmark_prices(
        self,
        db: Session,
        *,
        benchmark_symbol: str,
        end_date: date,
        window_days: int = 60,
        adjust_type: str = "qfq",
    ) -> dict[date, Decimal] | None:
        if adjust_type != "qfq":
            raise ValueError("portfolio risk returns require curated qfq prices")
        _prices, benchmark = self.load_risk_prices(
            db, symbols=[], benchmark_symbol=benchmark_symbol, end_date=end_date,
            window_days=window_days,
        )
        return benchmark

    def load_risk_prices(
        self,
        db: Session,
        *,
        symbols: list[str],
        benchmark_symbol: str | None,
        end_date: date,
        window_days: int = 60,
    ) -> tuple[dict[str, dict[date, Decimal]], dict[date, Decimal] | None]:
        """Read portfolio and benchmark QFQ prices from one curated snapshot."""
        start_date = self._shift_trading_days(db, end_date, window_days)
        requested = sorted(set(symbols) | ({benchmark_symbol} if benchmark_symbol else set()))
        prices: dict[str, dict[date, Decimal]] = {symbol: {} for symbol in requested}
        if requested:
            qfq_root = self.warm_store.prices_qfq_glob()
            if not qfq_root.exists():
                raise ValueError("curated QFQ price layer is unavailable for portfolio risk")
            release = None
            if db is not None:
                release = ReleaseRepository().get_active(db, dataset="curated.prices_qfq")
                if (
                    release is None
                    or release.quality_status != "trusted"
                    or release.max_date is None
                    or release.max_date < end_date
                ):
                    raise ValueError(
                        "active trusted QFQ release does not cover portfolio as_of_date"
                    )
                if ReleaseService.compute_parquet_checksum(qfq_root) != release.checksum:
                    raise ValueError("curated QFQ files differ from active release checksum")
            path_pattern = str(qfq_root / "**" / "*.parquet")
            placeholders = ", ".join("?" for _ in requested)
            connection = duckdb.connect(":memory:")
            try:
                rows = connection.execute(
                    "SELECT symbol, trade_date, close FROM read_parquet(?, hive_partitioning=true) "
                    f"WHERE trade_date BETWEEN ? AND ? AND symbol IN ({placeholders}) "
                    "ORDER BY symbol, trade_date",
                    [path_pattern, start_date, end_date, *requested],
                ).fetchall()
            finally:
                connection.close()
            if release is not None:
                current = ReleaseRepository().get_active(db, dataset="curated.prices_qfq")
                if (
                    current is None
                    or current.release_id != release.release_id
                    or ReleaseService.compute_parquet_checksum(qfq_root) != release.checksum
                ):
                    raise ValueError("curated QFQ release changed during portfolio risk read")
            for symbol, trade_date, close in rows:
                if trade_date in prices[symbol]:
                    raise ValueError(f"duplicate curated QFQ price: {symbol} {trade_date}")
                prices[symbol][trade_date] = close
        if db is not None:
            calendar = TradingCalendarRepository()
            for symbol, dated_prices in prices.items():
                if not dated_prices:
                    continue
                exchange = symbol_exchange(symbol)
                if exchange is None:
                    raise ValueError(f"cannot resolve exchange for portfolio risk symbol {symbol}")
                open_dates = calendar.list_open_dates(
                    db,
                    exchange=exchange,
                    start_date=min(dated_prices),
                    end_date=end_date,
                )
                missing_dates = sorted(set(open_dates) - set(dated_prices))
                if not open_dates or missing_dates:
                    raise ValueError(
                        f"curated QFQ history has an open-day gap for {symbol}: "
                        f"{missing_dates[:3]}"
                    )
        portfolio_prices = {symbol: prices[symbol] for symbol in symbols}
        benchmark_prices = (prices[benchmark_symbol] or None) if benchmark_symbol else None
        return portfolio_prices, benchmark_prices

    @staticmethod
    def _shift_trading_days(db: Session, end_date: date, window_days: int) -> date:
        from datetime import timedelta

        return end_date - timedelta(days=window_days * 2)
