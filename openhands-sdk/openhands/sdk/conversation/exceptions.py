from openhands.sdk.conversation.types import ConversationID


class ConversationRunError(RuntimeError):
    """Raised when a conversation run fails.

    Carries the conversation_id to make resuming/debugging easier while
    preserving the original exception via exception chaining.
    """

    conversation_id: ConversationID
    original_exception: BaseException

    def __init__(
        self,
        conversation_id: ConversationID,
        original_exception: BaseException,
        message: str | None = None,
    ) -> None:
        self.conversation_id = conversation_id
        self.original_exception = original_exception
        default_msg = (
            f"Conversation run failed for id={conversation_id}: {original_exception}"
        )
        super().__init__(message or default_msg)
