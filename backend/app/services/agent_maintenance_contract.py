"""One mapping for maintenance authorization, dispatch and original-task recovery."""

MAINTENANCE_ACTIONS = {
    "sync_daily_data": "daily_data_sync",
    "sync_data_and_factors": "daily_data_and_factors_sync",
    "repair_daily_gaps": "daily_bar_gap_backfill",
    "rebuild_factor_profiles": "factor_profile_rebuild",
    "repair_factor_values": "factor_values_rebuild",
    "refresh_alternative_data": "alternative_data_sync",
    "capture_tencent_quote": "tencent_quote_capture",
}
