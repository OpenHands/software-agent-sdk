import os
from collections.abc import Sequence
from typing import Self

from pydantic import Field, SecretStr

from openhands.sdk import (
    LLM,
    Action,
    Agent,
    Conversation,
    ImageContent,
    Observation,
    TextContent,
    ToolDefinition,
)
from openhands.sdk.tool import (
    Tool,
    ToolExecutor,
    register_tool,
)


class RepositoryStatsAction(Action):
    directory: str = Field(..., description="Path to the repository")


class RepositoryStatsObservation(Observation):
    python_file_count: int

    @property
    def to_llm_content(self) -> Sequence[TextContent | ImageContent]:
        return [TextContent(text=f"Found {self.python_file_count} Python files.")]


class RepositoryExecutor(
    ToolExecutor[RepositoryStatsAction, RepositoryStatsObservation]
):
    def __call__(
        self,
        action: RepositoryStatsAction,
        conversation=None,  # noqa: ARG002
    ) -> RepositoryStatsObservation:
        python_file_count = 0

        for files in os.walk(action.directory):
            python_file_count += sum(1 for f in files[2] if f.endswith(".py"))
        return RepositoryStatsObservation(python_file_count=python_file_count)


_REPO_STATS_DESCRIPTION = (
    "Recursively count .py files in a directory. "
    "Use this tool to report the number of Python files."
)


class RepositoryStatsTool(
    ToolDefinition[RepositoryStatsAction, RepositoryStatsObservation]
):
    @classmethod
    def create(cls, conv_state) -> Sequence[Self]:  # noqa: ARG003
        executor = RepositoryExecutor()
        return [
            cls(
                executor=executor,
                description=_REPO_STATS_DESCRIPTION,
                action_type=RepositoryStatsAction,
                observation_type=RepositoryStatsObservation,
            )
        ]


api_key = os.getenv("LLM_API_KEY")
assert api_key is not None, "LLM_API_KEY environment variable is not set."
model = os.getenv("LLM_MODEL", "gpt-5.5")
base_url = os.getenv("LLM_BASE_URL")
llm = LLM(
    usage_id="agent",
    model=model,
    base_url=base_url,
    api_key=SecretStr(api_key),
)

register_tool(RepositoryStatsTool.name, RepositoryStatsTool)
tools = [Tool(name=RepositoryStatsTool.name)]

agent = Agent(llm=llm, tools=tools)

target_dir = os.path.dirname(os.path.abspath(__file__))
conversation = Conversation(agent=agent, workspace=os.getcwd())
try:
    conversation.send_message(
        f"Use the repository_stats tool to recursively count Python files "
        f"in {target_dir}. Report the count."
    )
    conversation.run()
finally:
    conversation.close()

print(f"EXAMPLE_COST: {llm.metrics.accumulated_cost}")
