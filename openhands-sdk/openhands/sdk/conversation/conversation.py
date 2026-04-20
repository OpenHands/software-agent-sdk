from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Self, overload

from openhands.sdk.agent.base import AgentBase
from openhands.sdk.conversation.base import BaseConversation
from openhands.sdk.conversation.types import (
    ConversationCallbackType,
    ConversationID,
    ConversationTokenCallbackType,
    StuckDetectionThresholds,
)
from openhands.sdk.conversation.visualizer import (
    ConversationVisualizerBase,
    DefaultConversationVisualizer,
)
from openhands.sdk.hooks import HookConfig
from openhands.sdk.logger import get_logger
from openhands.sdk.plugin import PluginSource
from openhands.sdk.secret import SecretValue
from openhands.sdk.workspace import LocalWorkspace, RemoteWorkspace


if TYPE_CHECKING:
    from openhands.sdk.conversation.impl.local_conversation import LocalConversation
    from openhands.sdk.conversation.impl.remote_conversation import RemoteConversation

logger = get_logger(__name__)


class Conversation:
    """Factory class for creating conversation instances with OpenHands agents.

    This factory automatically creates either a LocalConversation or RemoteConversation
    based on the workspace type provided. LocalConversation runs the agent locally,
    while RemoteConversation connects to a remote agent server.

    Returns:
        LocalConversation if workspace is local, RemoteConversation if workspace
        is remote.

    Example:
        ```python
        from openhands.sdk import LLM, Agent, Conversation
        from openhands.sdk.plugin import PluginSource
        from pydantic import SecretStr

        llm = LLM(model="claude-sonnet-4-20250514", api_key=SecretStr("key"))
        agent = Agent(llm=llm, tools=[])
        conversation = Conversation(
            agent=agent,
            workspace="./workspace",
            plugins=[PluginSource(source="github:org/security-plugin", ref="v1.0")],
        )
        conversation.send_message("Hello!")
        conversation.run()
        ```
    """

    @overload
    def __new__(
        cls: type[Self],
        agent: AgentBase,
        *,
        workspace: str | Path | LocalWorkspace = "workspace/project",
        plugins: list[PluginSource] | None = None,
        persistence_dir: str | Path | None = None,
        conversation_id: ConversationID | None = None,
        callbacks: list[ConversationCallbackType] | None = None,
        token_callbacks: list[ConversationTokenCallbackType] | None = None,
        hook_config: HookConfig | None = None,
        max_iteration_per_run: int = 500,
        stuck_detection: bool = True,
        stuck_detection_thresholds: (
            StuckDetectionThresholds | Mapping[str, int] | None
        ) = None,
        visualizer: (
            type[ConversationVisualizerBase] | ConversationVisualizerBase | None
        ) = DefaultConversationVisualizer,
        secrets: dict[str, SecretValue] | dict[str, str] | None = None,
        delete_on_close: bool = True,
        tags: dict[str, str] | None = None,
    ) -> "LocalConversation": ...

    @overload
    def __new__(
        cls: type[Self],
        agent: AgentBase,
        *,
        workspace: RemoteWorkspace,
        plugins: list[PluginSource] | None = None,
        conversation_id: ConversationID | None = None,
        callbacks: list[ConversationCallbackType] | None = None,
        token_callbacks: list[ConversationTokenCallbackType] | None = None,
        hook_config: HookConfig | None = None,
        max_iteration_per_run: int = 500,
        stuck_detection: bool = True,
        stuck_detection_thresholds: (
            StuckDetectionThresholds | Mapping[str, int] | None
        ) = None,
        visualizer: (
            type[ConversationVisualizerBase] | ConversationVisualizerBase | None
        ) = DefaultConversationVisualizer,
        secrets: dict[str, SecretValue] | dict[str, str] | None = None,
        delete_on_close: bool = True,
        tags: dict[str, str] | None = None,
    ) -> "RemoteConversation": ...

    def __new__(
        cls: type[Self],
        agent: AgentBase,
        *,
        workspace: str | Path | LocalWorkspace | RemoteWorkspace = "workspace/project",
        plugins: list[PluginSource] | None = None,
        persistence_dir: str | Path | None = None,
        conversation_id: ConversationID | None = None,
        callbacks: list[ConversationCallbackType] | None = None,
        token_callbacks: list[ConversationTokenCallbackType] | None = None,
        hook_config: HookConfig | None = None,
        max_iteration_per_run: int = 500,
        stuck_detection: bool = True,
        stuck_detection_thresholds: (
            StuckDetectionThresholds | Mapping[str, int] | None
        ) = None,
        visualizer: (
            type[ConversationVisualizerBase] | ConversationVisualizerBase | None
        ) = DefaultConversationVisualizer,
        secrets: dict[str, SecretValue] | dict[str, str] | None = None,
        delete_on_close: bool = True,
        tags: dict[str, str] | None = None,
    ) -> BaseConversation:
        from openhands.sdk.conversation.impl.local_conversation import LocalConversation
        from openhands.sdk.conversation.impl.remote_conversation import (
            RemoteConversation,
        )

        if isinstance(workspace, RemoteWorkspace):
            # For RemoteConversation, persistence_dir should not be used.
            if persistence_dir is not None:
                raise ValueError(
                    "persistence_dir should not be set when using RemoteConversation"
                )

            # Build effective tags by merging multiple sources:
            # 1. Workspace default tags (automation context)
            # 2. Auto-generated tags (plugins/skills)
            # 3. User-provided tags (highest priority)
            effective_tags: dict[str, str] = {}

            # 1. Start with workspace default tags
            default_tags = workspace.default_conversation_tags
            if default_tags:
                effective_tags.update(default_tags)
                logger.debug(
                    f"Merged workspace default tags: {list(default_tags.keys())}"
                )

            # 2. Auto-generate plugins/skills tag from plugins parameter
            if plugins:
                plugin_urls = [p.source_url for p in plugins if p.source_url]
                if plugin_urls:
                    effective_tags["plugins"] = ",".join(plugin_urls)
                    logger.debug(f"Added plugins tag with {len(plugin_urls)} plugin(s)")

            # 3. User-provided tags override everything
            if tags:
                effective_tags.update(tags)

            return RemoteConversation(
                agent=agent,
                plugins=plugins,
                conversation_id=conversation_id,
                callbacks=callbacks,
                token_callbacks=token_callbacks,
                hook_config=hook_config,
                max_iteration_per_run=max_iteration_per_run,
                stuck_detection=stuck_detection,
                stuck_detection_thresholds=stuck_detection_thresholds,
                visualizer=visualizer,
                workspace=workspace,
                secrets=secrets,
                delete_on_close=delete_on_close,
                tags=effective_tags if effective_tags else None,
            )

        return LocalConversation(
            agent=agent,
            plugins=plugins,
            conversation_id=conversation_id,
            callbacks=callbacks,
            token_callbacks=token_callbacks,
            hook_config=hook_config,
            max_iteration_per_run=max_iteration_per_run,
            stuck_detection=stuck_detection,
            stuck_detection_thresholds=stuck_detection_thresholds,
            visualizer=visualizer,
            workspace=workspace,
            persistence_dir=persistence_dir,
            secrets=secrets,
            delete_on_close=delete_on_close,
            tags=tags,
        )
