"""Tests for workspace_router.py – the conversation workspace static server."""

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openhands.agent_server.conversation_service import ConversationService
from openhands.agent_server.dependencies import get_conversation_service
from openhands.agent_server.event_service import EventService
from openhands.agent_server.workspace_router import (
    conversation_workspace_url_path,
    workspace_router,
)
from openhands.sdk.workspace import LocalWorkspace


@pytest.fixture
def client_factory(tmp_path):
    """Build a TestClient whose conversation service points at ``tmp_path``."""

    def _build(
        *,
        conversation_id: UUID,
        workspace_dir=None,
    ) -> TestClient:
        app = FastAPI()
        app.include_router(workspace_router, prefix="/api")

        ws = workspace_dir if workspace_dir is not None else tmp_path
        event_service = AsyncMock(spec=EventService)
        event_service.stored = SimpleNamespace(
            workspace=LocalWorkspace(working_dir=str(ws))
        )

        conversation_service = AsyncMock(spec=ConversationService)

        async def _get_event_service(cid: UUID):
            if cid == conversation_id:
                return event_service
            return None

        conversation_service.get_event_service.side_effect = _get_event_service
        app.dependency_overrides[get_conversation_service] = (
            lambda: conversation_service
        )
        return TestClient(app, raise_server_exceptions=False)

    return _build


def test_url_path_helper_includes_conversation_id():
    cid = uuid4()
    assert conversation_workspace_url_path(cid) == (
        f"/api/conversations/{cid}/workspace/"
    )


def test_serve_file_at_workspace_root(client_factory, tmp_path):
    cid = uuid4()
    (tmp_path / "hello.txt").write_text("hi from workspace")
    client = client_factory(conversation_id=cid)

    resp = client.get(f"/api/conversations/{cid}/workspace/hello.txt")

    assert resp.status_code == 200
    assert resp.text == "hi from workspace"


def test_serve_file_in_subdirectory_with_inferred_content_type(
    client_factory, tmp_path
):
    cid = uuid4()
    nested = tmp_path / "reports"
    nested.mkdir()
    (nested / "report.html").write_text("<h1>ok</h1>")
    client = client_factory(conversation_id=cid)

    resp = client.get(f"/api/conversations/{cid}/workspace/reports/report.html")

    assert resp.status_code == 200
    assert resp.text == "<h1>ok</h1>"
    assert resp.headers["content-type"].startswith("text/html")


def test_root_serves_index_html_when_present(client_factory, tmp_path):
    cid = uuid4()
    (tmp_path / "index.html").write_text("<title>root</title>")
    client = client_factory(conversation_id=cid)

    resp_no_slash = client.get(
        f"/api/conversations/{cid}/workspace", follow_redirects=False
    )
    # FastAPI's default redirect_slashes points the no-trailing-slash form
    # at the trailing-slash form, but our endpoint is registered without a
    # trailing slash, so this should hit the route directly.
    assert resp_no_slash.status_code == 200
    assert resp_no_slash.text == "<title>root</title>"


def test_directory_serves_index_html(client_factory, tmp_path):
    cid = uuid4()
    sub = tmp_path / "site"
    sub.mkdir()
    (sub / "index.html").write_text("<title>sub</title>")
    client = client_factory(conversation_id=cid)

    resp = client.get(f"/api/conversations/{cid}/workspace/site/")
    assert resp.status_code == 200
    assert resp.text == "<title>sub</title>"


def test_directory_without_index_returns_404(client_factory, tmp_path):
    cid = uuid4()
    (tmp_path / "site").mkdir()
    client = client_factory(conversation_id=cid)

    resp = client.get(f"/api/conversations/{cid}/workspace/site/")
    assert resp.status_code == 404


def test_missing_file_returns_404(client_factory, tmp_path):
    cid = uuid4()
    client = client_factory(conversation_id=cid)

    resp = client.get(f"/api/conversations/{cid}/workspace/missing.txt")
    assert resp.status_code == 404


def test_path_traversal_is_rejected(client_factory, tmp_path):
    cid = uuid4()
    # Place a sibling file outside the workspace dir
    outside = tmp_path.parent / "outside.txt"
    outside.write_text("secret")

    workspace = tmp_path / "ws"
    workspace.mkdir()
    client = client_factory(conversation_id=cid, workspace_dir=workspace)

    # ``../outside.txt`` would escape the workspace root.
    resp = client.get(
        f"/api/conversations/{cid}/workspace/../outside.txt",
        # Don't let the test client normalize ".." away before sending.
        follow_redirects=False,
    )
    # Either the URL never reaches our handler (Starlette/HTTPX may strip
    # ".." segments) or our handler rejects it explicitly. Both outcomes
    # mean the secret file was *not* served.
    assert resp.status_code in {400, 404}
    assert "secret" not in resp.text


def test_unknown_conversation_returns_404(client_factory, tmp_path):
    cid = uuid4()
    other = uuid4()
    client = client_factory(conversation_id=cid)

    resp = client.get(f"/api/conversations/{other}/workspace/anything.txt")
    assert resp.status_code == 404
