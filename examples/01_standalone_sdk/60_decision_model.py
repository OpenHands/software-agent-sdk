"""Ask a decision model typed questions with DecisionModel.

An LLM writes text. A decision model answers typed questions about a ``state``
and returns values your code can branch on:

- ``Noul``: a yes/no question. The answer is the probability of yes.
- ``Choice``: one option from a closed set, with a probability per option.
- ``Score``: a position on ordered levels, with a probability per level.

Choice and Score answers also carry a ``confidence`` from 0 to 1.

This example triages one GitHub issue with TypeSafe's Jev model and follows the
practices in https://docs.typesafe.ai/concepts/how-to-build-with-system-one.md:

1. Keep control flow in code. Ask the model only for narrow judgments.
2. Send structured state, and point questions at fields with backticked paths.
   Options and levels can be structured too, which helps tell apart options
   that are easy to confuse.
3. Ask many atomic questions in one call, including speculative ones that only
   matter on some code paths. Questions run in parallel against the same state,
   so each extra question adds little latency and costs only its own tokens.
   LiteLLM accepts up to 128 questions per call; split more across calls that
   send the same state.
4. Combine the answers in code, and route on uncertainty instead of guessing.

Set JEV_API_KEY to a TypeSafe API key. DECISION_MODEL defaults to
typesafe/jev-latest; any LiteLLM decision model works.

With Laminar or OTEL configured (see 27_observability_laminar.py), each
decide() call emits a ``decision.<model>`` span with the questions, answers,
tokens and cost. The state is not recorded on the span.
"""

import os

from openhands.sdk import DecisionModel
from openhands.sdk.llm import Choice, Noul, Score


api_key = os.getenv("JEV_API_KEY")
assert api_key, "Set JEV_API_KEY to a TypeSafe API key."

decision_model = DecisionModel(
    model=os.getenv("DECISION_MODEL", "typesafe/jev-latest"),
    api_key=api_key,
)

# Send only what the questions need, as structured JSON.
state = {
    "issue": {
        "title": "Agent fails when the workspace path contains a space",
        "body": (
            "Running the hello world example with workspace='/tmp/my project' "
            "raises FileNotFoundError on the first terminal command. Paths "
            "without spaces work.\n"
            "Steps: 1) mkdir '/tmp/my project' 2) run 01_hello_world.py with "
            "that workspace.\n"
            "This blocks everyone on our team, please fix it ASAP!"
        ),
    },
}

# One request, many questions. Question names are only for your code: they are
# not sent to the model, so each `instructions` must be a complete question.
questions = {
    # Bug reports and feature requests are easy to confuse, so each option
    # says what it covers, what it does not, and gives examples. Using the same
    # keys for every option lets the model compare them directly. A plain
    # string is enough for an option that is not easily confused.
    "kind": Choice(
        instructions="What does `issue` mainly ask for?",
        criteria={
            "bug": {
                "what": "Documented or expected behavior that is broken.",
                "not_for": "Asking for behavior the software never had.",
                "examples": ["Crashes on startup", "Export writes an empty file"],
            },
            "feature": {
                "what": "A new capability or a change to working behavior.",
                "not_for": "Something that is supposed to work but fails.",
                "examples": ["Support YAML config", "Add a --quiet flag"],
            },
            "question": {
                "what": "Help using the software; nothing is broken.",
                "not_for": "Reports of failures or requests for changes.",
                "examples": ["How do I set the model?", "Where are logs kept?"],
            },
            "other": "None of the above.",
        },
    ),
    # Speculative: severity and repro steps only matter if this is a bug.
    "severity": Score(
        instructions="How badly does the problem in `issue` affect users?",
        criteria=[
            "Cosmetic; nothing is broken.",
            "A feature is broken, but a workaround exists.",
            "Work is blocked and there is no workaround.",
        ],
    ),
    "has_repro_steps": Noul(
        instructions="Does `issue.body` give steps to reproduce the problem?",
    ),
    "is_security": Noul(
        instructions=(
            "Does `issue` report a security vulnerability, such as leaked "
            "secrets or unauthorized access?"
        ),
    ),
    "well_scoped": Noul(
        instructions=(
            "Is `issue` specific enough that a developer could start work "
            "without asking follow-up questions?"
        ),
    ),
    "frustration": Score(
        instructions="How frustrated does the author of `issue` sound?",
        criteria=[
            "Calm and matter-of-fact.",
            "Annoyed but constructive.",
            "Angry or threatening to leave.",
        ],
    ),
}

result = decision_model.decide(state=state, questions=questions)

# Typed accessors raise if the name is missing or the answer is another type.
kind = result.choice("kind")
severity = result.score("severity")
frustration = result.score("frustration")
has_repro_steps = result.noul("has_repro_steps").probability
is_security = result.noul("is_security").probability
well_scoped = result.noul("well_scoped").probability

print(f"kind:            {kind.choice}, confidence {kind.confidence:.2f}")
print(f"                 {kind.probabilities}")
# A Score can land between levels; level 0 is the first entry in `criteria`.
print(f"severity:        {severity.score:.2f}, confidence {severity.confidence:.2f}")
print(f"                 {severity.legend}")
print(
    f"frustration:     {frustration.score:.2f}, confidence {frustration.confidence:.2f}"
)
print(f"has_repro_steps: {has_repro_steps:.2f}")
print(f"is_security:     {is_security:.2f}")
print(f"well_scoped:     {well_scoped:.2f}")

# A Noul near 0.5 means "unsure", not "medium". 0.7 and 0.3 are TypeSafe's
# suggested starting thresholds; tune them on your own data.
YES, NO = 0.7, 0.3

# Code owns the decision. Speculative answers that do not apply are ignored.
labels: list[str] = []
if is_security >= YES:
    route = "security team (private)"
elif kind.confidence < 0.6:
    route = "human triage: unclear what the issue is about"
elif kind.choice == "bug":
    labels.append("bug")
    # Low confidence means the probability is spread across levels, so the
    # score alone is not enough to act on.
    if severity.score >= 1.5 and severity.confidence >= 0.5:
        labels.append("priority:high")
    if has_repro_steps <= NO:
        labels.append("needs-repro")
    route = "agent" if well_scoped >= YES and has_repro_steps >= YES else "backlog"
elif kind.choice == "feature":
    labels.append("enhancement")
    route = "agent" if well_scoped >= YES else "needs-design"
else:
    route = "community support"

if frustration.score >= 1.5:
    labels.append("reply-soon")

print(f"\nroute: {route}")
print(f"labels: {labels}")

# Log the versioned model that answered: an alias like jev-latest moves when a
# new release ships. Pin that version if you tune thresholds against it.
print(f"\nanswered by: {result.raw_response.model}")
usage = decision_model.metrics.accumulated_token_usage
assert usage is not None
print(f"tokens: {usage.prompt_tokens} in, {usage.completion_tokens} out")

cost = decision_model.metrics.accumulated_cost
print(f"EXAMPLE_COST: {cost}")
