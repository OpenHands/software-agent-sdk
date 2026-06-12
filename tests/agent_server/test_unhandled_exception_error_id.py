"""Tests that unhandled exceptions return a correlation ``error_id``.

The ``error_id`` is included in both the 500 response body and the server-side
log line (which carries the full traceback), so an otherwise opaque 500 a caller
receives can be matched to its traceback in the server logs.
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openhands.agent_server.api import _add_exception_handlers


@pytest.fixture
def app_with_failing_route():
    app = FastAPI()
    _add_exception_handlers(app)

    @app.get("/boom")
    async def boom():
        raise RuntimeError("kaboom")

    return app


def test_unhandled_exception_returns_error_id(app_with_failing_route):
    # raise_server_exceptions=False so the registered handler's JSONResponse is
    # returned instead of the exception being re-raised into the test.
    client = TestClient(app_with_failing_route, raise_server_exceptions=False)
    response = client.get("/boom")

    assert response.status_code == 500
    body = response.json()
    assert body["detail"] == "Internal Server Error"
    error_id = body.get("error_id")
    assert isinstance(error_id, str)
    assert len(error_id) == 32  # uuid4().hex


def test_error_id_is_unique_per_request(app_with_failing_route):
    client = TestClient(app_with_failing_route, raise_server_exceptions=False)
    first = client.get("/boom").json()["error_id"]
    second = client.get("/boom").json()["error_id"]
    assert first != second
