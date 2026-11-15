from app.utils.discard import discard_directory


def test_discard_reclaims_current_and_previous_trash(tmp_path) -> None:
    trash = tmp_path / ".trash"
    stale = trash / "old.trash-20260101-000000"
    stale.mkdir(parents=True)
    (stale / "old.parquet").write_bytes(b"old")
    staging = tmp_path / "staging"
    staging.mkdir()
    (staging / "new.parquet").write_bytes(b"new")

    assert discard_directory(staging, trash_root=trash) is None
    assert not staging.exists()
    assert list(trash.iterdir()) == []
