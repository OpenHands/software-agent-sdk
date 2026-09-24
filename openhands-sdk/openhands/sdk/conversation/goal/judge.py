"""LLM judge that decides whether a ``/goal`` objective is complete.

This is the reusable kernel of the goal feature: a pure
``objective + transcript -> verdict`` evaluator with no dependency on the
critic machinery. The ``/goal`` runner uses it to drive continuation, but it
can equally back a status command, a stop hook, or a server endpoint.
"""

import contextlib
import json
import re
from collections.abc import Sequence
from typing import Any

from pydantic import BaseModel, Field

from openhands.sdk.conversation.goal.prompts import JUDGE_PROMPT
from openhands.sdk.event import Event, LLMConvertibleEvent
from openhands.sdk.llm import LLM, Message, TextContent, content_to_str
from openhands.sdk.logger import get_logger


logger = get_logger(__name__)


class GoalVerdict(BaseModel):
    """The judge's verdict on whether the objective is complete."""

    score: float = Field(
        ge=0.0,
        le=1.0,
        description="Probability (0-1) that the full objective is provably done.",
    )
    complete: bool = Field(
        description="Whether the judge considers the objective complete."
    )
    missing: str = Field(
        default="",
        description="Concise description of what remains, or empty if complete.",
    )


def judge_goal(judge_llm: LLM, objective: str, events: Sequence[Event]) -> GoalVerdict:
    """Audit the transcript and decide whether ``objective`` is complete.

    Args:
        judge_llm: The second LLM that grades completion.
        objective: The goal to audit against.
        events: Conversation events (non-LLM events are ignored).

    Returns:
        A GoalVerdict. On a judge response that cannot be parsed, returns a
        conservative low score so the caller keeps working rather than
        falsely finishing.
    """
    convertible = [e for e in events if isinstance(e, LLMConvertibleEvent)]
    transcript = _render_transcript(convertible)
    prompt = JUDGE_PROMPT.format(objective=objective, transcript=transcript)

    # The judge only needs the verdict text. Force non-streaming so reusing a
    # streaming agent LLM as the judge does not trip completion()'s requirement
    # of an on_token callback when stream=True.
    if judge_llm.stream:
        judge_llm = judge_llm.model_copy(update={"stream": False})
    response = judge_llm.completion(
        messages=[Message(role="user", content=[TextContent(text=prompt)])]
    )
    verdict = _parse_verdict(response.message)
    logger.debug("judge_goal verdict: %s", verdict)
    return verdict


def _render_transcript(events: Sequence[LLMConvertibleEvent]) -> str:
    """Render events as a plain ``role: text`` transcript for the judge.

    The agent's ``system`` prompt is excluded: it is large (~thousands of tokens)
    and carries no goal-specific evidence, so it would only inflate the judge's
    token cost on every audit.
    """
    turns = [
        (msg.role, text)
        for msg in LLMConvertibleEvent.events_to_messages(list(events))
        if msg.role != "system"
        and (text := "\n".join(content_to_str(msg.content)).strip())
    ]
    return "\n\n".join(f"{role}: {text}" for role, text in turns)


def _parse_verdict(message: Message) -> GoalVerdict:
    """Normalize the judge response into a GoalVerdict, conservatively."""
    raw = "\n".join(content_to_str(message.content)).strip()

    data: dict[str, Any] | None = None
    candidates = [raw]
    block = re.search(r"\{.*\}", raw, re.DOTALL)
    if block:
        candidates.append(block.group(0))
    for candidate in candidates:
        with contextlib.suppress(json.JSONDecodeError):
            parsed = json.loads(candidate)
            if isinstance(parsed, dict):
                data = parsed
                break

    if data is None:
        logger.warning("judge_goal: could not parse verdict: %r", raw)
        return GoalVerdict(
            score=0.0, complete=False, missing="Judge verdict could not be parsed."
        )

    try:
        score = float(data.get("score", 0.0))
    except (TypeError, ValueError):
        score = 0.0
    score = max(0.0, min(1.0, score))

    return GoalVerdict(
        score=score,
        complete=bool(data.get("complete", score >= 1.0)),
        missing=str(data.get("missing") or ""),
    )
