"""Prompt text for the ``/goal`` command's judge and continuation messages."""

from typing import Final


JUDGE_SYSTEM_PROMPT: Final[str] = """\
You are auditing whether a long-running GOAL has been COMPLETED by an AI \
software agent.

Derive the concrete requirements implied by the objective. For EACH requirement,
look for authoritative evidence in the transcript: file contents, command
output, or test results produced by the agent. Treat missing, uncertain, or
merely-claimed-but-unverified evidence as NOT satisfied.

Respond with STRICT JSON and nothing else, in exactly this shape:
{{"score": <float 0.0-1.0, probability the FULL objective is provably done>, \
"complete": <true|false>, "missing": "<concise description of what remains, or \
an empty string if complete>"}}"""


JUDGE_USER_PROMPT: Final[str] = """\
<objective>
{objective}
</objective>

<transcript>
{transcript}
</transcript>"""


JUDGE_PROMPT: Final[str] = JUDGE_SYSTEM_PROMPT + "\n\n" + JUDGE_USER_PROMPT
"""Backwards-compatible concatenation of the system steering prompt and the user
payload template. Kept so external callers that still format a single prompt
continue to work; new code should use ``JUDGE_SYSTEM_PROMPT`` and
``JUDGE_USER_PROMPT`` separately to build a ``system + user`` message pair."""


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
