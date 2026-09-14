"""Test that a registered secret never reaches stream deltas unmasked.

A secret can be split across chunks at any boundary the provider picks, so the
masker has to hold back partial matches; only real streams produce real
boundaries.
"""

import asyncio

from openhands.sdk import get_logger
from openhands.sdk.agent.stream_context import (
    StreamDelta,
    StreamProgress,
    StreamProgressCallbackType,
)
from openhands.sdk.conversation.impl.local_conversation import LocalConversation
from openhands.sdk.event import ActionEvent, MessageEvent
from openhands.sdk.llm import TextContent
from tests.integration.base import BaseIntegrationTest, SkipTest, TestResult


# Long enough to span several chunks on every provider; not a real credential.
SECRET_NAME = "STREAM_VERIFICATION_PHRASE"
SECRET_VALUE = "maple-harbor-" + "7f3a9c2e5b8d4a1f" * 3
SECRET_MASK = "<secret-hidden>"

# The stream mask only covers values already exported to a command, so the
# agent has to use the secret before echoing it.
INSTRUCTION = (
    f"First use the terminal to run `echo ${SECRET_NAME}`. Then reply with "
    f"exactly this verification phrase and nothing else: {SECRET_VALUE}"
)


logger = get_logger(__name__)


class StreamingSecretMaskingTest(BaseIntegrationTest):
    """Test that a registered secret never reaches stream deltas unmasked."""

    INSTRUCTION: str = INSTRUCTION

    def __init__(self, *args, **kwargs):
        self.progress: list[StreamProgress] = []
        kwargs["llm_config"] = {**kwargs["llm_config"], "stream": True}
        super().__init__(*args, **kwargs)

    @property
    def stream_callbacks(self) -> list[StreamProgressCallbackType]:
        return [self.progress.append]

    @property
    def max_iteration_per_run(self) -> int:
        return 5

    def setup(self) -> None:
        self.conversation.update_secrets({SECRET_NAME: SECRET_VALUE})

    def run_instructions(self, conversation: LocalConversation) -> None:
        # arun()/astep() is the path the agent-server streams through.
        conversation.send_message(message=self.instruction_message)
        asyncio.run(conversation.arun())

    def verify_result(self) -> TestResult:
        if not any(
            isinstance(e, ActionEvent)
            and e.action is not None
            and SECRET_NAME in e.action.model_dump_json()
            for e in self.collected_events
        ):
            raise SkipTest("agent never used the secret, so it was not exported")

        deltas = [f for f in self.progress if isinstance(f, StreamDelta)]
        if not deltas:
            return TestResult(
                success=False, reason="no stream deltas were emitted with stream=True"
            )

        # Joined per attempt and across attempts: a spurious attempt bump drops
        # the masker's held-back tail, which can leave the secret in pieces.
        streams: dict[tuple, str] = {}
        for d in deltas:
            for key in ((d.item_id, d.attempt, d.kind), (d.item_id, d.kind)):
                streams[key] = streams.get(key, "") + d.content
        leaked = [key for key, text in streams.items() if SECRET_VALUE in text]
        if leaked:
            return TestResult(
                success=False,
                reason=f"raw secret reached stream deltas for {leaked}",
            )

        agent_text = "".join(
            c.text
            for e in self.collected_events
            if isinstance(e, (MessageEvent, ActionEvent)) and e.source == "agent"
            for c in (
                e.llm_message.content if isinstance(e, MessageEvent) else e.thought
            )
            if isinstance(c, TextContent)
        )
        if SECRET_VALUE in agent_text:
            return TestResult(
                success=False, reason="raw secret reached a durable agent event"
            )

        if not any(SECRET_MASK in text for text in streams.values()):
            if SECRET_MASK in agent_text:
                return TestResult(
                    success=False,
                    reason="durable event masked the secret but no delta carried "
                    "the mask",
                )
            raise SkipTest("model did not repeat the phrase, so masking was not hit")

        return TestResult(
            success=True,
            reason=f"secret masked across {len(deltas)} delta(s)",
        )
