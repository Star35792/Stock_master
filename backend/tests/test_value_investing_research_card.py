"""Service-level tests for the value-trap evidence and the research card.

The kernel tests hand-build the evidence; these check the other half of the
chain -- that the SQL in the service produces the trend figures the kernel
then judges, and that the external research card never leaks into the gates.

The trend test reads the control plane, so it is skipped when the developer's
database is not available.
"""

from __future__ import annotations

import os
from datetime import UTC, date, datetime
from decimal import Decimal

import duckdb
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.services.value_investing_research_card import (
    build_card,
    build_cards,
    card_to_markdown,
)

UTC = UTC


def _control_plane() -> duckdb.DuckDBPyConnection:
    """Open an explicitly enabled production control plane read-only."""
    from pathlib import Path

    if os.getenv("RUN_PRODUCTION_DATA_AUDIT") != "1":
        pytest.skip("set RUN_PRODUCTION_DATA_AUDIT=1 to run production-data audits")
    path = Path(os.getenv("PRODUCTION_CONTROL_PLANE_PATH", "data/stock_master.duckdb")).resolve()
    if not path.exists():
        pytest.fail(f"production control plane does not exist: {path}")
    try:
        return duckdb.connect(str(path), read_only=True)
    except duckdb.Error as exc:
        pytest.fail(f"cannot open production control plane read-only at {path}: {exc}")


def test_explicit_production_audit_fails_when_database_is_missing(tmp_path, monkeypatch) -> None:
    missing = tmp_path / "missing.duckdb"
    monkeypatch.setenv("RUN_PRODUCTION_DATA_AUDIT", "1")
    monkeypatch.setenv("PRODUCTION_CONTROL_PLANE_PATH", str(missing))

    with pytest.raises(pytest.fail.Exception) as caught:
        _control_plane()
    assert str(missing.resolve()) in str(caught.value)


@pytest.mark.production_data
def test_a_decline_streak_counts_backwards_from_the_latest_period():
    """The streak is "the last N years fell", not "N falls anywhere"."""
    connection = _control_plane()
    try:
        rows = connection.execute(
            """
            WITH ann AS (
              SELECT symbol, period_end,
                try_cast(json_extract_string(metrics,'$.parent_holder_net_profit')
                  AS DOUBLE) AS net_profit
              FROM fundamental.financial_statement
              WHERE statement_type='income' AND fiscal_period='FY'
                AND period_end IN (DATE '2022-12-31', DATE '2023-12-31',
                                   DATE '2024-12-31', DATE '2025-12-31')
              QUALIFY row_number() OVER (PARTITION BY symbol, period_end
                ORDER BY report_date DESC NULLS LAST) = 1
            ), piv AS (
              SELECT symbol,
                max(net_profit) FILTER (WHERE period_end=DATE '2022-12-31') AS n_base,
                max(net_profit) FILTER (WHERE period_end=DATE '2023-12-31') AS n_p1,
                max(net_profit) FILTER (WHERE period_end=DATE '2024-12-31') AS n_p2,
                max(net_profit) FILTER (WHERE period_end=DATE '2025-12-31') AS n_last
              FROM ann GROUP BY symbol
            )
            SELECT symbol,
              CASE
                WHEN n_last IS NULL OR n_p2 IS NULL THEN NULL
                WHEN n_last >= n_p2 THEN 0
                WHEN n_p2 >= n_p1 THEN 1
                WHEN n_p1 >= n_base THEN 2
                ELSE 3
              END AS streak
            FROM piv
            """
        ).fetchall()
    finally:
        connection.close()

    assert rows, "the control plane must hold annual reports"
    for symbol, streak in rows:
        assert streak is None or 0 <= streak <= 3, symbol


@pytest.mark.production_data
def test_the_trap_signals_are_observable_on_the_control_plane():
    """The trap gate must have real evidence behind it, not empty rows."""
    connection = _control_plane()
    try:
        revenue_periods = connection.execute(
            """
            SELECT count(*) FROM (
              SELECT symbol FROM fundamental.financial_statement
              WHERE statement_type='income' AND fiscal_period='FY'
                AND period_end IN (DATE '2022-12-31', DATE '2023-12-31',
                                   DATE '2024-12-31', DATE '2025-12-31')
              GROUP BY symbol HAVING count(*) = 4
            )
            """
        ).fetchone()[0]
    finally:
        connection.close()
    assert revenue_periods > 5000


# ------------------------------------------------------------ research card


