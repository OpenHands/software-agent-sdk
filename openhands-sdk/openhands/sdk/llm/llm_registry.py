from collections.abc import Callable
from types import MappingProxyType
from typing import ClassVar
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from openhands.sdk.llm.llm import LLM
from openhands.sdk.llm.llm_session import LLMSession
from openhands.sdk.logger import get_logger


logger = get_logger(__name__)

# (log file name, log data, usage ID, model name)
LogCompletionsCallback = Callable[[str, str, str, str], None]


class RegistryEvent(BaseModel):
    llm: LLM
    session: LLMSession | None = Field(
        default=None,
        description=(
            "The session the registry keeps for the LLM's usage ID. Without "
            "one the event is about the LLM's own session."
        ),
    )

    model_config: ClassVar[ConfigDict] = ConfigDict(
        arbitrary_types_allowed=True,
    )


class LLMRegistry:
    """The LLMs of one conversation and their sessions, by role (usage ID).

    The registry maps each usage ID to the LLM that currently fills that role,
    avoiding the need to recreate LLMs with the same configuration.

    It also keeps one :class:`LLMSession` per usage ID. A session holds what the
    role accumulates (usage metrics, telemetry callbacks), so it outlives the
    LLM object in the slot: replacing or re-adding the LLM for a usage ID keeps
    the usage recorded so far. Two LLMs under different usage IDs never share a
    session, including when one was created from the other via model_copy().
    """

    registry_id: str
    retry_listener: Callable[[int, int], None] | None

    def __init__(
        self,
        retry_listener: Callable[[int, int], None] | None = None,
    ):
        """Initialize the LLM registry.

        Args:
            retry_listener: Optional callback for retry events.
        """
        self.registry_id = str(uuid4())
        self.retry_listener = retry_listener
        self._usage_to_llm: dict[str, LLM] = {}
        self._sessions: dict[str, LLMSession] = {}
        self._stats_update_callback: Callable[[], None] | None = None
        self._log_completions_callback: LogCompletionsCallback | None = None
        self.subscriber: Callable[[RegistryEvent], None] | None = None

    def subscribe(self, callback: Callable[[RegistryEvent], None]) -> None:
        """Subscribe to registry events.

        Args:
            callback: Function to call when LLMs are created or updated.
        """
        self.subscriber = callback

    def notify(self, event: RegistryEvent) -> None:
        """Notify subscribers of registry events.

        Args:
            event: The registry event to notify about.
        """
        if self.subscriber:
            try:
                self.subscriber(event)
            except Exception as e:
                logger.warning(f"Failed to emit event: {e}")

    @property
    def usage_to_llm(self) -> MappingProxyType[str, LLM]:
        """Access the internal usage-ID-to-LLM mapping (read-only view)."""

        return MappingProxyType(self._usage_to_llm)

    @property
    def sessions(self) -> MappingProxyType[str, LLMSession]:
        """The sessions by usage ID (read-only live view).

        A usage ID keeps its session for the life of the registry, also while
        no LLM is registered under it.
        """
        return MappingProxyType(self._sessions)

    def set_stats_update_callback(self, callback: Callable[[], None] | None) -> None:
        """Call ``callback`` whenever any session of this registry records usage.

        It applies to the sessions the registry has now and to those it opens
        later, so it does not need setting again when an LLM is switched.
        """
        self._stats_update_callback = callback
        for session in self._sessions.values():
            session.telemetry.set_stats_update_callback(callback)

    def set_log_completions_callback(
        self, callback: LogCompletionsCallback | None
    ) -> None:
        """Send completion logs to ``callback`` instead of writing them to disk.

        ``callback`` receives the log file name, the log data, the usage ID and
        the name of the model that was called. Like
        :meth:`set_stats_update_callback`, it covers present and future
        sessions; LLMs with ``log_completions`` off produce no logs.
        """
        self._log_completions_callback = callback
        for usage_id, session in self._sessions.items():
            self._apply_log_completions_callback(usage_id, session)

    def _apply_log_completions_callback(
        self, usage_id: str, session: LLMSession
    ) -> None:
        callback = self._log_completions_callback
        telemetry = session.telemetry
        if callback is None:
            telemetry.set_log_completions_callback(None)
            return

        def log_completion(filename: str, log_data: str) -> None:
            callback(filename, log_data, usage_id, telemetry.model_name)

        telemetry.set_log_completions_callback(log_completion)

    def add(self, llm: LLM) -> None:
        """Add an LLM instance to the registry.

        The LLM records in the session of its usage ID from then on. The first
        LLM added for a usage ID brings its own session along (a new one if it
        already serves another conversation). The registry keeps that session
        afterwards, so an LLM added later for the same usage ID continues from
        the metrics already recorded.

        Args:
            llm: The LLM instance to register.

        Raises:
            ValueError: If llm.usage_id already exists in the registry.
        """
        usage_id = llm.usage_id
        if usage_id in self._usage_to_llm:
            message = (
                f"Usage ID '{usage_id}' already exists in registry. "
                "Use a different usage_id on the LLM or "
                "call get() to retrieve the existing LLM."
            )
            raise ValueError(message)

        session = self._sessions.get(usage_id)
        if session is None:
            session = llm.session
            # One session per role: an LLM whose session is spoken for (it
            # serves another conversation, or shares it with a registered LLM
            # it was copied from) gets a new one here.
            if llm._registered_session() is not None or any(
                session is other for other in self._sessions.values()
            ):
                session = LLMSession(llm)
            self._sessions[usage_id] = session
            # Callbacks the LLM already carries stay unless the registry has
            # its own to install.
            if self._stats_update_callback is not None:
                session.telemetry.set_stats_update_callback(self._stats_update_callback)
            if self._log_completions_callback is not None:
                self._apply_log_completions_callback(usage_id, session)
        llm._join_registry_session(session)

        self._usage_to_llm[usage_id] = llm
        self.notify(RegistryEvent(llm=llm, session=session))
        logger.debug(
            f"[LLM registry {self.registry_id}]: Added LLM for usage {usage_id}"
        )

    def get(self, usage_id: str) -> LLM:
        """Get an LLM instance from the registry.

        Args:
            usage_id: Unique identifier for the LLM usage slot.

        Returns:
            The LLM instance.

        Raises:
            KeyError: If usage_id is not found in the registry.
        """
        if usage_id not in self._usage_to_llm:
            raise KeyError(
                f"Usage ID '{usage_id}' not found in registry. "
                "Use add() to register an LLM first."
            )

        logger.info(
            f"[LLM registry {self.registry_id}]: Retrieved LLM for usage {usage_id}"
        )
        return self._usage_to_llm[usage_id]

    def remove(self, usage_id: str) -> None:
        """Remove an LLM instance from the registry, if present.

        Unlike :meth:`add` and :meth:`get`, this is a no-op when ``usage_id``
        is absent, so callers can invalidate a lazily-cached slot without
        first checking for it. The session of the usage ID stays, so the usage
        it recorded is not lost.

        Args:
            usage_id: Usage slot to drop from the registry.
        """
        llm = self._usage_to_llm.pop(usage_id, None)
        if llm is not None:
            logger.debug(
                f"[LLM registry {self.registry_id}]: Removed LLM for usage {usage_id}"
            )

    def list_usage_ids(self) -> list[str]:
        """List all registered usage IDs."""

        return list(self._usage_to_llm.keys())
