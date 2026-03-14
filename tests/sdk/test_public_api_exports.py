from openhands.sdk import AsyncRemoteWorkspace, TokenUsage, page_iterator
from openhands.sdk.llm import TokenUsage as LLMTokenUsage
from openhands.sdk.utils import page_iterator as utils_page_iterator
from openhands.sdk.workspace import (
    AsyncRemoteWorkspace as WorkspaceAsyncRemoteWorkspace,
)
from openhands.sdk.workspace.remote import (
    AsyncRemoteWorkspace as RemoteAsyncRemoteWorkspace,
)


def test_top_level_exports_match_package_exports():
    assert TokenUsage is LLMTokenUsage
    assert page_iterator is utils_page_iterator
    assert AsyncRemoteWorkspace is WorkspaceAsyncRemoteWorkspace
    assert AsyncRemoteWorkspace is RemoteAsyncRemoteWorkspace
