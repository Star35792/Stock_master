from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker

from app.config.settings import settings
from app.database.agent_base import AgentBase


def create_agent_engine(path: str) -> Engine:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def configure(connection, _):
        cursor = connection.cursor()
        try:
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=FULL")
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_size_limit=4194304")
        finally:
            cursor.close()

    return engine


agent_engine = create_agent_engine(settings.agent_db_path)
AgentSessionLocal = sessionmaker(bind=agent_engine, expire_on_commit=False)


def initialize_agent_database(bind: Engine | None = None) -> None:
    import app.models.agent  # noqa: F401

    AgentBase.metadata.create_all(bind or agent_engine)
