"""Common test fixtures and utilities."""

import uuid
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from pydantic import SecretStr

from openhands.sdk import Agent
from openhands.sdk.conversation.state import ConversationState
from openhands.sdk.io import InMemoryFileStore
from openhands.sdk.llm import LLM
from openhands.sdk.tool import ToolExecutor
from openhands.sdk.workspace import LocalWorkspace


REPO_ROOT = Path(__file__).resolve().parent.parent
TOKENIZER_FIXTURES_DIR = REPO_ROOT / "tests" / "fixtures" / "tokenizers"
QWEN3_TOKENIZER_CONFIG = (
    TOKENIZER_FIXTURES_DIR / "qwen3-4b-instruct-2507-tokenizer_config.json"
)


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("examples")
    group.addoption(
        "--run-examples",
        action="store_true",
        default=False,
        help="Execute example scripts. Disabled by default for faster test runs.",
    )
    group.addoption(
        "--examples-results-dir",
        action="store",
        default=None,
        help=(
            "Directory to store per-example JSON results "
            "(defaults to .example-test-results)."
        ),
    )


@pytest.fixture(scope="session")
def examples_enabled(pytestconfig: pytest.Config) -> bool:
    return bool(pytestconfig.getoption("--run-examples"))


@pytest.fixture(scope="session")
def examples_results_dir(pytestconfig: pytest.Config) -> Path:
    configured = pytestconfig.getoption("--examples-results-dir")
    result_dir = (
        Path(configured)
        if configured is not None
        else REPO_ROOT / ".example-test-results"
    )
    result_dir.mkdir(parents=True, exist_ok=True)
    if not hasattr(pytestconfig, "workerinput"):
        for existing in result_dir.glob("*.json"):
            existing.unlink()
    return result_dir


@pytest.fixture(scope="session")
def tokenizer_fixtures_dir() -> Path:
    """Get the tokenizer fixtures directory path."""
    return TOKENIZER_FIXTURES_DIR


@pytest.fixture(scope="session")
def qwen3_tokenizer_config_path(tokenizer_fixtures_dir: Path) -> Path:
    """Path to the cached Qwen3 tokenizer config fixture."""
    return tokenizer_fixtures_dir / "qwen3-4b-instruct-2507-tokenizer_config.json"


@pytest.fixture
def mock_llm():
    """Create a standard mock LLM instance for testing."""
    return LLM(
        model="gpt-4o",
        api_key=SecretStr("test-key"),
        usage_id="test-llm",
        num_retries=2,
        retry_min_wait=1,
        retry_max_wait=2,
    )


@pytest.fixture
def mock_conversation_state(mock_llm, tmp_path):
    """Create a standard mock ConversationState for testing."""
    agent = Agent(llm=mock_llm)
    workspace = LocalWorkspace(working_dir=str(tmp_path))

    state = ConversationState(
        id=uuid.uuid4(),
        workspace=workspace,
        persistence_dir=str(tmp_path / ".state"),
        agent=agent,
    )

    # Set up filestore for state persistence
    state._fs = InMemoryFileStore()
    state._autosave_enabled = False

    return state


@pytest.fixture
def mock_tool():
    """Create a mock tool for testing."""

    class MockExecutor(ToolExecutor):
        def __call__(self, action, conversation=None):
            return MagicMock(output="mock output", metadata=MagicMock(exit_code=0))

    # Create a simple mock tool without complex dependencies
    mock_tool = MagicMock()
    mock_tool.name = "mock_tool"
    mock_tool.executor = MockExecutor()
    return mock_tool


def create_mock_litellm_response(
    content: str = "Test response",
    response_id: str = "test-id",
    model: str = "gpt-4o",
    prompt_tokens: int = 10,
    completion_tokens: int = 5,
    finish_reason: str = "stop",
):
    """Helper function to create properly structured LiteLLM mock responses.

    Args:
        content: Response content
        response_id: Unique response ID
        model: Model name
        prompt_tokens: Number of prompt tokens
        completion_tokens: Number of completion tokens
        finish_reason: Reason for completion
    """
    from litellm.types.utils import (
        Choices,
        Message as LiteLLMMessage,
        ModelResponse,
        Usage,
    )

    # Create proper LiteLLM message
    message = LiteLLMMessage(content=content, role="assistant")

    # Create proper choice
    choice = Choices(finish_reason=finish_reason, index=0, message=message)

    # Create proper usage
    usage = Usage(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=prompt_tokens + completion_tokens,
    )

    # Create proper ModelResponse
    response = ModelResponse(
        id=response_id,
        choices=[choice],
        created=1234567890,
        model=model,
        object="chat.completion",
        usage=usage,
    )

    return response


@pytest.fixture(autouse=True)
def suppress_logging(monkeypatch):
    """Suppress logging during tests to reduce noise."""
    mock_logger = MagicMock()
    monkeypatch.setattr("openhands.sdk.llm.llm.logger", mock_logger)
