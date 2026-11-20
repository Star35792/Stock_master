from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from app.repositories.agent import AgentStateConflict
from app.schemas.agent import (
    AgentChatRequest,
    AgentConfirmRequest,
    AgentForkRequest,
    AgentMemoryWrite,
    AgentSessionRead,
)
from app.schemas.agent_execution import AgentPlanControl
from app.schemas.agent_planning import AgentPlanDecision
from app.schemas.agent_recovery import AgentRecoveryRequest
from app.services.agent import AgentConflictError, AgentNotFoundError, AgentService
from app.services.agent_plan_recovery import AgentPlanRecoveryService
from app.services.agent_stream import AgentStream
from app.services.deepseek import AgentUnavailableError

router = APIRouter(prefix="/agent", tags=["agent"])
service = AgentService()


def _http_error(exc):
    code = (
        503
        if isinstance(exc, AgentUnavailableError)
        else 409
        if isinstance(exc, AgentConflictError)
        else 404
        if isinstance(exc, AgentNotFoundError)
        else 422
    )
    return HTTPException(status_code=code, detail=str(exc))


@router.get("/status")
def agent_status():
    return service.status()


@router.get("/sessions")
def list_agent_sessions():
    try:
        return service.store.list_sessions()
    except AgentUnavailableError as exc:
        raise _http_error(exc) from None


@router.get("/memories")
def list_agent_memories():
    try:
        return service.store.memories()
    except AgentUnavailableError as exc:
        raise _http_error(exc) from None


@router.post("/memories")
def save_agent_memory(payload: AgentMemoryWrite):
    try:
        return service.store.save_memory(payload)
    except AgentUnavailableError as exc:
        raise _http_error(exc) from None
    except AgentStateConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None


@router.delete("/memories/{memory_id}")
def delete_agent_memory(memory_id: str, revision: int = Query(ge=1)):
    try:
        return service.store.delete_memory(memory_id, revision)
    except AgentUnavailableError as exc:
        raise _http_error(exc) from None
    except AgentStateConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None


@router.post("/chat", response_model=AgentSessionRead)
def agent_chat(payload: AgentChatRequest):
    try:
        return service.chat(payload.message, payload.session_id)
    except (AgentUnavailableError, AgentConflictError, AgentNotFoundError, ValueError) as exc:
        raise _http_error(exc) from None


@router.post("/chat/stream")
def agent_chat_stream(payload: AgentChatRequest, request: Request):
    stream = AgentStream()
    return StreamingResponse(
        stream.frames(request.is_disconnected, service.chat, payload.message, payload.session_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/plans/stream")
def agent_plan_stream(payload: AgentChatRequest, request: Request):
    stream = AgentStream()
    return StreamingResponse(
        stream.frames(request.is_disconnected, service.plan, payload.message, payload.session_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/plans", response_model=AgentSessionRead)
def agent_plan(payload: AgentChatRequest):
    try:
        return service.plan(payload.message, payload.session_id)
    except (AgentUnavailableError, AgentConflictError, AgentNotFoundError, ValueError) as exc:
        raise _http_error(exc) from None


@router.post("/plans/{plan_id}/decision", response_model=AgentSessionRead)
def agent_plan_decision(plan_id: str, payload: AgentPlanDecision):
    try:
        return service.decide_plan(
            payload.session_id, plan_id, payload.version, payload.plan_hash, payload.decision
        )
    except (AgentUnavailableError, AgentConflictError, AgentNotFoundError, ValueError) as exc:
        raise _http_error(exc) from None


@router.post("/plans/{plan_id}/execution", response_model=AgentSessionRead)
def agent_plan_execution(plan_id: str, payload: AgentPlanControl):
    try:
        return service.executor.control(
            payload.session_id, plan_id, payload.version, payload.plan_hash, payload.operation
        )
    except (AgentUnavailableError, AgentConflictError, AgentNotFoundError, ValueError) as exc:
        raise _http_error(exc) from None


@router.post("/plans/{plan_id}/recovery", response_model=AgentSessionRead)
def agent_plan_recovery(plan_id: str, payload: AgentRecoveryRequest):
    try:
        return AgentPlanRecoveryService(service).recover(
            payload.session_id, plan_id, payload.version, payload.plan_hash, payload.step_id
        )
    except (AgentUnavailableError, AgentConflictError, AgentNotFoundError, ValueError) as exc:
        raise _http_error(exc) from None


@router.get("/sessions/{session_id}", response_model=AgentSessionRead)
def agent_session(session_id: str):
    try:
        return service.get(session_id)
    except (AgentConflictError, AgentNotFoundError, AgentUnavailableError) as exc:
        raise _http_error(exc) from None


@router.post("/sessions/{session_id}/fork", response_model=AgentSessionRead, status_code=201)
def agent_fork(session_id: str, payload: AgentForkRequest):
    try:
        return service.fork(session_id, payload.message_index)
    except (AgentUnavailableError, AgentConflictError, AgentNotFoundError, ValueError) as exc:
        raise _http_error(exc) from None


@router.post("/actions/{action_id}/decision", response_model=AgentSessionRead)
def agent_action_decision(
    action_id: str, payload: AgentConfirmRequest, background_tasks: BackgroundTasks
):
    try:
        result, task_id = service.decide(payload.session_id, action_id, payload.decision)
    except (AgentConflictError, AgentNotFoundError, AgentUnavailableError, ValueError) as exc:
        raise _http_error(exc) from None
    if task_id is not None:
        background_tasks.add_task(service.tools.execute, task_id)
    return result


@router.get("/tasks/{task_id}")
def agent_task(task_id: int):
    try:
        return service.tools.task(task_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
