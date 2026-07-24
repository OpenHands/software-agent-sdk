from pathlib import Path
from uuid import uuid4

import pytest
from deprecation import DeprecatedWarning

from openhands.sdk.conversation.request import StartACPConversationRequest
from openhands.sdk.workspace import LocalWorkspace


def test_start_acp_conversation_request_warns_with_current_schedule(
    tmp_path: Path,
) -> None:
    with pytest.warns(
        DeprecatedWarning,
        match="StartACPConversationRequest",
    ) as warning_records:
        request = StartACPConversationRequest(
            workspace=LocalWorkspace(working_dir=str(tmp_path)),
            agent_profile_id=uuid4(),
        )

    warning_message = str(warning_records[0].message)
    assert "deprecated as of 1.36.0" in warning_message
    assert "removed in 1.41.0" in warning_message
    assert request.agent_profile_id is not None
