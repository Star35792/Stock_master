from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

# The real-position ledger lives in a standalone SQLite file, so it needs its own
# DeclarativeBase/metadata. Sharing the DuckDB ``Base`` would make one
# ``create_all`` try to build DuckDB-schema tables on SQLite and vice versa.
positions_metadata = MetaData(
    naming_convention={
        "ix": "ix_%(column_0_label)s",
        "uq": "uq_%(table_name)s_%(column_0_name)s",
        "ck": "ck_%(table_name)s_%(constraint_name)s",
        "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
        "pk": "pk_%(table_name)s",
    }
)


class PositionsBase(DeclarativeBase):
    metadata = positions_metadata
