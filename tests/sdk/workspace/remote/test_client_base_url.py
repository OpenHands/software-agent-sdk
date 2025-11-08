"""Test for client base_url configuration (Issue #800).

Verifies that RemoteWorkspace and AsyncRemoteWorkspace create httpx clients
with base_url set, fixing the UnsupportedProtocol error with relative URLs.
"""

import httpx

from openhands.sdk.workspace.remote.async_remote_workspace import (
    AsyncRemoteWorkspace,
)
from openhands.sdk.workspace.remote.base import RemoteWorkspace


def test_remote_workspace_client_has_base_url():
    """Test that RemoteWorkspace creates client with base_url set."""
    workspace = RemoteWorkspace(host="http://localhost:8000", working_dir="/workspace")
    client = workspace.client

    assert isinstance(client, httpx.Client)
    assert client.base_url is not None
    assert str(client.base_url) == "http://localhost:8000"


def test_async_remote_workspace_client_has_base_url():
    """Test that AsyncRemoteWorkspace creates client with base_url set."""
    workspace = AsyncRemoteWorkspace(
        host="http://localhost:8000", working_dir="/workspace"
    )
    client = workspace.client

    assert isinstance(client, httpx.AsyncClient)
    assert client.base_url is not None
    assert str(client.base_url) == "http://localhost:8000"
