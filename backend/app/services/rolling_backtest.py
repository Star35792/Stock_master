from dataclasses import dataclass

from app.backtest.curve_stitch import CombinedEquityCurve
from app.schemas.strategy import RollingWindowRead
from app.services.backtest import PersistedBacktestResult


@dataclass(frozen=True)
class RollingBacktestWindowResult:
    window: RollingWindowRead
    persisted: PersistedBacktestResult
    selection_summary: dict | None = None


@dataclass(frozen=True)
class RollingBacktestResult:
    """Run-level wrapper returned by ``run_rolling_factor_selection``.

    ``windows`` are the individual test-window runs; ``combined`` is the stitched
    per-window net equity curve (or ``None`` when windows overlap and stitching
    is refused); ``declarations`` carry the non-tradable / overlap-policy
    disclosures for the combined curve.
    """

    windows: list[RollingBacktestWindowResult]
    combined: CombinedEquityCurve | None
    declarations: dict[str, str]
