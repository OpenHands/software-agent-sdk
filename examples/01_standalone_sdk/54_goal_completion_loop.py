"""The /goal command: pursue an objective until a judge LLM confirms it is done.

A plain ``conversation.run()`` stops as soon as the agent *thinks* it is
finished. The ``/goal`` loop is stricter: after each run it asks a second
"judge" LLM to audit the transcript for authoritative evidence -- file
contents, command output, test results -- that the objective is *provably*
complete. If something is still missing, it re-prompts the agent with the
judge's feedback and runs again, until the goal is genuinely done or a hard
iteration cap is reached.

That makes it a good fit for verifiable objectives like "make the tests pass":
the agent cannot finish just by claiming success; the judge has to see green
output first.

Key concepts demonstrated:
1. ``run_goal(conversation, objective, judge_llm, max_iterations=...)`` drives
   the conversation from the outside, re-prompting until the judge is satisfied.
2. A second, independent "judge" LLM grades completion -- separate from the
   agent that does the work.
3. The returned ``GoalOutcome`` reports whether the goal ``"complete"``-d or was
   ``"capped"``, how many audit rounds it took, and the judge's final verdict.

Because ``run_goal`` drives the conversation you pass in (it does not fork or
spin up a sidecar), every turn -- objective, agent work, judge-driven followups
-- lands in the same ``conversation.state.events`` history. It therefore
composes with whatever agent, tools, or critic you already have.
"""

import os
import tempfile

from openhands.sdk import LLM, Agent, Conversation, Tool
from openhands.sdk.conversation.goal import run_goal
from openhands.tools.file_editor import FileEditorTool
from openhands.tools.terminal import TerminalTool


# The agent LLM does the work; the judge LLM independently grades completion.
# Two separate instances (same model, distinct usage_id) keep their costs apart.
model = os.getenv("LLM_MODEL", "gpt-5.5")
api_key = os.getenv("LLM_API_KEY")
base_url = os.getenv("LLM_BASE_URL")
agent_llm = LLM(usage_id="agent", model=model, api_key=api_key, base_url=base_url)
judge_llm = LLM(usage_id="goal-judge", model=model, api_key=api_key, base_url=base_url)

agent = Agent(
    llm=agent_llm,
    tools=[Tool(name=TerminalTool.name), Tool(name=FileEditorTool.name)],
)

workspace = tempfile.mkdtemp(prefix="goal_demo_")
conversation = Conversation(agent=agent, workspace=workspace)

# A verifiable objective: the judge can only call it done once it has seen
# pytest actually pass -- not merely the agent asserting that it did.
objective = (
    "Create mathx.py with an add(a, b) function and test_mathx.py with a pytest "
    "test for it. The goal is complete only when `python -m pytest -q` passes."
)

# Drive the conversation toward the objective, re-judging after each run.
outcome = run_goal(conversation, objective, judge_llm, max_iterations=3)

print("\n" + "=" * 70)
print(f"Goal {outcome.status} after {outcome.iterations} audit round(s).")
print(f"Judge score: {outcome.verdict.score:.2f}")
if outcome.verdict.missing:
    print(f"Still missing: {outcome.verdict.missing}")
print(f"Workspace: {workspace}")
print("=" * 70)

# Report cost (agent work + judge audits).
cost = agent_llm.metrics.accumulated_cost + judge_llm.metrics.accumulated_cost
print(f"EXAMPLE_COST: {cost}")
