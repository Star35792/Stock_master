import json
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from hashlib import sha256

from sqlalchemy.orm import Session

from app.repositories.market import DailyBarRepository


@dataclass(frozen=True)
class TrustedAdvSnapshot:
    symbol: str
    available: bool
    average_daily_amount: Decimal | None
    observation_count: int
    window: int
    minimum_observations: int
    start_date: date | None
    end_date: date | None
    input_hash: str
    daily_bar_ids: tuple[int, ...]
    observations: tuple[dict[str, str | int | None], ...]


class TrustedAverageDailyAmountService:
    def __init__(
        self,
        repository: DailyBarRepository | None = None,
        *,
        window: int = 20,
        minimum_observations: int = 15,
    ) -> None:
        if window <= 0 or minimum_observations <= 0 or minimum_observations > window:
            raise ValueError("invalid ADV observation policy")
        self.repository = repository or DailyBarRepository()
        self.window = window
        self.minimum_observations = minimum_observations

    def calculate(
        self,
        db: Session,
        *,
        symbols: set[str],
        as_of_date: date,
    ) -> dict[str, TrustedAdvSnapshot]:
        rows = self.repository.list_recent_trusted_none_amounts(
            db,
            symbols=symbols,
            end_date=as_of_date,
            window=self.window,
        )
        by_symbol: dict[str, list[object]] = {symbol: [] for symbol in symbols}
        for row in rows:
            by_symbol[row.symbol].append(row)

        snapshots = {}
        for symbol in sorted(symbols):
            observations = by_symbol[symbol]
            amounts_valid = all(row.amount is not None and row.amount > 0 for row in observations)
            available = len(observations) >= self.minimum_observations and amounts_valid
            average = (
                sum((Decimal(row.amount) for row in observations), Decimal())
                / Decimal(len(observations))
                if available
                else None
            )
            input_payload = {
                "symbol": symbol,
                "as_of_date": as_of_date.isoformat(),
                "window": self.window,
                "minimum_observations": self.minimum_observations,
                "observations": [
                    {
                        "daily_bar_id": row.daily_bar_id,
                        "trade_date": row.trade_date.isoformat(),
                        "amount": None if row.amount is None else str(row.amount),
                        "source_name": row.source_name,
                        "quality_status": row.quality_status,
                        "updated_at": row.updated_at.isoformat(),
                    }
                    for row in observations
                ],
            }
            canonical = json.dumps(
                input_payload,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
            snapshots[symbol] = TrustedAdvSnapshot(
                symbol=symbol,
                available=available,
                average_daily_amount=average,
                observation_count=len(observations),
                window=self.window,
                minimum_observations=self.minimum_observations,
                start_date=observations[0].trade_date if observations else None,
                end_date=observations[-1].trade_date if observations else None,
                input_hash=sha256(canonical.encode("utf-8")).hexdigest(),
                daily_bar_ids=tuple(row.daily_bar_id for row in observations),
                observations=tuple(input_payload["observations"]),
            )
        return snapshots
