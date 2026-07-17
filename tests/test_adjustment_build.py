import pytest

from app.services.adjustment_build import AdjustmentBuildService


def test_adjustment_build_validates_source_name() -> None:
    with pytest.raises(ValueError, match="source_name"):
        AdjustmentBuildService().rebuild_qfq(object(), source_name="")  # type: ignore[arg-type]


def test_adjustment_build_validates_version() -> None:
    with pytest.raises(ValueError, match="version"):
        AdjustmentBuildService().rebuild_qfq(object(), version="")  # type: ignore[arg-type]
