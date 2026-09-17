from sqlalchemy import create_engine, text

from app.database.session import apply_schema_compatibility


def test_schema_compatibility_adds_release_lineage_columns(tmp_path) -> None:
    engine = create_engine(f"duckdb:///{tmp_path / 'legacy.duckdb'}")
    with engine.begin() as connection:
        connection.execute(text("CREATE SCHEMA factor"))
        connection.execute(text("CREATE SCHEMA market"))
        connection.execute(text("CREATE TABLE factor.factor_run (id BIGINT)"))
        connection.execute(text("CREATE TABLE market.adjustment_factor (id BIGINT)"))

    apply_schema_compatibility(engine)
    apply_schema_compatibility(engine)

    with engine.connect() as connection:
        columns = set(
            connection.execute(
                text("""
                    SELECT table_schema, table_name, column_name
                    FROM information_schema.columns
                    WHERE (table_schema, table_name) IN (
                        ('factor', 'factor_run'),
                        ('market', 'adjustment_factor')
                    )
                """)
            ).all()
        )

    assert ("factor", "factor_run", "release_id") in columns
    assert ("market", "adjustment_factor", "release_id") in columns
    engine.dispose()
