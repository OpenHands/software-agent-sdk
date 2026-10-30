"""Reach a decision model through LiteLLM, or through your own code.

``DecisionModel`` takes the SDK's question types (``Noul``, ``Choice``,
``Score``) and returns the SDK's answer types. Where it sends the questions is
up to you:

1. Through LiteLLM, the default. Set ``model`` to a LiteLLM model string. With
   the LiteLLM release the SDK uses (1.105), that covers TypeSafe Jev
   (``typesafe/jev-latest``), OpenAI (``openai/gpt-6-luna``), OpenRouter,
   Perplexity, Cloudflare Clef, Microsoft Foundry (``azure_ai/<deployment>``),
   Strands Decider and self-hosted vLLM (``hosted_vllm/<model>``). Databricks,
   Fireworks AI, Laya, Bespoke Nimble and ``Box`` questions need LiteLLM 1.106
   or later. See https://docs.litellm.ai/docs/decisions for the current list.

2. Through your own code. Subclass ``DecisionModel`` and override ``send()``
   (and ``asend()`` if you call ``adecide()``). Each method makes one request
   and returns a ``DecisionReply`` built from SDK types. ``decide()`` still
   adds retries, error mapping, metrics and the ``decision.<model>`` span, and
   checks that every question got one answer of the right type.

Use route 2 for a model only you can reach, such as one you host privately or
an internal service. For a generally available model, contribute a provider to
LiteLLM (https://github.com/BerriAI/litellm) instead. Every LiteLLM user gets
it, and SDK users get it through route 1 when the SDK takes that LiteLLM
release.

This example writes route 2 for TypeSafe's HTTP API. LiteLLM already supports
TypeSafe, so the example can ask both routes the same questions and show that
the code that reads the answers does not change.
"""

import asyncio
import os
from collections.abc import Mapping
from typing import Any

import httpx
from pydantic import SecretStr

from openhands.sdk import DecisionModel, DecisionResult
from openhands.sdk.llm import (
    Choice,
    ChoiceAnswer,
    DecisionReply,
    DecisionUsage,
    Noul,
    NoulAnswer,
    Question,
    Score,
    ScoreAnswer,
)
from openhands.sdk.llm.exceptions import (
    LLMRateLimitError,
    LLMResponseError,
    LLMServiceUnavailableError,
    LLMTimeoutError,
)


class TypeSafeHTTPDecisionModel(DecisionModel):
    """Call TypeSafe's ``/v1/systemone`` API directly, without LiteLLM."""

    def send(self, state: Any, questions: Mapping[str, Question]) -> DecisionReply:
        try:
            response = httpx.post(**self._request(state, questions))
        except httpx.TimeoutException as e:
            raise LLMTimeoutError(str(e)) from e
        return _reply(response)

    async def asend(
        self, state: Any, questions: Mapping[str, Question]
    ) -> DecisionReply:
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(**self._request(state, questions))
        except httpx.TimeoutException as e:
            raise LLMTimeoutError(str(e)) from e
        return _reply(response)

    def _request(self, state: Any, questions: Mapping[str, Question]) -> dict:
        api_key = self.api_key
        assert isinstance(api_key, SecretStr), "Set api_key."
        return {
            "url": f"{self.base_url or 'https://api.typesafe.ai'}/v1/systemone",
            "headers": {"Authorization": f"Bearer {api_key.get_secret_value()}"},
            "json": {
                "model": self.model,
                "state": state,
                # exclude_none drops optional fields left unset, such as
                # Noul.criteria.
                "questions": {
                    name: q.model_dump(exclude_none=True)
                    for name, q in questions.items()
                },
            },
            "timeout": self.timeout,
        }


def _reply(response: httpx.Response) -> DecisionReply:
    # DecisionModel retries these SDK errors; any other error is raised as is.
    if response.status_code == 429:
        raise LLMRateLimitError(response.text)
    if response.status_code >= 500:
        raise LLMServiceUnavailableError(response.text)
    response.raise_for_status()
    body = response.json()
    return DecisionReply(
        answers={name: _answer(a) for name, a in body["answers"].items()},
        model=body["model"],
        usage=DecisionUsage(
            input_tokens=body["usage"]["input_tokens"],
            output_tokens=body["usage"]["output_tokens"],
        ),
        # TypeSafe's response has no price, so cost stays unset. Set it if
        # your provider reports one or you keep a price table.
        raw_response=body,
    )


def _answer(answer: dict) -> NoulAnswer | ChoiceAnswer | ScoreAnswer:
    match answer["type"]:
        case "noul":
            return NoulAnswer(probability=answer["noul"])
        case "choice":
            return ChoiceAnswer(
                choice=answer["choice"],
                confidence=answer["confidence"],
                probabilities=answer["probabilities"],
            )
        case "score":
            return ScoreAnswer(
                score=answer["score"],
                confidence=answer["confidence"],
                probabilities=answer["probabilities"],
                legend=answer["legend"],
            )
    raise LLMResponseError(f"Unsupported answer type: {answer['type']!r}")


api_key = os.getenv("JEV_API_KEY")
assert api_key, "Set JEV_API_KEY to a TypeSafe API key."

state = {
    "issue": {
        "title": "Export to CSV drops the header row",
        "body": "Since 2.3, exported CSV files have no header row. "
        "Steps: open a table, click Export, choose CSV. Expected a header row.",
    }
}
questions: dict[str, Question] = {
    "is_bug": Noul(instructions="Does `issue` report broken behavior?"),
    "area": Choice(
        instructions="Which part of the product does `issue` concern?",
        criteria={
            "import_export": "Reading or writing files",
            "ui": "Screens, buttons and layout",
            "other": "Anything else",
        },
    ),
    "severity": Score(
        instructions="How badly does `issue` affect the user's work?",
        criteria=["Cosmetic", "Workaround available", "Work blocked"],
    ),
}


def summarize(result: DecisionResult) -> str:
    """Read the answers. This code does not know which route answered."""
    severity = result.score("severity")
    return (
        f"is_bug={result.noul('is_bug').probability:.2f} "
        f"area={result.choice('area').choice} "
        f"severity={severity.score:.2f} (confidence {severity.confidence:.2f}) "
        f"model={result.model}"
    )


direct = TypeSafeHTTPDecisionModel(model="jev-latest", api_key=api_key)
via_litellm = DecisionModel(model="typesafe/jev-latest", api_key=api_key)

print("direct HTTP:", summarize(direct.decide(state, questions)))
print("direct, async:", summarize(asyncio.run(direct.adecide(state, questions))))
print("LiteLLM:", summarize(via_litellm.decide(state, questions)))

usage = direct.metrics.accumulated_token_usage
assert usage is not None
print(f"\ndirect route tokens over 2 calls: {usage.prompt_tokens} in")
print(f"LiteLLM route cost: ${via_litellm.metrics.accumulated_cost:.6f}")
print(f"EXAMPLE_COST: {via_litellm.metrics.accumulated_cost}")
