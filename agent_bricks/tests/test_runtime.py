from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

import runtime.main as runtime


def test_foreground_invocation(monkeypatch) -> None:
    invoke = AsyncMock(return_value={"output": [{"role": "assistant", "content": "hello"}]})
    monkeypatch.setattr(runtime, "invoke", invoke)

    response = TestClient(runtime.app).post(
        "/invocations",
        json={"input": [{"role": "user", "content": "hi"}]},
    )

    assert response.status_code == 200
    assert response.json()["output"][0]["content"] == "hello"
    invoke.assert_awaited_once_with([{"role": "user", "content": "hi"}], run_id=None)


def test_run_id_is_passed_through(monkeypatch) -> None:
    """The Agent Flow Debugger sends the run_id so tool calls land in its run."""
    invoke = AsyncMock(return_value={"output": [{"role": "assistant", "content": "ok"}]})
    monkeypatch.setattr(runtime, "invoke", invoke)
    TestClient(runtime.app).post("/invocations", json={"input": [], "run_id": "r_123"})
    invoke.assert_awaited_once_with([], run_id="r_123")


def test_custom_server_rejects_runtime_transport_fields() -> None:
    response = TestClient(runtime.app).post(
        "/invocations",
        json={"input": [], "stream": True},
    )

    assert response.status_code == 422
    assert "/api/invocations" not in runtime.app.openapi()["paths"]
