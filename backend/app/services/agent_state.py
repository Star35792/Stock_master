from functools import wraps
from secrets import token_urlsafe
from threading import Lock

from sqlalchemy.exc import SQLAlchemyError

from app.database.agent_session import AgentSessionLocal
from app.repositories.agent import AgentStateRepository
from app.services.deepseek import AgentUnavailableError


class AgentStorageUnavailableError(AgentUnavailableError):
    pass


def storage_operation(method):
    @wraps(method)
    def guarded(*args, **kwargs):
        try:
            return method(*args, **kwargs)
        except (SQLAlchemyError, OSError):
            raise AgentStorageUnavailableError(
                "智能体本地状态库暂不可用，请检查存储后重新载入；系统不会据此重放任务。"
            ) from None

    return guarded


class AgentStateStore:
    """Every SQLite transaction ends before an LLM or business-service call."""

    def __init__(self, session_factory=AgentSessionLocal):
        self.session_factory = session_factory
        self.repository = AgentStateRepository()
        self.lock = Lock()

    @storage_operation
    def load(self, session_id):
        with self.session_factory() as db:
            return self.repository.load(db, session_id)

    @storage_operation
    def list_sessions(self):
        with self.session_factory() as db:
            return self.repository.list_sessions(db)

    @storage_operation
    def save(self, state):
        with self.lock, self.session_factory() as db:
            revision = self.repository.save(db, state)
            db.commit()
            return revision

    @storage_operation
    def memories(self):
        with self.session_factory() as db:
            return self.repository.memories(db)

    @storage_operation
    def save_memory(self, request):
        with self.lock, self.session_factory() as db:
            memory_id = request.id or token_urlsafe(24)
            if (
                request.source_session_id
                and self.repository.load(db, request.source_session_id) is None
            ):
                raise ValueError("记忆来源会话不存在。")
            self.repository.save_memory(
                db,
                memory_id=memory_id,
                content=request.content,
                kind=request.kind,
                source_session_id=request.source_session_id,
                revision=request.revision,
            )
            db.commit()
        return self.memories()

    @storage_operation
    def delete_memory(self, memory_id, revision):
        with self.lock, self.session_factory() as db:
            self.repository.delete_memory(db, memory_id, revision)
            db.commit()
        return self.memories()
