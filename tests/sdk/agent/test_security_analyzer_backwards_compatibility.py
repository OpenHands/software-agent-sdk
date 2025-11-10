"""Test backwards compatibility for security_analyzer field migration from Agent to ConversationState."""  # noqa: E501

from openhands.sdk.agent import Agent
from openhands.sdk.conversation.impl.local_conversation import LocalConversation
from openhands.sdk.conversation.state import SecurityAnalyzerRecord
from openhands.sdk.llm.llm import LLM
from openhands.sdk.security.llm_analyzer import LLMSecurityAnalyzer
from openhands.sdk.workspace.local import LocalWorkspace


def test_security_analyzer_migrates_and_is_cleared():
    llm = LLM(model="test-model", api_key=None)
    agent = Agent(llm=llm, security_analyzer=LLMSecurityAnalyzer())

    assert agent.security_analyzer is not None

    conversation = LocalConversation(
        agent=agent, workspace=LocalWorkspace(working_dir="/tmp")
    )

    assert agent.security_analyzer is None
    assert conversation.state.security_analyzer is not None

    analyzer_history = conversation.state.security_analyzer_history

    # Event for initial analyzer + override during migration
    assert len(analyzer_history) == 2
    assert isinstance(analyzer_history[0], SecurityAnalyzerRecord)
    assert analyzer_history[0].analyzer_type is None
    assert analyzer_history[1].analyzer_type == "LLMSecurityAnalyzer"
