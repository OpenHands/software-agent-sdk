"""Exceptions the agent server's conversation and event services raise.

Each one names a refusal or a state that callers handle by type. The ones that
subclass an ``openhands.sdk.errors`` category get their HTTP status from
``errors.py``; the ``ValueError`` ones keep the messages they always had, and
routes map them where they are caught.
"""

from openhands.sdk.errors import Conflict, RateLimited


class ServiceInactive(ValueError):
    """The service is not running: not started yet, or closing or closed.

    It stays a ``ValueError`` with the message ``"inactive_service"``, so
    routes and clients see the same error as before; callers catch the type.
    """

    def __init__(self) -> None:
        super().__init__("inactive_service")


class ConversationRunLimitExceeded(RateLimited, RuntimeError):
    """The server has no capacity to start another conversation run."""


class ConversationAlreadyRunning(ValueError):
    """A run was refused because the conversation is running already."""

    def __init__(self) -> None:
        super().__init__("conversation_already_running")


class GoalAlreadyRunning(ValueError):
    """A goal loop was refused because one is running already."""

    def __init__(self) -> None:
        super().__init__("goal_already_running")


class HeldForUser(ValueError):
    """A run was refused because the conversation waits on the user.

    ``status`` is the execution status it waits in (confirmation, paused,
    error or stuck). Only the user may run the conversation from here; the
    refused message, if any, stays saved.
    """

    def __init__(self, status: str) -> None:
        super().__init__(status)
        self.status = status


class CondensationUnavailable(ValueError):
    """The conversation's agent has no condenser that handles requests."""


class InvalidParentConversation(ValueError):
    """``parent_conversation_id`` is unknown, self-referential, or in
    a different workspace."""


class UnavailableAgentTool(ValueError):
    """The agent names a tool whose ``tool_module_qualnames`` module the server
    cannot import, so the agent could never be built."""


class CredentialBindingActivationRequired(Conflict, RuntimeError):
    """A runtime credential binding must be activated first; retry after it."""

    def __init__(self, detail: str) -> None:
        super().__init__(detail, extra={"retryable": True})


class CredentialBindingActivationTooLate(RuntimeError):
    """The conversation can no longer take this credential binding in place:
    it already has a different one, is not running, or is not an ACP agent."""
