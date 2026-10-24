from dataclasses import dataclass

from app.schemas.strategy import RollingWindowRead
from app.services.backtest import PersistedBacktestResult


@dataclass(frozen=True)
class RollingBacktestWindowResult:
    window: RollingWindowRead
    persisted: PersistedBacktestResult
    selection_summary: dict | None = None
