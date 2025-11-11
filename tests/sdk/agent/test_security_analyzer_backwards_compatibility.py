"""Test backwards compatibility for security_analyzer field migration from Agent to ConversationState."""  # noqa: E501

import uuid

from openhands.sdk.agent import Agent
from openhands.sdk.conversation.impl.local_conversation import LocalConversation
from openhands.sdk.io.local import LocalFileStore
from openhands.sdk.llm.llm import LLM
from openhands.sdk.security.llm_analyzer import LLMSecurityAnalyzer
from openhands.sdk.workspace.local import LocalWorkspace
from openhands.sdk.workspace.workspace import Workspace


def test_security_analyzer_migrates_and_is_cleared():
    llm = LLM(model="test-model", api_key=None)
    agent = Agent(llm=llm, security_analyzer=LLMSecurityAnalyzer())

    assert agent.security_analyzer is not None

    conversation = LocalConversation(
        agent=agent, workspace=LocalWorkspace(working_dir="/tmp")
    )

    assert agent.security_analyzer is None
    assert conversation.state.security_analyzer is not None


def test_security_analyzer_reconciliation_and_migration(tmp_path):
    # Create conversation state that
    # has agent with no security analyzer
    DUMMY_BASE_STATE = """{"id": "2d73fc17-6d31-4a5c-ba0d-19c80888bdf3", "agent": {"kind": "Agent", "llm": {"model": "litellm_proxy/claude-sonnet-4-20250514", "api_key": "**********", "base_url": "https://llm-proxy.app.all-hands.dev/", "openrouter_site_url": "https://docs.all-hands.dev/", "openrouter_app_name": "OpenHands", "num_retries": 5, "retry_multiplier": 8.0, "retry_min_wait": 8, "retry_max_wait": 64, "max_message_chars": 30000, "temperature": 0.0, "top_p": 1.0, "max_input_tokens": 1000000, "max_output_tokens": 64000, "drop_params": true, "modify_params": true, "disable_stop_word": false, "caching_prompt": true, "log_completions": false, "log_completions_folder": "logs/completions", "reasoning_effort": "high", "extended_thinking_budget": 200000, "service_id": "agent", "metadata": {"trace_version": "1.0.0", "tags": ["app:openhands", "model:litellm_proxy/claude-sonnet-4-20250514", "type:agent", "web_host:unspecified", "openhands_sdk_version:1.0.0", "openhands_tools_version:1.0.0"], "session_id": "2d73fc17-6d31-4a5c-ba0d-19c80888bdf3"}, "OVERRIDE_ON_SERIALIZE": ["api_key", "aws_access_key_id", "aws_secret_access_key"]}, "tools": [{"name": "BashTool", "params": {}}, {"name": "FileEditorTool", "params": {}}, {"name": "TaskTrackerTool", "params": {}}], "mcp_config": {"mcpServers": {"fetch": {"command": "uvx", "args": ["mcp-server-fetch"]}, "repomix": {"command": "npx", "args": ["-y", "repomix@1.4.2", "--mcp"]}, "new_fetch": {"command": "npm", "args": ["mcp-server-fetch"], "env": {}, "transport": "stdio"}}}, "filter_tools_regex": "^(?!repomix)(.*)|^repomix.*pack_codebase.*$", "agent_context": {"microagents": [], "system_message_suffix": "You current working directory is: /Users/rohitmalhotra/Documents/Openhands/Openhands/openhands-cli"}, "system_prompt_filename": "system_prompt.j2", "system_prompt_kwargs": {"cli_mode": true}, "security_analyzer": null, "condenser": {"kind": "LLMSummarizingCondenser", "llm": {"model": "litellm_proxy/claude-sonnet-4-20250514", "api_key": "**********", "base_url": "https://llm-proxy.app.all-hands.dev/", "openrouter_site_url": "https://docs.all-hands.dev/", "openrouter_app_name": "OpenHands", "num_retries": 5, "retry_multiplier": 8.0, "retry_min_wait": 8, "retry_max_wait": 64, "max_message_chars": 30000, "temperature": 0.0, "top_p": 1.0, "max_input_tokens": 1000000, "max_output_tokens": 64000, "drop_params": true, "modify_params": true, "disable_stop_word": false, "caching_prompt": true, "log_completions": false, "log_completions_folder": "logs/completions", "reasoning_effort": "high", "extended_thinking_budget": 200000, "service_id": "condenser", "metadata": {"trace_version": "1.0.0", "tags": ["app:openhands", "model:litellm_proxy/claude-sonnet-4-20250514", "type:condenser", "web_host:unspecified", "openhands_sdk_version:1.0.0", "openhands_tools_version:1.0.0"], "session_id": "2d73fc17-6d31-4a5c-ba0d-19c80888bdf3"}, "OVERRIDE_ON_SERIALIZE": ["api_key", "aws_access_key_id", "aws_secret_access_key"]}, "max_size": 80, "keep_first": 4}}, "workspace": {"kind": "LocalWorkspace", "working_dir": "/Users/rohitmalhotra/Documents/Openhands/Openhands/openhands-cli"}, "persistence_dir": "/Users/rohitmalhotra/.openhands/conversations/2d73fc17-6d31-4a5c-ba0d-19c80888bdf3", "max_iterations": 500, "stuck_detection": true, "agent_status": "idle", "confirmation_policy": {"kind": "AlwaysConfirm"}, "activated_knowledge_microagents": [], "stats": {"service_to_metrics": {"agent": {"model_name": "litellm_proxy/claude-sonnet-4-20250514", "accumulated_cost": 0.0, "accumulated_token_usage": {"model": "litellm_proxy/claude-sonnet-4-20250514", "prompt_tokens": 0, "completion_tokens": 0, "cache_read_tokens": 0, "cache_write_tokens": 0, "reasoning_tokens": 0, "context_window": 0, "per_turn_token": 0, "response_id": ""}, "costs": [], "response_latencies": [], "token_usages": []}, "condenser": {"model_name": "litellm_proxy/claude-sonnet-4-20250514", "accumulated_cost": 0.0, "accumulated_token_usage": {"model": "litellm_proxy/claude-sonnet-4-20250514", "prompt_tokens": 0, "completion_tokens": 0, "cache_read_tokens": 0, "cache_write_tokens": 0, "reasoning_tokens": 0, "context_window": 0, "per_turn_token": 0, "response_id": ""}, "costs": [], "response_latencies": [], "token_usages": []}}}"""  # noqa: E501

    llm = LLM(model="test-model", api_key=None)
    file_store = LocalFileStore(root=str(tmp_path))
    file_store.write(
        "conversations/2d73fc17-6d31-4a5c-ba0d-19c80888bdf3/base_state.json",
        DUMMY_BASE_STATE,
    )

    # Update agent security analyzer to test reconciliation
    agent = Agent(llm=llm, security_analyzer=LLMSecurityAnalyzer())

    # Creating conversation should migrate security analyzer
    conversation = LocalConversation(
        agent=agent,
        workspace=Workspace(working_dir="/tmp"),
        persistence_dir=str(tmp_path),
        conversation_id=uuid.UUID("2d73fc17-6d31-4a5c-ba0d-19c80888bdf3"),
    )

    assert isinstance(conversation.state.security_analyzer, LLMSecurityAnalyzer)
    assert agent.security_analyzer is None


def test_agent_serialize_deserialize_does_not_change_analyzer(tmp_path):
    """
    Just serializing and deserializing should not wipe
    security analyzer information. Only when a conversation is
    created should the security analyzer information be transferred.
    """

    llm = LLM(model="test-model", api_key=None)
    agent = Agent(llm=llm, security_analyzer=LLMSecurityAnalyzer())

    agent = Agent.model_validate_json(agent.model_dump_json())
    assert isinstance(agent.security_analyzer, LLMSecurityAnalyzer)

    conversation = LocalConversation(
        agent=agent, workspace=Workspace(working_dir="/tmp")
    )

    assert isinstance(conversation.state.security_analyzer, LLMSecurityAnalyzer)
    assert agent.security_analyzer is None
