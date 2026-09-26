"""Scoped workspace operations reuse the normal sync/async request machinery."""

import inspect
from unittest.mock import patch
from uuid import uuid4

import httpx
import pytest
from pydantic import ValidationError

from openhands.sdk.workspace import RemoteWorkspace
from openhands.sdk.workspace.remote.async_remote_workspace import AsyncRemoteWorkspace


@pytest.mark.parametrize("async_mode", [False, True])
@pytest.mark.parametrize("scoped", [False, True])
@pytest.mark.asyncio
async def test_commands_files_git_and_lifecycle(async_mode, scoped, tmp_path):
    cid = uuid4() if scoped else None
    prefix = f"/api/conversations/{cid}" if cid else "/api"
    requests = []
    output_reads = 0

    def respond(request):
        nonlocal output_reads
        requests.append(request)
        assert request.headers["X-Session-API-Key"] == "test-scope-key"
        path = request.url.path
        assert path.startswith(prefix)
        if path.endswith("start_bash_command"):
            return httpx.Response(200, json={"id": "command-one"})
        if path.endswith("bash_events/search"):
            assert request.url.params["command_id__eq"] == "command-one"
            output_reads += 1
            return httpx.Response(
                200,
                json={
                    "items": [
                        {
                            "kind": "BashOutput",
                            "id": "output-one",
                            "order": 1,
                            "exit_code": None if output_reads == 1 else 0,
                            "stdout": "done",
                            "stderr": "",
                        }
                    ]
                },
            )
        if path.endswith("file/upload"):
            assert request.url.params["path"] == "/workspace/bundle.tgz"
            assert b"bundle-bytes" in request.content
            return httpx.Response(200, json={"success": True, "file_size": 12})
        if path.endswith("file/download"):
            assert request.url.params["path"] == "/workspace/bundle.tgz"
            return httpx.Response(200, content=b"bundle-bytes")
        if path.endswith("git/changes"):
            assert request.url.params["path"] == "/workspace/repo"
            return httpx.Response(200, json=[{"status": "UPDATED", "path": "file.txt"}])
        if path.endswith("git/diff"):
            assert request.url.params["path"] == "/workspace/repo/file.txt"
            return httpx.Response(200, json={"original": "before", "modified": "after"})
        if path.endswith("runtime/credentials"):
            return httpx.Response(200, json={"session_api_key": "worker-key"})
        if path.endswith("/runtime"):
            return httpx.Response(404)  # Releasing an already-gone runtime is safe.
        raise AssertionError(path)

    cls = AsyncRemoteWorkspace if async_mode else RemoteWorkspace
    http_cls = httpx.AsyncClient if async_mode else httpx.Client
    client = http_cls(
        transport=httpx.MockTransport(respond),
        base_url="http://test",
        headers={"X-Session-API-Key": "test-scope-key"},
    )
    workspace = cls(
        host="http://test",
        api_key="test-scope-key",
        working_dir="/workspace",
        runtime_conversation_id=cid,
    )

    async def invoke(method, *args):
        result = method(*args)
        return await result if inspect.isawaitable(result) else result

    with patch.object(
        httpx, "AsyncClient" if async_mode else "Client", return_value=client
    ):
        try:
            command_id = await invoke(workspace.start_command, "echo done")
            assert command_id == "command-one"
            pending = await invoke(workspace.get_command_output, command_id)
            assert pending["exit_code"] is None
            done = await invoke(workspace.get_command_output, command_id)
            assert done["exit_code"] == 0
            result = await invoke(workspace.execute_command, "echo done")
            assert result.exit_code == 0 and result.stdout == "done"
            uploaded = await invoke(
                workspace.file_upload, b"bundle-bytes", "/workspace/bundle.tgz"
            )
            assert uploaded.success
            downloaded = await invoke(
                workspace.file_download,
                "/workspace/bundle.tgz",
                tmp_path / "bundle.tgz",
            )
            assert downloaded.success
            assert (tmp_path / "bundle.tgz").read_bytes() == b"bundle-bytes"
            changes = await invoke(workspace.git_changes, "repo")
            assert [str(change.path) for change in changes] == ["file.txt"]
            diff = await invoke(workspace.git_diff, "repo/file.txt")
            assert (diff.original, diff.modified) == ("before", "after")
            if scoped:
                assert await invoke(workspace.get_runtime_session_key) == "worker-key"
                await invoke(workspace.release_runtime)
            else:
                for method in (
                    workspace.get_runtime_session_key,
                    workspace.release_runtime,
                ):
                    with pytest.raises(ValueError, match="conversation scope"):
                        await invoke(method)
            assert all(r.url.path.startswith(prefix) for r in requests)
            with pytest.raises(ValidationError):
                workspace.runtime_conversation_id = uuid4()
        finally:
            await invoke(workspace.reset_client)


@pytest.mark.asyncio
async def test_async_workspace_context_closes_client_on_failure():
    async with AsyncRemoteWorkspace(
        host="http://test", working_dir="/workspace"
    ) as workspace:
        client = workspace.client
        assert not client.is_closed
    assert client.is_closed


@pytest.mark.parametrize(
    "payload", [{}, {"session_api_key": ""}, {"session_api_key": 1}]
)
def test_rejects_missing_runtime_credential(payload):
    with httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json=payload))
    ) as client:
        with patch("httpx.Client", return_value=client):
            workspace = RemoteWorkspace(
                host="http://test",
                working_dir="/workspace",
                runtime_conversation_id=uuid4(),
            )
            with pytest.raises(ValueError, match="empty session credential"):
                workspace.get_runtime_session_key()
