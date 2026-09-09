from openhands.sdk import Agent, Conversation
from openhands.sdk.conversation.conversation_stats import ConversationStats
from openhands.sdk.event import Event
from openhands.sdk.testing import TestLLM


class ChildObserver:
    def __init__(self) -> None:
        self.contexts: list[tuple[str, str, str | None]] = []

    def __call__(self, event: Event) -> None:
        pass

    def for_subagent(
        self,
        *,
        task_id: str,
        subagent_type: str,
        description: str | None,
    ) -> "ChildObserver":
        child = ChildObserver()
        child.contexts.append((task_id, subagent_type, description))
        return child

    def finish_subagent(
        self,
        *,
        status: str,
        result: str | None,
        error: str | None,
        stats: ConversationStats,
    ) -> None:
        pass


class FailingObserver(ChildObserver):
    def for_subagent(
        self,
        *,
        task_id: str,
        subagent_type: str,
        description: str | None,
    ) -> ChildObserver:
        raise RuntimeError("observer unavailable")


def test_subagent_callbacks_are_opt_in_and_failure_isolated() -> None:
    ordinary_events = []
    observer = ChildObserver()
    conversation = Conversation(
        agent=Agent(llm=TestLLM.from_messages([]), tools=[]),
        callbacks=[ordinary_events.append, observer, FailingObserver()],
        visualizer=None,
    )
    try:
        callbacks = conversation.create_subagent_callbacks(
            task_id="task-1",
            subagent_type="implementer",
            description="Implement helper",
        )
    finally:
        conversation.close()

    assert len(callbacks) == 1
    assert isinstance(callbacks[0], ChildObserver)
    assert callbacks[0].contexts == [("task-1", "implementer", "Implement helper")]
    assert ordinary_events == []
