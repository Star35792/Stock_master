import duckdb
import pytest

from scripts.maintenance.restore_financial_unique_indexes import audit, restore


@pytest.fixture
def connection():
    con = duckdb.connect(":memory:")
    con.execute("CREATE SCHEMA fundamental")
    con.execute(
        "CREATE TABLE fundamental.financial_statement "
        "(symbol VARCHAR, statement_type VARCHAR, period VARCHAR, "
        "fiscal_year INTEGER, fiscal_period VARCHAR, value INTEGER)"
    )
    con.execute(
        "CREATE TABLE fundamental.financial_indicator "
        "(symbol VARCHAR, report VARCHAR, ability VARCHAR, "
        "indicator_name VARCHAR, value INTEGER)"
    )
    con.execute(
        "INSERT INTO fundamental.financial_statement VALUES "
        "('000001.SZ', 'income', 'annual', 2025, '4', 1)"
    )
    con.execute(
        "INSERT INTO fundamental.financial_indicator VALUES "
        "('000001.SZ', '2025-4', 'profit', 'roe', 12)"
    )
    yield con
    con.close()


def test_restore_enables_upsert_and_is_idempotent(connection):
    before = audit(connection)
    assert all(not t["present"] for t in before["targets"])
    created = restore(connection, before)
    assert set(created) == {"uq_financial_statement_key", "uq_financial_indicator_key"}
    connection.execute(
        "INSERT INTO fundamental.financial_statement VALUES "
        "('000001.SZ', 'income', 'annual', 2025, '4', 2) "
        "ON CONFLICT (symbol, statement_type, period, fiscal_year, fiscal_period) "
        "DO UPDATE SET value=excluded.value"
    )
    connection.execute(
        "INSERT INTO fundamental.financial_indicator VALUES "
        "('000001.SZ', '2025-4', 'profit', 'roe', 13) "
        "ON CONFLICT (symbol, report, ability, indicator_name) "
        "DO UPDATE SET value=excluded.value"
    )
    assert connection.execute("SELECT value FROM fundamental.financial_statement").fetchone() == (
        2,
    )
    assert connection.execute("SELECT value FROM fundamental.financial_indicator").fetchone() == (
        13,
    )
    assert restore(connection, audit(connection)) == []
    assert connection.execute("SELECT count(*) FROM duckdb_indexes()").fetchone() == (2,)


@pytest.mark.parametrize("invalid", ["duplicate", "null"])
def test_bad_keys_refuse_all_mutations(connection, invalid):
    if invalid == "duplicate":
        connection.execute(
            "INSERT INTO fundamental.financial_indicator "
            "SELECT * FROM fundamental.financial_indicator"
        )
    else:
        connection.execute(
            "INSERT INTO fundamental.financial_indicator VALUES "
            "(NULL, '2025-4', 'profit', 'roe', 1)"
        )
    with pytest.raises(ValueError, match="no repair"):
        restore(connection, audit(connection))
    assert connection.execute("SELECT count(*) FROM duckdb_indexes()").fetchone() == (0,)
    assert connection.execute(
        "SELECT count(*) FROM fundamental.financial_indicator"
    ).fetchone() == (2,)


def test_conflicting_name_rolls_back_first_index(connection):
    connection.execute(
        "CREATE INDEX uq_financial_indicator_key ON fundamental.financial_indicator(value)"
    )
    with pytest.raises(duckdb.Error):
        restore(connection, audit(connection))
    assert connection.execute("SELECT index_name FROM duckdb_indexes()").fetchall() == [
        ("uq_financial_indicator_key",)
    ]


def test_audit_never_mutates_and_accepts_equivalent_index(connection):
    connection.execute(
        "CREATE UNIQUE INDEX alternate_name "
        "ON fundamental.financial_indicator(symbol, report, ability, indicator_name)"
    )
    report = audit(connection)
    assert report["targets"][1]["present"]
    assert len(report["uniqueness_inventory"]) > 2
    assert connection.execute("SELECT count(*) FROM duckdb_indexes()").fetchone() == (1,)
