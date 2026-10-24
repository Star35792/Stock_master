from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

# Research artifacts (strategy backtest tables) live in a standalone DuckDB
# file so research scripts can write while the API process holds the main
# database. Sharing the main ``Base`` would make one ``create_all`` try to
# build the same tables in both files.
research_metadata = MetaData(
    naming_convention={
        "ix": "ix_%(column_0_label)s",
        "uq": "uq_%(table_name)s_%(column_0_name)s",
        "ck": "ck_%(table_name)s_%(constraint_name)s",
        "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
        "pk": "pk_%(table_name)s",
    }
)


class ResearchBase(DeclarativeBase):
    metadata = research_metadata
