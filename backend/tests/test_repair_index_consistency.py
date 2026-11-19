import json

import duckdb
import pytest

from scripts.maintenance import repair_index_consistency as repair


@pytest.fixture
def connection():
    con = duckdb.connect(":memory:")
    con.execute("CREATE SCHEMA metadata")
    con.execute("CREATE SCHEMA market")
    con.execute("CREATE SEQUENCE system_job_log_id_seq START 3")
    con.execute(
        "CREATE TABLE metadata.job_log (id BIGINT DEFAULT nextval('system_job_log_id_seq'), "
        "job_name VARCHAR,job_type VARCHAR,status VARCHAR,started_at TIMESTAMPTZ,"
        "finished_at TIMESTAMPTZ,rows_read INTEGER,metadata JSON)"
    )
    con.execute(
        "INSERT INTO metadata.job_log (id,job_name,status,metadata) VALUES "
        "(1,'factor_pipeline','partial','{\"empty_services\":[\"valuation\"]}'),"
        "(2,'price_value_screen','failed','{\"reason\":\"missing_input\"}')"
    )
    con.execute("CREATE UNIQUE INDEX uq_job_log_id ON metadata.job_log(id)")
    con.execute("CREATE TABLE market.security (symbol VARCHAR,price DECIMAL(18,4),name VARCHAR)")
    con.execute(
        "INSERT INTO market.security VALUES ('600000.SH',12.3456,'浦发'),('688845.SH',0,NULL)"
    )
    con.execute("CREATE UNIQUE INDEX uq_security_symbol ON market.security(symbol)")
    yield con
    con.close()


def _simulate_stale_lookup(monkeypatch):
    """Exercise the discrepancy branch without corrupting a database file."""
    original_lookup = repair.lookup_count
    original_guard = repair._create_guard
    stale = {"uq_job_log_id"}

    def lookup(con, spec, key):
        if spec.name in stale and key == 2:
            return 0
        return original_lookup(con, spec, key)

    def create_guard(con, spec, name):
        original_guard(con, spec, name)
        stale.discard(spec.name)

    monkeypatch.setattr(repair, "lookup_count", lookup)
    monkeypatch.setattr(repair, "_create_guard", create_guard)


def test_audit_is_read_only_and_detects_lookup_disagreement(connection, monkeypatch):
    before = repair.fingerprint(connection, "metadata.job_log")
    _simulate_stale_lookup(monkeypatch)
    report = repair.audit(connection)
    job_probe = next(item["probe"] for item in report["indexes"] if item["name"] == "uq_job_log_id")
    assert job_probe == {
        "verified": True, "keys_checked": 2, "mismatch_count": 1, "mismatch_keys": [2],
    }
    assert repair.fingerprint(connection, "metadata.job_log") == before
    assert len(repair.inventory(connection, repair.TARGET_TABLES)) == 2


def test_rebuild_preserves_every_column_and_index_names(connection, monkeypatch):
    _simulate_stale_lookup(monkeypatch)
    report = repair.rebuild_indexes(connection, ["uq_job_log_id"])
    assert report["before"]["tables"] == report["after"]["tables"]
    assert report["repaired_indexes"] == ["uq_job_log_id"]
    probes = {item["name"]: item["probe"] for item in report["after"]["indexes"]}
    assert probes["uq_job_log_id"]["mismatch_count"] == 0
    assert {item.name for item in repair.inventory(connection, repair.TARGET_TABLES)} == {
        "uq_job_log_id", "uq_security_symbol",
    }
    with pytest.raises(duckdb.ConstraintException):
        connection.execute("INSERT INTO metadata.job_log(id) VALUES (2)")


def test_interrupted_name_recreation_keeps_equivalent_unique_guard(connection, monkeypatch):
    _simulate_stale_lookup(monkeypatch)
    before = repair.fingerprint(connection, "metadata.job_log")

    class FailedOriginalRecreation:
        def execute(self, sql, *parameters):
            if sql.startswith("CREATE UNIQUE INDEX uq_job_log_id ON"):
                raise RuntimeError("injected DDL failure")
            return connection.execute(sql, *parameters)

    with pytest.raises(repair.IndexRepairError, match="injected DDL failure") as failed:
        repair.rebuild_indexes(FailedOriginalRecreation(), ["uq_job_log_id"])
    assert failed.value.report["phase"] == "original_dropped"
    assert failed.value.report["before"]["tables"]["metadata.job_log"] == before
    assert repair.fingerprint(connection, "metadata.job_log") == before
    assert connection.execute(
        "SELECT index_name FROM duckdb_indexes() WHERE table_name='job_log'"
    ).fetchall() == [("uq_job_log_id__repair_guard",)]
    with pytest.raises(duckdb.ConstraintException):
        connection.execute("INSERT INTO metadata.job_log(id) VALUES (2)")


def test_healthy_and_unknown_indexes_cannot_be_rebuilt(connection):
    before = repair.audit(connection)
    for name in ("uq_job_log_id", "unregistered_index"):
        with pytest.raises(ValueError, match="no proven lookup inconsistency"):
            repair.rebuild_indexes(connection, [name])
    assert repair.audit(connection) == before


def test_unverifiable_null_duplicate_or_oversized_keys_refuse_repair(connection, monkeypatch):
    connection.execute("INSERT INTO metadata.job_log(id) VALUES (NULL)")
    report = repair.audit(connection)
    job = next(item for item in report["indexes"] if item["name"] == "uq_job_log_id")
    assert job["probe"]["reason"] == "null_or_duplicate_keys"
    with pytest.raises(ValueError, match="no repair"):
        repair.rebuild_indexes(connection, ["uq_job_log_id"])
    connection.execute("DELETE FROM metadata.job_log WHERE id IS NULL")
    monkeypatch.setattr(repair, "MAX_PROBE_KEYS", 1)
    report = repair.audit(connection)
    assert all(item["probe"]["reason"] == "probe_key_limit_exceeded" for item in report["indexes"])


def test_success_audit_records_identity_and_verified_data_hash(connection, monkeypatch):
    _simulate_stale_lookup(monkeypatch)
    report = repair.rebuild_indexes(connection, ["uq_job_log_id"])
    audit_id = repair.write_audit(connection, report, "operator", "restore indexed reads")
    row = connection.execute(
        "SELECT status,job_type,metadata FROM metadata.job_log WHERE id=?", [audit_id],
    ).fetchone()
    assert row[:2] == ("success", "repair")
    metadata = json.loads(row[2])
    assert metadata["operator"] == "operator"
    assert metadata["reason"] == "restore indexed reads"
    assert metadata["before"]["tables"] == metadata["after"]["tables"]
