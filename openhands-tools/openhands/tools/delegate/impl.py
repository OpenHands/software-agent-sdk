"""Implementation of delegate tool executor."""

import threading
from typing import TYPE_CHECKING

from openhands.sdk.conversation.impl.local_conversation import LocalConversation
from openhands.sdk.conversation.response_utils import get_agent_final_response
from openhands.sdk.logger import get_logger
from openhands.sdk.subagent import get_agent_factory
from openhands.sdk.tool.tool import ToolExecutor
from openhands.tools.delegate.definition import DelegateObservation


if TYPE_CHECKING:
    from openhands.tools.delegate.definition import DelegateAction

logger = get_logger(__name__)


class DelegateExecutor(ToolExecutor):
    """Executor for delegation operations.

    This class handles:
    - Spawning sub-agents with meaningful string identifiers (e.g., 'refactor_module')
    - Delegating tasks to sub-agents and waiting for results (blocking)
    """

    def __init__(self, max_children: int = 5):
        self._parent_conversation: LocalConversation | None = None
        # Map from user-friendly identifier to conversation
        self._sub_agents: dict[str, LocalConversation] = {}
        self._max_children: int = max_children

    @property
    def parent_conversation(self) -> LocalConversation:
        """Get the parent conversation.

        Raises:
            RuntimeError: If parent conversation has not been set yet.
        """
        if self._parent_conversation is None:
            raise RuntimeError(
                "Parent conversation not set. This should be set automatically "
                "on the first call to the executor."
            )
        return self._parent_conversation

    def __call__(  # type: ignore[override]
        self, action: "DelegateAction", conversation: LocalConversation
    ) -> DelegateObservation:
        """Execute a spawn or delegate action."""
        if self._parent_conversation is None:
            self._parent_conversation = conversation

        # Route to appropriate handler based on command
        if action.command == "spawn":
            return self._spawn_agents(action)
        elif action.command == "delegate":
            return self._delegate_tasks(action)
        else:
            return DelegateObservation.from_text(
                text=(
                    f"Unsupported command: {action.command}. "
                    "Available commands: spawn, delegate"
                ),
                command=action.command,
                is_error=True,
            )

    @staticmethod
    def _format_agent_label(agent_id: str, agent_type: str) -> str:
        """Compose a friendly label for logging and user messages."""
        type_suffix = " (default)" if agent_type == "default" else f" ({agent_type})"
        return f"{agent_id}{type_suffix}"

    def _resolve_agent_type(self, action: "DelegateAction", index: int) -> str:
        """Get the agent type for a given index, defaulting to the general agent."""
        if not action.agent_types or index >= len(action.agent_types):
            return "default"
        return action.agent_types[index].strip() or "default"

    def _spawn_agents(self, action: "DelegateAction") -> DelegateObservation:
        """Spawn sub-agents with optional agent types."""
        if not action.ids:
            return DelegateObservation.from_text(
                text="At least one ID is required for spawn action",
                command=action.command,
                is_error=True,
            )

        # Validate agent_types if provided
        if action.agent_types is not None:
            if len(action.agent_types) > len(action.ids):
                return DelegateObservation.from_text(
                    text=(
                        f"agent_types length ({len(action.agent_types)}) "
                        f"cannot exceed ids length ({len(action.ids)})"
                    ),
                    command=action.command,
                    is_error=True,
                )

        if len(self._sub_agents) + len(action.ids) > self._max_children:
            return DelegateObservation.from_text(
                text=(
                    f"Cannot spawn {len(action.ids)} agents. "
                    f"Already have {len(self._sub_agents)} agents, "
                    f"maximum is {self._max_children}"
                ),
                command=action.command,
                is_error=True,
            )

        try:
            parent_conversation = self.parent_conversation
            parent_llm = parent_conversation.agent.llm
            parent_visualizer = parent_conversation._visualizer
            workspace_path = parent_conversation.state.workspace.working_dir

            resolved_agent_types = [
                self._resolve_agent_type(action, i) for i in range(len(action.ids))
            ]

            for agent_id, agent_type in zip(action.ids, resolved_agent_types):
                # Each sub-agent gets its own LLM copy with independent metrics.
                # model_copy() shallow-copies private attrs, so reset_metrics()
                # is needed to break the shared Metrics reference with the parent.
                sub_agent_llm = parent_llm.model_copy(update={"stream": False})
                sub_agent_llm.reset_metrics()

                factory = get_agent_factory(name=agent_type)
                worker_agent = factory.factory_func(sub_agent_llm)

                # Use parent visualizer's create_sub_visualizer method if available
                # This allows custom visualizers (e.g., TUI-based) to create
                # appropriate sub-visualizers for their environment
                sub_visualizer = None
                if parent_visualizer is not None:
                    sub_visualizer = parent_visualizer.create_sub_visualizer(agent_id)

                sub_conversation = LocalConversation(
                    agent=worker_agent,
                    workspace=workspace_path,
                    visualizer=sub_visualizer,
                )

                self._sub_agents[agent_id] = sub_conversation

                # Log what type of agent was created
                logger.info(
                    f"Spawned sub-agent '{self._format_agent_label(agent_id, agent_type)}'"  # noqa: E501
                )

            # Create success message with details
            agent_details = [
                self._format_agent_label(agent_id, agent_type)
                for agent_id, agent_type in zip(action.ids, resolved_agent_types)
            ]

            message = (
                f"Successfully spawned {len(action.ids)} sub-agents: "
                f"{', '.join(agent_details)}"
            )
            return DelegateObservation.from_text(
                text=message,
                command=action.command,
            )

        except Exception as e:
            logger.error(f"Error: failed to spawn agents: {e}", exc_info=True)
            return DelegateObservation.from_text(
                text=f"failed to spawn agents: {str(e)}",
                command=action.command,
                is_error=True,
            )

    def _delegate_tasks(self, action: "DelegateAction") -> "DelegateObservation":
        """Delegate tasks to sub-agents using user-friendly identifiers
        and wait for results (blocking).

        Args:
            action: DelegateAction with tasks dict mapping identifiers to tasks
                   (e.g., {'lodging': 'Find hotels', 'activities': 'List attractions'})

        Returns:
            DelegateObservation with consolidated results from all sub-agents
        """
        if not action.tasks:
            return DelegateObservation.from_text(
                text="at least one task is required for delegate action",
                command=action.command,
                is_error=True,
            )

        # Check that all requested agent IDs exist
        missing_agents = set(action.tasks.keys()) - set(self._sub_agents.keys())
        if missing_agents:
            return DelegateObservation.from_text(
                text=(
                    f"sub-agents not found: {', '.join(missing_agents)}. "
                    f"Available agents: {', '.join(self._sub_agents.keys())}"
                ),
                command=action.command,
                is_error=True,
            )

        try:
            # Create threads to run tasks in parallel
            threads = []
            results = {}
            errors = {}

            # Get the parent agent's name from the visualizer if available
            parent_conversation = self.parent_conversation
            parent_name = None
            if hasattr(parent_conversation, "_visualizer"):
                visualizer = parent_conversation._visualizer
                if visualizer is not None:
                    parent_name = getattr(visualizer, "_name", None)

            def run_task(
                agent_id: str,
                conversation: LocalConversation,
                task: str,
                parent_name: str | None,
            ):
                """Run a single task on a sub-agent."""
                try:
                    logger.info(f"Sub-agent {agent_id} starting task: {task[:100]}...")
                    # Pass raw parent_name - visualizer handles formatting
                    conversation.send_message(task, sender=parent_name)
                    conversation.run()

                    # Extract the final response using get_agent_final_response
                    final_response = get_agent_final_response(conversation.state.events)
                    if final_response:
                        results[agent_id] = final_response
                        logger.info(f"Sub-agent {agent_id} completed successfully")
                    else:
                        results[agent_id] = "No response from sub-agent"
                        logger.warning(
                            f"Sub-agent {agent_id} completed but no final response"
                        )

                except Exception as e:
                    error_msg = f"Sub-agent {agent_id} failed: {str(e)}"
                    errors[agent_id] = error_msg
                    logger.error(error_msg, exc_info=True)

            # Start all tasks in parallel
            for agent_id, task in action.tasks.items():
                conversation = self._sub_agents[agent_id]
                thread = threading.Thread(
                    target=run_task,
                    args=(agent_id, conversation, task, parent_name),
                    name=f"Task-{agent_id}",
                )
                threads.append(thread)
                thread.start()

            # Wait for all threads to complete
            for thread in threads:
                thread.join()

            # Sync sub-agent metrics into parent conversation.
            # Sub-agent metrics are cumulative, so replace (not merge)
            # to avoid double-counting on repeated delegations.
            parent_stats = parent_conversation.conversation_stats
            for agent_id in action.tasks:
                if agent_id in self._sub_agents:
                    sub_conv = self._sub_agents[agent_id]
                    parent_stats.usage_to_metrics[f"delegate:{agent_id}"] = (
                        sub_conv.conversation_stats.get_combined_metrics()
                    )

            # Collect results in the same order as the input tasks
            all_results = []

            for agent_id in action.tasks.keys():
                if agent_id in results:
                    all_results.append(f"Agent {agent_id}: {results[agent_id]}")
                elif agent_id in errors:
                    all_results.append(f"Agent {agent_id} ERROR: {errors[agent_id]}")
                else:
                    all_results.append(f"Agent {agent_id}: No result")

            # Create comprehensive message with results
            output_text = f"Completed delegation of {len(action.tasks)} tasks"
            if errors:
                output_text += f" with {len(errors)} errors"

            if all_results:
                results_text = "\n".join(
                    f"{i}. {result}" for i, result in enumerate(all_results, 1)
                )
                output_text += f"\n\nResults:\n{results_text}"

            return DelegateObservation.from_text(
                text=output_text,
                command=action.command,
            )

        except Exception as e:
            logger.error(f"Failed to delegate tasks: {e}", exc_info=True)
            return DelegateObservation.from_text(
                text=f"failed to delegate tasks: {str(e)}",
                command=action.command,
                is_error=True,
            )
