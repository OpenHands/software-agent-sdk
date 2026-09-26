"""Regression tests: base_state.json is the single source of truth for the agent.

These pin the behavior behind the meta.json / base_state.json de-duplication:

* ``ConversationState.create`` resumes the agent from ``base_state.json`` when the
  caller supplies ``agent=None`` — a durable model switch survives a reload.
* Passing an explicit agent on resume keeps the legacy verify-and-override
  behavior (back-compat for callers that reconfigure on resume).
"""

import uuid

import pytest

from openhands.sdk import LLM, Agent
from openhands.sdk.conversation.state import ConversationState
from openhands.sdk.io import LocalFileStore
from openhands.sdk.workspace import LocalWorkspace


def _agent(model: str, usage_id: str = "default") -> Agent:
    return Agent(llm=LLM(model=model, usage_id=usage_id), tools=[])


def test_resume_with_agent_none_keeps_persisted_agent(tmp_path):
    """A model change written to base_state.json survives a reload with agent=None."""
    file_store = LocalFileStore(str(tmp_path))
    workspace = LocalWorkspace(working_dir=str(tmp_path))

    cid = uuid.uuid4()
    state = ConversationState.create(
        id=cid,
        agent=_agent("model-a"),
        workspace=workspace,
        file_store=file_store,
    )
    # Simulate a durable switch: rewrite the agent on base_state.json.
    state.agent = _agent("model-b")
    assert state.agent.llm.model == "model-b"

    # Reload without supplying an agent -> base_state.json is authoritative.
    reloaded = ConversationState.create(
        id=cid,
        agent=None,
        workspace=workspace,
        file_store=LocalFileStore(str(tmp_path)),
    )
    assert reloaded.agent.llm.model == "model-b"


def test_resume_with_explicit_agent_overrides(tmp_path):
    """Passing an agent on resume keeps the legacy verify-and-override behavior."""
    file_store = LocalFileStore(str(tmp_path))
    workspace = LocalWorkspace(working_dir=str(tmp_path))
    cid = uuid.uuid4()

    ConversationState.create(
        id=cid,
        agent=_agent("model-a"),
        workspace=workspace,
        file_store=file_store,
    )

    reloaded = ConversationState.create(
        id=cid,
        agent=_agent("model-c"),
        workspace=workspace,
        file_store=LocalFileStore(str(tmp_path)),
    )
    assert reloaded.agent.llm.model == "model-c"


def test_new_conversation_requires_agent(tmp_path):
    """Creating a brand-new state (no base_state.json) still requires an agent."""
    with pytest.raises(ValueError, match="agent is required"):
        ConversationState.create(
            id=uuid.uuid4(),
            agent=None,
            workspace=LocalWorkspace(working_dir=str(tmp_path)),
            file_store=LocalFileStore(str(tmp_path)),
        )
