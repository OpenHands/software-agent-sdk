"""The ``limit`` bound on every paginated search endpoint must be enforced.

These endpoints used to declare ``Query(gt=0, lte=100)``. ``lte`` is not a
FastAPI/Pydantic constraint keyword (``le`` is), so the bound was silently
dropped: it never reached the OpenAPI schema and was never validated. An
oversized ``limit`` then hit a bare ``assert`` in the handler and surfaced as an
unhandled ``AssertionError`` — HTTP 500 for what is a client error.
"""

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openhands.agent_server.bash_router import bash_router
from openhands.agent_server.conversation_router import conversation_router
from openhands.agent_server.dependencies import get_event_service
from openhands.agent_server.event_router import event_router
from openhands.agent_server.event_service import EventService
from openhands.agent_server.file_router import file_router


SEARCH_ROUTES = [
    (conversation_router, "/api/conversations/search"),
    (event_router, "/api/conversations/{conversation_id}/events/search"),
    (bash_router, "/api/bash/bash_events/search"),
    (file_router, "/api/file/search_subdirs"),
]


def _limit_schema(router, path: str) -> dict:
    app = FastAPI()
    app.include_router(router, prefix="/api")
    parameters = app.openapi()["paths"][path]["get"]["parameters"]
    limit = next(p for p in parameters if p["name"] == "limit")
    return limit["schema"]


@pytest.mark.parametrize(
    "router, path", SEARCH_ROUTES, ids=[path for _, path in SEARCH_ROUTES]
)
def test_search_limit_bounds_are_published_in_the_schema(router, path):
    """The bound has to be in the schema, or generated clients cannot see it."""
    schema = _limit_schema(router, path)

    assert schema["maximum"] == 100
    assert schema["exclusiveMinimum"] == 0
    # A misspelled constraint rides along into the schema instead of raising.
    assert "lte" not in schema


@pytest.mark.parametrize("limit", [0, 101])
def test_search_events_rejects_out_of_range_limit(limit):
    """An out-of-range ``limit`` is a client error, not a server fault."""
    app = FastAPI()
    app.include_router(event_router, prefix="/api")
    service = AsyncMock(spec=EventService)
    service.search_events = AsyncMock(return_value={"items": [], "next_page_id": None})
    app.dependency_overrides[get_event_service] = lambda: service

    with TestClient(app) as client:
        response = client.get(
            f"/api/conversations/{uuid4()}/events/search", params={"limit": limit}
        )

    assert response.status_code == 422
    service.search_events.assert_not_called()


def test_search_events_accepts_the_maximum_limit():
    app = FastAPI()
    app.include_router(event_router, prefix="/api")
    service = AsyncMock(spec=EventService)
    service.search_events = AsyncMock(return_value={"items": [], "next_page_id": None})
    app.dependency_overrides[get_event_service] = lambda: service

    with TestClient(app) as client:
        response = client.get(
            f"/api/conversations/{uuid4()}/events/search", params={"limit": 100}
        )

    assert response.status_code == 200
    service.search_events.assert_called_once()
