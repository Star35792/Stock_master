"""CI smoke check for the actual three-file production layout."""

from sqlalchemy import create_engine, inspect

from app.database.positions_session import initialize_positions_database
from app.database.research_session import initialize_research_database
from app.database.session import initialize_database


def test_three_database_initializers_are_repeatable(tmp_path):
    main = create_engine(f"duckdb:///{tmp_path / 'main.duckdb'}")
    research = create_engine(f"duckdb:///{tmp_path / 'research.duckdb'}")
    positions = create_engine(f"sqlite:///{tmp_path / 'positions.db'}")
    try:
        for _ in range(2):
            initialize_database(main)
            initialize_research_database(research)
            initialize_positions_database(positions)
        assert "daily_bar" in inspect(main).get_table_names(schema="market")
        assert "backtest_run" not in inspect(main).get_table_names(schema="strategy")
        assert "backtest_run" in inspect(research).get_table_names(schema="strategy")
        assert "position_book" in inspect(positions).get_table_names()
    finally:
        positions.dispose()
        research.dispose()
        main.dispose()