def _observation(
    symbol: str,
    observation_type: str,
    payload: dict,
    *,
    source: str = "neodata",
    as_of_date: date | None = None,
    captured_at: datetime | None = None,
):
    from app.models.strategy import ExternalObservation

    return ExternalObservation(
        symbol=symbol,
        source=source,
        observation_type=observation_type,
        as_of_date=as_of_date or date(2026, 11, 10),
        captured_at=captured_at or datetime(2026, 11, 10, 8, 0, tzinfo=UTC),
        payload=payload,
        payload_version=1,
    )


def test_the_card_reads_the_business_description():
    """This is the dimension the project's own data could not answer."""
    observation = _observation(
        "601799.SH",
        "company_profile",
        {
            "business": "汽车车灯的研发、设计、制造和销售。",
            "enterprise_type": "民营相对控股企业",
            "employee_sum": "7532",
            "sw_info": (
                '[{"PlateName":"汽车","PlateType":"申万一级行业"},'
                '{"PlateName":"汽车零部件","PlateType":"申万二级行业"},'
                '{"PlateName":"车身附件及饰件","PlateType":"申万三级行业"}]'
            ),
        },
    )
    card = build_card("601799.SH", [observation], name="星宇股份")
    assert card.business == "汽车车灯的研发、设计、制造和销售。"
    assert card.enterprise_type == "民营相对控股企业"
    assert card.employee_count == 7532
    assert card.industry_path == "汽车 > 汽车零部件 > 车身附件及饰件"


def test_the_card_keeps_consensus_separate_from_realised_evidence():
    observation = _observation(
        "601799.SH",
        "consensus",
        {
            "targetPrice": 105.9828,
            "forecasts": [
                {"year": 2026, "eps": 4.87, "pe": 13.91, "netProfit": 139152.5},
                {"year": 2027, "eps": 5.9, "pe": 11.47, "netProfit": 168622.5},
            ],
        },
        source="westock",
    )
    card = build_card("601799.SH", [observation])
    assert card.consensus_eps[2026] == Decimal("4.87")
    assert card.target_price == Decimal("105.9828")
    rendered = card.to_dict()
    assert "forward_looking" in rendered
    assert "不参与本策略的任何闸门判定" in rendered["forward_looking"]["warning"]
    # The realised fields stay empty: a forecast is not a fact.
    assert card.business is None
    assert card.rating_count is None


def test_a_missing_observation_is_an_absence_not_a_zero():
    """No coverage must not read as "no risk"."""
    observation = _observation("601799.SH", "rating", {"ratingCnt": 0})
    card = build_card("601799.SH", [observation])
    assert card.rating_count == 0
    assert card.rating_buy_count is None
    rendered = card.to_dict()
    assert rendered["rating"]["rating_count"] == 0


def test_a_later_capture_wins_over_an_earlier_one():
    earlier = _observation(
        "601799.SH",
        "company_profile",
        {"business": "旧描述"},
        captured_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    later = _observation(
        "601799.SH",
        "company_profile",
        {"business": "新描述"},
        captured_at=datetime(2026, 11, 10, tzinfo=UTC),
    )
    card = build_card("601799.SH", [earlier, later])
    assert card.business == "新描述"


def test_the_card_ignores_an_unknown_observation_type():
    observation = _observation("601799.SH", "some_future_source", {"business": "不该被采纳"})
    card = build_card("601799.SH", [observation])
    assert card.business is None
    assert card.observation_refs == ()


def test_a_security_without_observations_gets_no_card():
    """An empty card would read as "we looked and found nothing"."""
    from app.database.research_base import ResearchBase

    engine = create_engine("duckdb:///:memory:")
    with engine.begin() as connection:
        connection.execute(text("CREATE SCHEMA IF NOT EXISTS strategy"))
    ResearchBase.metadata.create_all(
        bind=engine,
        tables=[ResearchBase.metadata.tables["strategy.external_observation"]],
    )
    session = sessionmaker(bind=engine)()
    try:
        cards = build_cards(session, ["600000.SH", "600001.SH"])
        assert cards == []
    finally:
        session.close()
        engine.dispose()


def test_the_markdown_names_the_forward_looking_warning():
    observation = _observation(
        "601799.SH",
        "consensus",
        {"forecasts": [{"year": 2026, "eps": 4.87}]},
        source="westock",
    )
    card = build_card("601799.SH", [observation], name="星宇股份")
    markdown = card_to_markdown(card)
    assert "601799.SH" in markdown
    assert "2026E EPS 4.87" in markdown
