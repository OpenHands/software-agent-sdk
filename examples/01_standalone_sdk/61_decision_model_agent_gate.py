"""Use a decision model to choose which issues an agent works on.

A decision model call is fast and costs a small fraction of a cent, so it fits
in front of an LLM agent as a gate: triage every incoming issue, and spend
agent time only on the ones that are ready for it.

This example:

1. Triages a queue of issues concurrently with ``adecide()``. Each issue gets
   one request, and every question about that issue goes in that request.
2. Picks issues in plain code: well scoped, small, and not a security report.
3. Hands each picked issue to an agent.
4. Checks the agent's work. Code checks what code can (did any file change?).
   A second decision request judges the agent's reply. A second request is
   right here because its state did not exist before the agent ran; questions
   about the original state belong in the first request.

Set JEV_API_KEY for the decision model and LLM_API_KEY for the agent.

With Laminar or OTEL configured (see 27_observability_laminar.py), ``@observe``
opens a ``triage_issue`` span per issue and the ``decision.<model>`` span nests
under it. The agent's conversation is traced as its own session, as in 27.
"""

import asyncio
import os
import tempfile
from pathlib import Path

from openhands.sdk import LLM, Agent, Conversation, DecisionModel, Tool
from openhands.sdk.conversation import get_agent_final_response
from openhands.sdk.llm import Choice, Noul, Score
from openhands.sdk.observability import observe
from openhands.tools.file_editor import FileEditorTool
from openhands.tools.terminal import TerminalTool


jev_api_key = os.getenv("JEV_API_KEY")
assert jev_api_key, "Set JEV_API_KEY to a TypeSafe API key."

decision_model = DecisionModel(
    model=os.getenv("DECISION_MODEL", "typesafe/jev-latest"),
    api_key=jev_api_key,
)

ISSUES = [
    {
        "number": 101,
        "title": "Add a slugify helper",
        "body": (
            "Please add `slugify(text: str) -> str` to a new file `slug.py`. "
            "It should lowercase the text, replace runs of whitespace with a "
            "single hyphen, and drop every character that is not a letter, "
            "digit or hyphen. Example: 'Hello, World!' -> 'hello-world'."
        ),
    },
    {
        "number": 102,
        "title": "Things are slow",
        "body": "The app feels slow sometimes. Can you make it faster?",
    },
    {
        "number": 103,
        "title": "API key shows up in debug logs",
        "body": (
            "With DEBUG=1 the full LLM API key is printed in the log line for "
            "every request. Anyone with log access can read it."
        ),
    },
    {
        "number": 104,
        "title": "Move storage to Postgres",
        "body": (
            "We should replace the file-based conversation store with "
            "Postgres, including migrations for existing users."
        ),
    },
]

TRIAGE_QUESTIONS = {
    "kind": Choice(
        instructions="What does `issue` mainly ask for?",
        criteria={
            "bug": "Something is broken.",
            "feature": "A new capability or a change in behavior.",
            "other": "Neither a bug nor a feature request.",
        },
    ),
    "well_scoped": Noul(
        instructions=(
            "Does `issue.body` say exactly what to change and what the result "
            "should be, so a developer could start without follow-up questions?"
        ),
    ),
    "size": Score(
        instructions="How much code would it take to do what `issue` asks?",
        criteria=[
            "A few lines in one file.",
            "Changes across several files or a new module.",
            "A redesign or migration across the codebase.",
        ],
    ),
    "is_security": Noul(
        instructions=(
            "Does `issue` report a security problem, such as exposed secrets "
            "or unauthorized access?"
        ),
    ),
}

YES, NO = 0.7, 0.3


# ignore_input keeps the issue text off the span, as DecisionModel does.
@observe(name="triage_issue", ignore_input=True)
async def triage(issue: dict) -> tuple[str, str]:
    """Return (kind, route) for one issue."""
    result = await decision_model.adecide(
        state={"issue": issue}, questions=TRIAGE_QUESTIONS
    )
    kind = result.choice("kind").choice
    size = result.score("size")
    if result.noul("is_security").probability >= YES:
        return kind, "security team"
    if size.score >= 1.5:
        return kind, "human planning"
    if result.noul("well_scoped").probability < YES:
        return kind, "needs more information"
    # Agents get only small changes the model is sure about.
    if size.score > 0.5 or size.confidence < 0.5:
        return kind, "human planning"
    return kind, "agent"


async def triage_queue(issues: list[dict]) -> list[tuple[str, str]]:
    # One request per issue, sent concurrently. Each request already asks
    # every question about its issue, so no issue needs a second round trip.
    return await asyncio.gather(*(triage(issue) for issue in issues))


triaged = asyncio.run(triage_queue(ISSUES))
for issue, (kind, route) in zip(ISSUES, triaged):
    print(f"#{issue['number']} [{kind}] {issue['title']!r} -> {route}")

llm = LLM(
    model=os.getenv("LLM_MODEL", "gpt-5.5"),
    api_key=os.getenv("LLM_API_KEY"),
    base_url=os.getenv("LLM_BASE_URL"),
    usage_id="agent",
)
agent = Agent(
    llm=llm,
    tools=[Tool(name=TerminalTool.name), Tool(name=FileEditorTool.name)],
)

for issue, (_, route) in zip(ISSUES, triaged):
    if route != "agent":
        continue

    workspace = tempfile.mkdtemp()
    conversation = Conversation(agent=agent, workspace=workspace)
    conversation.send_message(
        f"Resolve issue #{issue['number']}: {issue['title']}\n\n{issue['body']}\n\n"
        "Finish with a short summary of what you changed, and say if you left "
        "anything undone."
    )
    conversation.run()
    reply = get_agent_final_response(conversation.state.events)

    # Use code when you can: an agent that changed nothing did not do the work.
    if not any(Path(workspace).iterdir()):
        print(f"#{issue['number']}: no files changed; sending to a human")
        continue

    # Use the decision model for the judgment code cannot make.
    review = decision_model.decide(
        state={"issue": issue, "agent_reply": reply},
        questions={
            "claims_done": Noul(
                instructions=(
                    "Does `agent_reply` say that the change requested in "
                    "`issue.body` was made?"
                ),
            ),
            "left_work_undone": Noul(
                instructions=(
                    "Does `agent_reply` say that part of the request in "
                    "`issue.body` was left undone or done differently?"
                ),
            ),
        },
    )
    claims_done = review.noul("claims_done").probability
    left_work_undone = review.noul("left_work_undone").probability
    print(
        f"#{issue['number']}: claims_done={claims_done:.2f} "
        f"left_work_undone={left_work_undone:.2f}"
    )
    if claims_done >= YES and left_work_undone <= NO:
        print(f"#{issue['number']}: ready for review")
    else:
        print(f"#{issue['number']}: sending to a human")

decision_cost = decision_model.metrics.accumulated_cost
agent_cost = llm.metrics.accumulated_cost
print(f"decision model: ${decision_cost:.6f}  agent LLM: ${agent_cost:.4f}")
print(f"EXAMPLE_COST: {decision_cost + agent_cost}")
