"""
Debate orchestrator for multi-model PR review.

This module coordinates the debate between reviewer agents,
managing their communication and synthesizing the final review.
"""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass, field
from typing import Any

from debate_tools import (
    ConcludeDebateTool,
    MessageQueue,
    SendToReviewerTool,
)
from models import DebateResult, DebateState, PRInfo, ReviewerModel, ReviewResult
from prompt import (
    format_debate_initial_prompt,
    format_final_consolidation_prompt,
)
from pydantic import SecretStr

from openhands.sdk import LLM, Agent, AgentContext, Conversation, Tool, get_logger
from openhands.sdk.conversation import get_agent_final_response
from openhands.tools.preset.default import get_default_condenser, get_default_tools


logger = get_logger(__name__)


@dataclass
class ReviewerConclusion:
    """Conclusion from a reviewer."""

    model: ReviewerModel
    final_position: str
    consensus_points: str
    remaining_disagreements: str


@dataclass
class DebateSession:
    """Manages the state of a debate session."""

    pr_info: PRInfo
    initial_reviews: dict[ReviewerModel, ReviewResult]
    debate_state: DebateState = field(default_factory=DebateState)
    conversations: dict[ReviewerModel, Any] = field(default_factory=dict)
    conclusions: dict[ReviewerModel, ReviewerConclusion] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def record_conclusion(
        self,
        model: ReviewerModel,
        final_position: str,
        consensus_points: str,
        remaining_disagreements: str,
    ) -> None:
        """Record a reviewer's conclusion."""
        with self._lock:
            self.conclusions[model] = ReviewerConclusion(
                model=model,
                final_position=final_position,
                consensus_points=consensus_points,
                remaining_disagreements=remaining_disagreements,
            )
            logger.info(f"{model.display_name} has concluded")

    def all_concluded(self) -> bool:
        """Check if all reviewers have concluded."""
        with self._lock:
            return len(self.conclusions) == len(self.initial_reviews)


