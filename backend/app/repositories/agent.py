import json
from datetime import UTC, datetime

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from app.models.agent import AgentMemoryRecord, AgentSessionRecord

MAX_SESSION_BYTES = 256 * 1024


class AgentStateConflict(ValueError):
    pass


class AgentStateRepository:
    def load(self, db: Session, session_id: str) -> dict | None:
        row = db.get(AgentSessionRecord, session_id)
        if row is None:
            return None
        return {**row.state, "revision": row.revision}

    def list_sessions(self, db: Session) -> list[dict]:
        rows = db.scalars(select(AgentSessionRecord).order_by(AgentSessionRecord.updated_at.desc()))
        return [
            {
                "session_id": row.id,
                "updated_at": row.updated_at,
                "title": row.state.get("title", "研究对话"),
            }
            for row in rows
        ]

    def save(self, db: Session, state: dict) -> int:
        if len(json.dumps(state, ensure_ascii=False).encode()) > MAX_SESSION_BYTES:
            raise ValueError("会话已达到256KiB上限，请新建对话；原任务记录保留。")
        session_id, revision = state["session_id"], state.get("revision", 0)
        now = datetime.now(UTC).isoformat()
        if revision == 0:
            if db.scalar(select(func.count()).select_from(AgentSessionRecord)) >= 64:
                raise ValueError("已保留64个会话，请继续已有会话或显式整理历史记录。")
            db.add(AgentSessionRecord(id=session_id, revision=1, state=state, updated_at=now))
            db.flush()
            return 1
        changed = db.execute(
            update(AgentSessionRecord)
            .where(
                AgentSessionRecord.id == session_id,
                AgentSessionRecord.revision == revision,
            )
            .values(state=state, revision=revision + 1, updated_at=now)
            .returning(AgentSessionRecord.revision)
        ).scalar_one_or_none()
        if changed is None:
            raise AgentStateConflict("会话已被其他请求更新，请重新载入后继续。")
        return changed

    def memories(self, db: Session) -> list[dict]:
        rows = db.scalars(select(AgentMemoryRecord).order_by(AgentMemoryRecord.updated_at.desc()))
        return [
            {
                "id": row.id,
                "kind": row.kind,
                "content": row.content,
                "source_session_id": row.source_session_id,
                "revision": row.revision,
                "updated_at": row.updated_at,
            }
            for row in rows
        ]

    def save_memory(
        self,
        db: Session,
        *,
        memory_id: str,
        content: str,
        kind: str,
        source_session_id: str | None,
        revision: int = 0,
    ) -> None:
        now = datetime.now(UTC).isoformat()
        if revision == 0:
            if db.scalar(select(func.count()).select_from(AgentMemoryRecord)) >= 32:
                raise ValueError("长期记忆最多32条，请先整理已有记忆。")
            db.add(
                AgentMemoryRecord(
                    id=memory_id,
                    content=content,
                    kind=kind,
                    source_session_id=source_session_id,
                    revision=1,
                    updated_at=now,
                )
            )
        else:
            changed = db.execute(
                update(AgentMemoryRecord)
                .where(
                    AgentMemoryRecord.id == memory_id,
                    AgentMemoryRecord.revision == revision,
                )
                .values(content=content, kind=kind, revision=revision + 1, updated_at=now)
                .returning(AgentMemoryRecord.id)
            ).scalar_one_or_none()
            if changed is None:
                raise AgentStateConflict("记忆已变化，请刷新后再修改。")

    def delete_memory(self, db: Session, memory_id: str, revision: int) -> None:
        deleted = db.execute(
            delete(AgentMemoryRecord)
            .where(
                AgentMemoryRecord.id == memory_id,
                AgentMemoryRecord.revision == revision,
            )
            .returning(AgentMemoryRecord.id)
        ).scalar_one_or_none()
        if deleted is None:
            raise AgentStateConflict("记忆不存在或已变化，请刷新后再删除。")
