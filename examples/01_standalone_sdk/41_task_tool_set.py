"""
Animal Quiz with Task Tool Set

Demonstrates the TaskToolSet with a main agent delegating to an
animal-expert sub-agent. The flow is:

1. Main agent picks an animal and delegates to the "animal_expert"
   sub-agent to generate a multiple-choice question about it.
2. Main agent thinks about the question and picks an answer.
3. Main agent resumes the same sub-agent conversation to ask whether
   its answer is correct. The sub-agent confirms or corrects it.
"""

import os

from openhands.sdk import LLM, Agent, AgentContext, Conversation, Tool
from openhands.sdk.context import Skill
from openhands.sdk.subagent import register_agent
from openhands.tools.delegate import DelegationVisualizer
from openhands.tools.task import TaskToolSet


llm = LLM(
    model=os.getenv("LLM_MODEL", "anthropic/claude-sonnet-4-5-20250929"),
    api_key=os.getenv("LLM_API_KEY"),
    base_url=os.getenv("LLM_BASE_URL", None),
)
# ── Register the animal expert sub-agent ─────────────────────────────


def create_animal_expert(llm: LLM) -> Agent:
    """Factory for the animal-expert sub-agent."""
    return Agent(
        llm=llm,
        tools=[],  # no tools needed – pure knowledge
        agent_context=AgentContext(
            skills=[
                Skill(
                    name="animal_expertise",
                    content=(
                        "You are a world-class zoologist. "
                        "When asked to generate a quiz question, respond with "
                        "EXACTLY this format and nothing else:\n\n"
                        "Question: <question text>\n"
                        "A) <option>\n"
                        "B) <option>\n"
                        "C) <option>\n"
                        "D) <option>\n\n"
                        "When asked to verify an answer, state whether it is "
                        "correct or incorrect, reveal the right answer, and "
                        "give a short fun-fact explanation."
                    ),
                    trigger=None,  # always active
                )
            ],
            system_message_suffix="Keep every response concise.",
        ),
    )


register_agent(
    name="animal_expert",
    factory_func=create_animal_expert,
    description="Zoologist that creates and verifies animal quiz questions.",
)

# ── Main agent ───────────────────────────────────────────────────────

main_agent = Agent(
    llm=llm,
    tools=[Tool(name=TaskToolSet.name)],
)

conversation = Conversation(
    agent=main_agent,
    workspace=os.getcwd(),
    visualizer=DelegationVisualizer(name="QuizHost"),
)

# ── Round 1: generate the question ──────────────────────────────────

conversation.send_message(
    "Pick any animal you like and use the task tool to delegate to the "
    "'animal_expert' sub-agent. Ask it to generate a single "
    "multiple-choice question (A-D) about that animal. "
    "Once you get the question back, think step-by-step about which "
    "answer is correct and pick one (A, B, C, or D). Tell the user "
    "the question and your chosen answer."
)
conversation.run()

# ── Round 2: verify the answer ──────────────────────────────────────

conversation.send_message(
    "Now use the task tool to resume the previous 'animal_expert' "
    "sub-agent conversation. Tell it which answer you picked and ask "
    "it whether that answer is correct. Report the result to the user."
)
conversation.run()

# ── Done ────────────────────────────────────────────────────────────

cost = conversation.conversation_stats.get_combined_metrics().accumulated_cost
print(f"\nEXAMPLE_COST: {cost}")
