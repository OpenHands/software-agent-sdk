"""Implementation of delegate tool executor."""

import threading
from typing import TYPE_CHECKING

from openhands.sdk.conversation.impl.local_conversation import LocalConversation
from openhands.sdk.conversation.response_utils import get_agent_final_response
from openhands.sdk.logger import get_logger
from openhands.sdk.tool.tool import ToolExecutor
from openhands.tools.delegate.definition import DelegateObservation
from openhands.tools.preset.default import get_default_agent


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
            return DelegateObservation(
                command=action.command,
                message=f"Unsupported command: {action.command}",
            )

    def _spawn_agents(self, action: "DelegateAction") -> DelegateObservation:
        """Spawn sub-agents with user-friendly identifiers.

        Args:
            action: DelegateAction with command="spawn" containing list of string
                   identifiers (e.g., ['lodging', 'activities'])

        Returns:
            DelegateObservation indicating success/failure and which agents were spawned
        """
        if not action.ids:
            return DelegateObservation(
                command="spawn",
                message="Error: at least one ID is required for spawn action",
            )

        if len(self._sub_agents) + len(action.ids) > self._max_children:
            return DelegateObservation(
                command="spawn",
                message=(
                    f"Cannot spawn {len(action.ids)} agents. "
                    f"Already have {len(self._sub_agents)} agents, "
                    f"maximum is {self._max_children}"
                ),
            )

        try:
            parent_conversation = self.parent_conversation
            parent_llm = parent_conversation.agent.llm
            visualize = getattr(parent_conversation, "visualize", True)
            workspace_path = parent_conversation.state.workspace.working_dir

            for agent_id in action.ids:
                # Create a sub-agent with the specified ID
                worker_agent = get_default_agent(
                    llm=parent_llm.model_copy(
                        update={"service_id": f"sub_agent_{agent_id}"}
                    ),
                )

                sub_conversation = LocalConversation(
                    agent=worker_agent,
                    workspace=workspace_path,
                    visualize=visualize,
                    name_for_visualization=agent_id,
                )

                self._sub_agents[agent_id] = sub_conversation
                logger.info(f"Spawned sub-agent with ID: {agent_id}")

            agent_list = ", ".join(action.ids)
            message = f"Successfully spawned {len(action.ids)} sub-agents: {agent_list}"
            return DelegateObservation(
                command="spawn",
                message=message,
            )

        except Exception as e:
            logger.error(f"Error: failed to spawn agents: {e}", exc_info=True)
            return DelegateObservation(
                command="spawn",
                message=f"Error: failed to spawn agents: {str(e)}",
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
            return DelegateObservation(
                command="delegate",
                message="Error: at least one task is required for delegate action",
            )

        # Check that all requested agent IDs exist
        missing_agents = set(action.tasks.keys()) - set(self._sub_agents.keys())
        if missing_agents:
            return DelegateObservation(
                command="delegate",
                message=(
                    f"Error: sub-agents not found: {', '.join(missing_agents)}. "
                    f"Available agents: {', '.join(self._sub_agents.keys())}"
                ),
            )

        try:
            # Create threads to run tasks in parallel
            threads = []
            results = {}
            errors = {}

            def run_task(agent_id: str, conversation: LocalConversation, task: str):
                """Run a single task on a sub-agent."""
                try:
                    logger.info(f"Sub-agent {agent_id} starting task: {task[:100]}...")
                    conversation.send_message(task)
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
                    args=(agent_id, conversation, task),
                    name=f"Task-{agent_id}",
                )
                threads.append(thread)
                thread.start()

            # Wait for all threads to complete
            for thread in threads:
                thread.join()

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
            message = f"Completed delegation of {len(action.tasks)} tasks"
            if errors:
                message += f" with {len(errors)} errors"

            if all_results:
                results_text = "\n".join(
                    f"{i}. {result}" for i, result in enumerate(all_results, 1)
                )
                message += f"\n\nResults:\n{results_text}"

            return DelegateObservation(
                command="delegate",
                message=message,
            )

        except Exception as e:
            logger.error(f"Failed to delegate tasks: {e}", exc_info=True)
            return DelegateObservation(
                command="delegate",
                message=f"Error: failed to delegate tasks: {str(e)}",
            )
