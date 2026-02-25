"""
Animal Quiz with Task Tool Set

Demonstrates the TaskToolSet with a main agent delegating to an
animal-expert sub-agent. The flow is:

1. User names an animal.
2. Main agent delegates to the "animal_expert" sub-agent to generate
   a multiple-choice question about that animal.
3. Main agent shows the question to the user.
4. User picks an answer.
5. Main agent delegates again to the same sub-agent type to check
   whether the answer is correct and explain why.
"""

import os

from openhands.sdk import LLM, Agent, AgentContext, Conversation, Tool
from openhands.sdk.context import Skill
from openhands.tools.delegate import DelegationVisualizer, register_agent
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

animal = input("Pick an animal: ")

conversation.send_message(
    f"The user chose the animal: {animal}. "
    "Use the task tool to delegate to the 'animal_expert' sub-agent "
    "and ask it to generate a single multiple-choice question (A-D) "
    f"about {animal}. "
    "Once you get the question back, display it to the user exactly "
    "as the sub-agent returned it and ask the user to pick A, B, C, or D."
)
conversation.run()

# ── Round 2: verify the answer ──────────────────────────────────────

answer = input("Your answer (A/B/C/D): ")

conversation.send_message(
    f"The user answered: {answer}. "
    "Use the task tool to delegate to the 'animal_expert' sub-agent again "
    f"and ask it whether '{answer}' is the correct answer to the question "
    "it generated earlier. Don't include the question; instead, use the "
    "'resume' parameter to continue the previous conversation."
)
conversation.run()

# ── Done ────────────────────────────────────────────────────────────

cost = conversation.conversation_stats.get_combined_metrics().accumulated_cost
print(f"\nEXAMPLE_COST: {cost}")
