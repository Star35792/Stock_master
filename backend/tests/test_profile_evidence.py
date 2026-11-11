from datetime import date

from app.factors.profile_evidence import (
    build_parquet_content_snapshot,
    build_profile_evidence_identity,
    hash_trade_dates,
    parse_profile_evidence_identity,
)


def _identity(**overrides: object) -> str:
    values = {
        "release_tag": "release-a",
        "factor_snapshot_sha256": "f" * 64,
        "price_snapshot_sha256": "e" * 64,
        "trade_date_sha256": "d" * 64,
        "requested_start_date": date(2024, 1, 1),
        "requested_end_date": date(2024, 12, 31),
        "start_date": date(2024, 1, 2),
        "end_date": date(2024, 12, 30),
        "trade_date_count": 240,
        "factor_name": "ret_1d",
        "factor_version": "v1-qfq",
        "code_version": "source-a",
    }
    values.update(overrides)
    return build_profile_evidence_identity(**values)  # type: ignore[arg-type]


def test_identity_is_deterministic_and_self_validating() -> None:
    encoded = _identity()
    assert encoded == _identity()
    identity = parse_profile_evidence_identity(encoded)
    assert identity is not None
    assert identity.release_tag == "release-a"
    assert identity.matches(
        factor_name="ret_1d",
        factor_version="v1-qfq",
        code_version="source-a",
        first_trade_date=date(2024, 1, 2),
        last_trade_date=date(2024, 12, 30),
        trade_date_count=240,
        trade_date_sha256="d" * 64,
    )
    assert _identity(price_snapshot_sha256="a" * 64) != encoded
    assert _identity(release_tag="release-b") != encoded


def test_legacy_and_malformed_identity_fail_closed() -> None:
    assert parse_profile_evidence_identity("trusted historical inputs") is None
    assert parse_profile_evidence_identity("evidence_identity_v1:{}") is None


def test_trade_date_hash_covers_internal_gaps() -> None:
    complete = [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)]
    assert hash_trade_dates(complete) != hash_trade_dates(
        [date(2024, 1, 2), date(2024, 1, 4)]
    )


def test_parquet_snapshot_changes_with_content(tmp_path) -> None:
    root = tmp_path / "prices_qfq"
    root.mkdir()
    parquet = root / "part.parquet"
    parquet.write_bytes(b"first")
    before = build_parquet_content_snapshot([("prices_qfq", root)])
    parquet.write_bytes(b"other")
    after = build_parquet_content_snapshot([("prices_qfq", root)])
    assert before.sha256 != after.sha256
    assert before.source_checksums[0]["sha256"] != after.source_checksums[0]["sha256"]
