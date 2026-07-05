"""Prompt text for the ``/goal`` command's judge and continuation messages."""

from typing import Final


JUDGE_PROMPT: Final[str] = """You are auditing whether a long-running GOAL has \
been COMPLETED by an AI software agent.

<objective>
{objective}
</objective>

Derive the concrete requirements implied by the objective. For EACH requirement,
look for authoritative evidence in the transcript below: file contents, command
output, or test results produced by the agent. Treat missing, uncertain, or
merely-claimed-but-unverified evidence as NOT satisfied.

<transcript>
{transcript}
</transcript>

Respond with STRICT JSON and nothing else, in exactly this shape:
{{"score": <float 0.0-1.0, probability the FULL objective is provably done>, \
"complete": <true|false>, "missing": "<concise description of what remains, or \
an empty string if complete>"}}"""


FOLLOWUP_PROMPT: Final[str] = """The goal is NOT yet complete (audit iteration \
{iteration}).
Outstanding: {missing}

Inspect the real current state of the workspace (do not rely on memory). For \
each remaining requirement, make concrete progress and gather authoritative \
evidence by running the relevant tests/commands. Keep the full objective intact \
and finish only once every requirement is provably satisfied."""


RESUME_PROMPT: Final[str] = """Resuming a goal that was paused or interrupted. \
Re-check the real current state of the workspace (do not rely on memory) and \
continue making concrete, verified progress toward the original objective. \
Finish only once every requirement is provably satisfied."""