class DebateOrchestrator:
    """Orchestrates the debate between reviewer agents.

    The debate flow:
    1. Initialize agents with their initial reviews
    2. Send consolidated reviews to all agents
    3. Allow agents to debate using inter-agent tools
    4. Collect conclusions when agents are done
    5. Synthesize final review
    """

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        max_rounds: int = 3,
    ):
        """Initialize the debate orchestrator.

        Args:
            api_key: API key for the LLM (defaults to LLM_API_KEY env var)
            base_url: Optional base URL for the LLM API
            max_rounds: Maximum debate rounds before forcing conclusion
        """
        self._api_key = api_key or os.getenv("LLM_API_KEY")
        if not self._api_key:
            raise ValueError("LLM_API_KEY environment variable is required")

        self._base_url = base_url or os.getenv("LLM_BASE_URL")
        self._max_rounds = max_rounds

    def _create_llm_for_model(self, model: ReviewerModel) -> LLM:
        """Create an LLM instance for a specific model."""
        api_key = self._api_key
        if not api_key:
            raise ValueError("API key is required")
        config: dict[str, Any] = {
            "model": model.value,
            "api_key": SecretStr(api_key),
            "usage_id": f"debate_{model.name.lower()}",
            "drop_params": True,
            "stream": False,
        }
        if self._base_url:
            config["base_url"] = self._base_url
        return LLM(**config)

    def _create_response_handler(self, session: DebateSession) -> Any:
        """Create a response handler for inter-agent messages.

        This handler is called when an agent sends a message to another.
        It routes the message and returns the response.
        """

        def handle_message(
            sender: ReviewerModel,
            recipient: ReviewerModel | None,
            message: str,
        ) -> str:
            """Handle a message from one agent to another."""
            logger.info(
                f"Message from {sender.display_name} to "
                f"{recipient.display_name if recipient else 'all'}"
            )

            if recipient is None:
                # Broadcast - collect responses from all other agents
                responses = []
                for model in ReviewerModel:
                    if model != sender:
                        response = self._get_agent_response(session, model, message)
                        responses.append(f"**{model.display_name}**: {response}")
                return "\n\n".join(responses)
            else:
                # Direct message
                return self._get_agent_response(session, recipient, message)

        return handle_message

    def _get_agent_response(
        self,
        session: DebateSession,
        model: ReviewerModel,
        message: str,
    ) -> str:
        """Get a response from an agent to a message.

        This sends the message to the agent's conversation and extracts
        the response.
        """
        conversation = session.conversations.get(model)
        if not conversation:
            return f"{model.display_name} is not available."

        # Send the message and run the agent
        conversation.send_message(
            f"A reviewer has sent you a message:\n\n{message}\n\n"
            "Please respond to their points. You can use the send_to_reviewer tool "
            "to continue the discussion, or conclude_debate when ready."
        )
        conversation.run()

        # Extract response
        response = get_agent_final_response(conversation.state.events)
        return response or "No response."

    def _create_debate_agent(
        self,
        model: ReviewerModel,
        session: DebateSession,
        message_queue: MessageQueue,
    ) -> tuple[Agent, list[Tool]]:
        """Create a debate agent with inter-agent communication tools.

        Args:
            model: The model for this agent
            session: The debate session
            message_queue: Shared message queue

        Returns:
            Tuple of (agent, tools)
        """
        llm = self._create_llm_for_model(model)

        # Create custom tool definitions for this agent
        send_tool_defs = SendToReviewerTool.create(
            sender_model=model,
            debate_state=session.debate_state,
            message_queue=message_queue,
        )

        conclude_tool_defs = ConcludeDebateTool.create(
            sender_model=model,
            debate_state=session.debate_state,
            conclusion_callback=session.record_conclusion,
        )

        # Get base tools (no browser for CLI mode)
        tools: list[Tool | Any] = get_default_tools(enable_browser=False)

        # Add the tool definitions directly (they are already ToolDefinition instances)
        tools.extend(send_tool_defs)
        tools.extend(conclude_tool_defs)

        # Create agent context
        agent_context = AgentContext(
            system_message_suffix=f"""
You are {model.display_name}, participating in a code review debate.
Your goal is to:
1. Share your perspective on the code changes
2. Engage constructively with other reviewers
3. Work toward consensus where possible
4. Note remaining disagreements when consensus isn't reached

Use the send_to_reviewer tool to communicate with other reviewers.
When you're done debating, use conclude_debate to record your final position.
""",
        )

        agent = Agent(
            llm=llm,
            tools=tools,
            agent_context=agent_context,
            system_prompt_kwargs={"cli_mode": True},
            condenser=get_default_condenser(
                llm=llm.model_copy(
                    update={"usage_id": f"condenser_debate_{model.name}"}
                )
            ),
        )

        return agent, tools

    def run_debate(
        self,
        pr_info: PRInfo,
        initial_reviews: dict[ReviewerModel, ReviewResult],
    ) -> DebateResult:
        """Run the debate between reviewers.

        Args:
            pr_info: Pull request information
            initial_reviews: Initial reviews from each model

        Returns:
            DebateResult with the final consolidated review
        """
        logger.info("Starting debate session")

        # Initialize session
        session = DebateSession(
            pr_info=pr_info,
            initial_reviews=initial_reviews,
        )
        session.debate_state.initial_reviews = {
            model: result.review_text for model, result in initial_reviews.items()
        }
        session.debate_state.max_rounds = self._max_rounds

        # Create message queue with response handler
        message_queue = MessageQueue(
            response_handler=self._create_response_handler(session)
        )

        # Create conversations for each reviewer
        cwd = os.getcwd()
        secrets: dict[str, str] = {}
        if self._api_key:
            secrets["LLM_API_KEY"] = self._api_key
        if github_token := os.getenv("GITHUB_TOKEN"):
            secrets["GITHUB_TOKEN"] = github_token

        for model in initial_reviews.keys():
            agent, _ = self._create_debate_agent(model, session, message_queue)
            conversation = Conversation(
                agent=agent,
                workspace=cwd,
                secrets=secrets if secrets else None,
            )
            session.conversations[model] = conversation

        # Format consolidated reviews
        reviews_for_debate = {
            model.display_name: result.review_text
            for model, result in initial_reviews.items()
        }
        debate_prompt = format_debate_initial_prompt(reviews_for_debate)

        # Send consolidated reviews to all agents and start debate
        logger.info("Sending consolidated reviews to all agents")
        threads = []

        def run_agent_debate(model: ReviewerModel, conv: Any) -> None:
            """Run debate for a single agent."""
            try:
                conv.send_message(debate_prompt)
                conv.run()
            except Exception as e:
                logger.error(f"Debate error for {model.display_name}: {e}")

        for model, conversation in session.conversations.items():
            thread = threading.Thread(
                target=run_agent_debate,
                args=(model, conversation),
                name=f"Debate-{model.name}",
            )
            threads.append(thread)
            thread.start()

        # Wait for all agents to conclude or timeout
        for thread in threads:
            thread.join(timeout=300)  # 5 minute timeout per agent

        # Synthesize final review
        logger.info("Synthesizing final review")
        final_review = self._synthesize_final_review(session)

        # Calculate total cost
        total_cost = sum(r.cost for r in initial_reviews.values())
        for conv in session.conversations.values():
            metrics = conv.conversation_stats.get_combined_metrics()
            total_cost += metrics.accumulated_cost

        return DebateResult(
            pr_info=pr_info,
            initial_reviews=initial_reviews,
            debate_state=session.debate_state,
            final_consolidated_review=final_review,
            total_cost=total_cost,
        )

    def _synthesize_final_review(self, session: DebateSession) -> str:
        """Synthesize the final review from the debate.

        Args:
            session: The completed debate session

        Returns:
            Final consolidated review text
        """
        # Build consolidation based on conclusions
        if session.conclusions:
            conclusion_texts = []
            for model, conclusion in session.conclusions.items():
                consensus = conclusion.consensus_points or "None noted"
                disagreements = conclusion.remaining_disagreements or "None noted"
                conclusion_texts.append(
                    f"## {model.display_name}'s Final Position\n\n"
                    f"{conclusion.final_position}\n\n"
                    f"**Consensus Points:**\n{consensus}\n\n"
                    f"**Remaining Disagreements:**\n{disagreements}"
                )

            debate_summary = "\n\n---\n\n".join(conclusion_texts)
        else:
            # No conclusions recorded, use discussion history
            debate_summary = session.debate_state.get_discussion_history()

        # Use an LLM to create the final consolidated review
        consolidation_prompt = format_final_consolidation_prompt(debate_summary)

        # Use Claude Sonnet for final consolidation (it's usually available)
        try:
            llm = self._create_llm_for_model(ReviewerModel.CLAUDE_SONNET_4_5)
            agent = Agent(
                llm=llm,
                tools=[],  # No tools needed for consolidation
                system_prompt_kwargs={"cli_mode": True},
            )
            conversation = Conversation(
                agent=agent,
                workspace=os.getcwd(),
            )
            conversation.send_message(consolidation_prompt)
            conversation.run()

            final_review = get_agent_final_response(conversation.state.events)
            return final_review or debate_summary
        except Exception as e:
            logger.error(f"Failed to synthesize final review: {e}")
            return debate_summary
