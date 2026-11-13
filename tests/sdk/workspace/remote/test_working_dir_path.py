"""Remote workspaces accept a Path working_dir like local ones (#6654)."""

from pathlib import Path

import pytest

from openhands.sdk.workspace import RemoteWorkspace, Workspace
from openhands.sdk.workspace.remote.async_remote_workspace import (
    AsyncRemoteWorkspace,
)


@pytest.mark.parametrize(
    "make",
    [
        lambda wd: RemoteWorkspace(host="http://127.0.0.1:1", working_dir=wd),
        lambda wd: Workspace(host="http://127.0.0.1:1", working_dir=wd),
        lambda wd: AsyncRemoteWorkspace(host="http://127.0.0.1:1", working_dir=wd),
    ],
    ids=["RemoteWorkspace", "Workspace-factory", "AsyncRemoteWorkspace"],
)
def test_remote_workspace_accepts_path_working_dir(make):
    workspace = make(Path("/workspace/project"))

    assert workspace.working_dir == "/workspace/project"
    assert isinstance(workspace.working_dir, str)
