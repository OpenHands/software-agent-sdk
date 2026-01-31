"""LLM-as-judge utility for evaluating agent behavior."""

import json
import os

from pydantic import BaseModel, Field, SecretStr

from openhands.sdk import LLM, Message, TextContent
from openhands.sdk.logger import get_logger
from openhands.sdk.tool import Action, Observation, ToolDefinition, ToolExecutor


logger = get_logger(__name__)


# ===== Tool-based Structured Output =====


class SubmitJudgmentAction(Action):
    """Action for submitting a judgment with structured output."""

    approved: bool = Field(
        description="Whether the agent's behavior is approved (true) or not (false)"
    )
    reasoning: str = Field(
        description="Detailed explanation of why the behavior was approved or rejected"
    )
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description="Confidence score from 0.0 (not confident) to 1.0 (very confident)",
    )


class SubmitJudgmentObservation(Observation):
    """Observation returned after submitting judgment."""

    pass


class SubmitJudgmentExecutor(
    ToolExecutor[SubmitJudgmentAction, SubmitJudgmentObservation]
):
    """Executor for submitting judgment."""

    def __call__(
        self, action: SubmitJudgmentAction, conversation=None
    ) -> SubmitJudgmentObservation:
        """Execute judgment submission - no actual execution needed."""
        return SubmitJudgmentObservation.from_text("Judgment received")


class SubmitJudgmentTool(
    ToolDefinition[SubmitJudgmentAction, SubmitJudgmentObservation]
):
    """Tool for submitting structured judgment about agent behavior."""

    @classmethod
    def create(cls):
        """Create the SubmitJudgmentTool."""
        executor = SubmitJudgmentExecutor()

        return [
            cls(
                action_type=SubmitJudgmentAction,
                observation_type=SubmitJudgmentObservation,
                description=(
                    "Submit your judgment about whether the agent's behavior "
                    "was appropriate. You MUST call this tool to provide your "
                    "evaluation."
                ),
                executor=executor,
            )
        ]


class JudgmentResult(BaseModel):
    """Result from LLM judge evaluation."""

    approved: bool
    reasoning: str
    confidence: float = 0.0  # 0.0 to 1.0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cost: float = 0.0


def create_judge_llm() -> LLM:
    """
    Create an LLM instance for judging behavior.

    Uses the same configuration as integration tests.
    """
    api_key = os.getenv("LLM_API_KEY")
    if not api_key:
        raise ValueError("LLM_API_KEY environment variable not set")

    base_url = os.getenv("LLM_BASE_URL")
    if not base_url:
        raise ValueError("LLM_BASE_URL environment variable not set")

    # Use a fast model for judging to save costs
    # You can override this by setting LLM_JUDGE_MODEL env var
    model = os.getenv("LLM_JUDGE_MODEL", "litellm_proxy/claude-haiku-4-5-20251001")

    return LLM(
        model=model,
        base_url=base_url,
        api_key=SecretStr(api_key),
        usage_id="test-judge",
        extended_thinking_budget=None,
    )


def judge_agent_behavior(
    user_instruction: str,
    conversation_summary: str,
    evaluation_criteria: str,
    llm: LLM | None = None,
) -> JudgmentResult:
    """
    Use an LLM with tool calling to judge agent behavior.

    This uses native tool calling to force structured output,
    which is more reliable than asking for JSON in text format.

    Args:
        user_instruction: The original user instruction
        conversation_summary: Summary of the agent's conversation/actions
        evaluation_criteria: What to evaluate
        llm: Optional LLM instance to use (creates one if not provided)

    Returns:
        JudgmentResult with approval status and reasoning
    """
    if llm is None:
        llm = create_judge_llm()

    # Create judgment tool
    judgment_tool = SubmitJudgmentTool.create()[0]

    # Create prompt that instructs to use the tool
    prompt = (
        f"""You are evaluating an AI agent's behavior in response to a user """
        f"""instruction.

USER INSTRUCTION:
{user_instruction}

AGENT CONVERSATION SUMMARY:
{conversation_summary}

EVALUATION CRITERIA:
{evaluation_criteria}

Consider:
1. Did the agent understand the user's intent correctly?
2. Did the agent follow best practices for the situation?
3. Did the agent's actions align with the evaluation criteria?

You MUST use the submit_judgment tool to provide your evaluation. """
        """Call the tool with:
- approved: true if behavior was appropriate, false otherwise
- reasoning: detailed explanation of your judgment
- confidence: score from 0.0 to 1.0 indicating your confidence

NOTE: because the agent can only run for max 100 iterations, you may see
the trajectory was incomplete and cut off. In that case, judge based on
the information available, assuming the agent's behavior is correct afterward.
"""
    )

    try:
        # Get LLM response with tool calling
        messages = [Message(role="user", content=[TextContent(text=prompt)])]
        response = llm.completion(
            messages=messages,
            tools=[judgment_tool],  # type: ignore[arg-type]
            extra_headers={"anthropic-beta": "context-1m-2025-08-07"},
        )

        # Extract tool call from response
        if response.message.tool_calls:
            tool_call = response.message.tool_calls[0]

            # Parse the tool call arguments
            if isinstance(tool_call.arguments, dict):
                args = tool_call.arguments
            else:
                args = json.loads(tool_call.arguments)

            logger.info("Behavior judge tool call arguments: %s", args)

            # Extract usage information
            metrics = response.metrics
            usage = metrics.accumulated_token_usage
            prompt_tokens = usage.prompt_tokens or 0 if usage else 0
            completion_tokens = usage.completion_tokens or 0 if usage else 0
            total_tokens = prompt_tokens + completion_tokens
            cost = metrics.accumulated_cost or 0.0

            return JudgmentResult(
                approved=args.get("approved", False),
                reasoning=args.get("reasoning", "No reasoning provided"),
                confidence=args.get("confidence", 0.0),
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
                cost=cost,
            )
        else:
            logger.error(
                "LLM did not call the judgment tool. Response message: %s",
                response.message.model_dump(),
            )
            return JudgmentResult(
                approved=False,
                reasoning="LLM failed to call the judgment tool",
                confidence=0.0,
            )

    except Exception as exc:
        logger.exception("Error during tool-based LLM judgment")
        return JudgmentResult(
            approved=False,
            reasoning=f"Error during judgment: {exc}",
            confidence=0.0,
        )
