from datetime import date, datetime
from zoneinfo import ZoneInfo

from app.utils.trading_session import is_pre_close_capture, session_settle_at

SHANGHAI = ZoneInfo("Asia/Shanghai")
TRADE_DATE = date(2026, 11, 10)


def test_capture_during_morning_session_is_pre_close() -> None:
    # The real incident: the 2026-11-10 batch ran at 11:02 Shanghai time.
    captured_at = datetime(2026, 11, 10, 11, 2, 48, tzinfo=SHANGHAI)

    assert is_pre_close_capture(captured_at, TRADE_DATE) is True


def test_capture_just_after_the_settle_buffer_is_final() -> None:
    assert (
        is_pre_close_capture(datetime(2026, 11, 10, 15, 29, tzinfo=SHANGHAI), TRADE_DATE)
        is True
    )
    assert (
        is_pre_close_capture(datetime(2026, 11, 10, 15, 30, tzinfo=SHANGHAI), TRADE_DATE)
        is False
    )


def test_capture_on_a_later_day_is_final_for_an_earlier_session() -> None:
    # A next-morning backfill of yesterday's completed session is legitimate.
    captured_at = datetime(2026, 11, 11, 9, 15, tzinfo=SHANGHAI)

    assert is_pre_close_capture(captured_at, TRADE_DATE) is False


def test_capture_of_an_earlier_session_is_not_judged_by_clock_time_of_day() -> None:
    # 11:02 on 11-11 is after 11-10's close even though the wall-clock time is
    # "inside trading hours"; the guard compares against the bar's own session.
    captured_at = datetime(2026, 11, 11, 11, 2, tzinfo=SHANGHAI)

    assert is_pre_close_capture(captured_at, TRADE_DATE) is False


def test_naive_timestamp_is_read_as_shanghai_wall_clock() -> None:
    assert is_pre_close_capture(datetime(2026, 11, 10, 11, 0), TRADE_DATE) is True


def test_utc_timestamp_is_converted_before_comparison() -> None:
    # 03:02 UTC is 11:02 Shanghai: still inside the session.
    assert (
        is_pre_close_capture(
            datetime(2026, 11, 10, 3, 2, tzinfo=ZoneInfo("UTC")), TRADE_DATE
        )
        is True
    )


def test_missing_capture_time_is_not_evidence_of_an_early_capture() -> None:
    assert is_pre_close_capture(None, TRADE_DATE) is False


def test_settle_time_keeps_the_expected_buffer_after_the_close() -> None:
    assert session_settle_at(TRADE_DATE).isoformat() == "2026-11-10T15:30:00+08:00"
