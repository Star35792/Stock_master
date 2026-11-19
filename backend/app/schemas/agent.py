import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class AgentChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str = Field(min_length=1, max_length=4000)
    session_id: str | None = Field(default=None, min_length=24, max_length=100)


class AgentConfirmRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str = Field(min_length=24, max_length=100)
    decision: Literal["confirm", "cancel"]


class AgentActionRead(BaseModel):
    action_id: str
    tool: str
    title: str
    parameters: dict[str, Any]
    preview: dict[str, Any]
    status: Literal["pending", "starting", "confirmed", "cancelled", "expired"] = "pending"
    task_id: int | None = None


class AgentSessionRead(BaseModel):
    session_id: str
    messages: list[dict[str, str]]
    steps: list[dict[str, Any]]
    actions: list[AgentActionRead]


class AgentMemoryWrite(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    id: str | None = Field(default=None, min_length=24, max_length=64)
    kind: Literal["preference", "constraint", "lesson"]
    content: str = Field(min_length=1, max_length=400)
    source_session_id: str | None = Field(default=None, min_length=24, max_length=64)
    revision: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def validate_identity(self):
        if (self.id is None) != (self.revision == 0):
            raise ValueError("新增记忆不传ID，修改记忆须携带当前ID与修订号。")
        if re.search(
            r"(?i)(api[_ -]?key|password|secret|token)\s*[:=]|sk-[A-Za-z0-9_-]{16,}", self.content
        ):
            raise ValueError("长期记忆不能保存凭据，请移除密钥或令牌后重试。")
        return self
