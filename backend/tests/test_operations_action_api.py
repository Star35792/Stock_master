from datetime import UTC, datetime

from fastapi.testclient import TestClient

from app.api.routes import operations as operations_routes
from app.main import app
from app.schemas.operations import OperationsActionPreview, OperationsActionRead
from app.services.operations_actions import OperationsActionService

client = TestClient(app)


def _action_read(*, status: str = "queued") -> OperationsActionRead:
    now = datetime.now(UTC)
    return OperationsActionRead(
        action_id=42,
        action_type="daily_data_sync",
        status=status,
        phase="queued" if status == "queued" else "completed",
        parameters={},
        result=None,
        created_at=now,
        started_at=now,
    )


def test_operations_action_preview_endpoint(monkeypatch) -> None:
    def fake_preview(_db, *, action_type, parameters):
        return OperationsActionPreview(
            action_type=action_type,
            executable=True,
            summary="ready",
            parameters=parameters,
            checks={"safe": True},
        )

    monkeypatch.setattr(operations_routes.action_service, "preview", fake_preview)
    response = client.post(
        "/api/v1/operations/actions/preview",
        json={"action_type": "daily_data_sync", "parameters": {}},
    )

    assert response.status_code == 200
    assert response.json()["checks"] == {"safe": True}


def test_operations_action_start_schedules_background_execution(monkeypatch) -> None:
    executed: list[int] = []

    monkeypatch.setattr(
        operations_routes.action_service,
        "start",
        lambda _db, *, request: _action_read(),
    )
    monkeypatch.setattr(
        operations_routes.action_service,
        "execute",
        lambda action_id: executed.append(action_id),
    )
    response = client.post(
        "/api/v1/operations/actions",
        json={
            "action_type": "daily_data_sync",
            "parameters": {},
            "idempotency_key": "test-sync-0001",
        },
    )

    assert response.status_code == 202
    assert response.json()["action_id"] == 42
    assert executed == [42]


def test_operations_action_parameter_bounds() -> None:
    service = OperationsActionService()

    normalized = service._normalize_parameters(  # noqa: SLF001 - contract boundary test
        "daily_bar_gap_backfill",
        {
            "symbols": [" 600000.sh ", "600000.SH"],
            "start_date": "2026-11-01",
            "end_date": "2026-11-02",
        },
    )

    assert normalized == {
        "symbols": ["600000.SH"],
        "start_date": "2026-11-01",
        "end_date": "2026-11-02",
        "adjust_type": "qfq",
    }
