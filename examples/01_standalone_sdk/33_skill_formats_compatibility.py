"""
Example: Codex CLI-style skill triggering with progressive disclosure.

This example demonstrates:
1. Agent-triggered skills (agent decides when to use based on description)
2. Progressive disclosure (metadata at startup, full content on demand)
3. Backward compatibility with OpenHands keyword-triggered skills

Skills are triggered when:
- User explicitly names a skill
- Task clearly matches a skill's description (agent decides)

Reference: https://developers.openai.com/codex/skills/
"""

import os
import tempfile
from pathlib import Path

from pydantic import SecretStr

from openhands.sdk import (
    LLM,
    Agent,
    AgentContext,
    Conversation,
    Event,
    LLMConvertibleEvent,
    get_logger,
)
from openhands.sdk.context import (
    KeywordTrigger,
    Skill,
    SkillResources,
)
from openhands.sdk.tool import Tool
from openhands.tools.terminal import TerminalTool


logger = get_logger(__name__)


def create_example_skills() -> tuple[list[Skill], Path]:
    """Create example skills in both formats.

    Returns:
        Tuple of (skills list, temp directory path for cleanup)
    """
    # Create a temp directory for skill files
    temp_dir = Path(tempfile.mkdtemp())

    # Create AgentSkills-style directory structure
    skill_root = temp_dir / "agentskills-skill"
    skill_root.mkdir()
    scripts_dir = skill_root / "scripts"
    scripts_dir.mkdir()
    (scripts_dir / "deploy.sh").write_text("#!/bin/bash\necho 'Deploying...'\n")
    references_dir = skill_root / "references"
    references_dir.mkdir()
    (references_dir / "guide.md").write_text("# Deployment Guide\n")

    skills = []

    # =========================================================================
    # OpenHands-style skills (original format, no description/resources)
    # =========================================================================

    # 1. OpenHands repo skill (always active)
    skills.append(
        Skill(
            name="openhands-repo-skill",
            content=(
                "This is an OpenHands-style repo skill.\n"
                "It uses the original format without description or resources.\n"
                "When active, remind the user you're in compatibility mode."
            ),
            source="/path/to/openhands-repo-skill/SKILL.md",
            trigger=None,  # Always active
        )
    )

    # 2. OpenHands keyword-triggered skill
    skills.append(
        Skill(
            name="openhands-keyword-skill",
            content=(
                "IMPORTANT: The user mentioned 'legacy-deploy'.\n"
                "This is an OpenHands-style keyword skill.\n"
                "Explain that legacy deployment uses the old pipeline."
            ),
            source="/path/to/openhands-keyword-skill/SKILL.md",
            trigger=KeywordTrigger(keywords=["legacy-deploy", "old-deploy"]),
        )
    )

    # =========================================================================
    # AgentSkills-standard skills (new format with description/resources)
    # =========================================================================

    # 3. AgentSkills repo skill (always active, with description)
    skills.append(
        Skill(
            name="agentskills-repo-skill",
            content=(
                "This is an AgentSkills-standard repo skill.\n"
                "It includes a description and resources.\n"
                "When active, mention that you have access to deployment scripts."
            ),
            description="Repository guidelines for the deployment system",
            source=str(skill_root / "SKILL.md"),
            resources=SkillResources(
                skill_root=str(skill_root),
                scripts=["deploy.sh"],
            ),
            trigger=None,  # Always active
        )
    )

    # 4. AgentSkills keyword-triggered skill (with description/resources)
    skills.append(
        Skill(
            name="agentskills-keyword-skill",
            content=(
                "IMPORTANT: The user mentioned 'modern-deploy'.\n"
                "This is an AgentSkills-standard keyword skill.\n"
                "Explain that modern deployment uses the new CI/CD pipeline.\n"
                "You can reference scripts in the scripts dir for deployment."
            ),
            description="Modern deployment procedures using CI/CD",
            source=str(skill_root / "SKILL.md"),
            resources=SkillResources(
                skill_root=str(skill_root),
                scripts=["deploy.sh"],
                references=["guide.md"],
            ),
            trigger=KeywordTrigger(keywords=["modern-deploy", "new-deploy", "cicd"]),
        )
    )

    return skills, temp_dir


def print_section(title: str) -> None:
    """Print a section header."""
    print("\n" + "=" * 80)
    print(f" {title}")
    print("=" * 80 + "\n")


def main():
    # Configure LLM
    api_key = os.getenv("LLM_API_KEY")
    if not api_key:
        print("ERROR: LLM_API_KEY environment variable is not set.")
        print("Please set it to run this example.")
        return

    model = os.getenv("LLM_MODEL", "anthropic/claude-sonnet-4-5-20250929")
    base_url = os.getenv("LLM_BASE_URL")

    llm = LLM(
        usage_id="agent",
        model=model,
        base_url=base_url,
        api_key=SecretStr(api_key),
    )

    # Create skills in both formats
    skills, temp_dir = create_example_skills()

    print_section("Skills Created")
    for skill in skills:
        print(f"  - {skill.name}")
        fmt = "AgentSkills" if skill.description else "OpenHands"
        print(f"    Format: {fmt}")
        skill_type = "Repo (always active)" if skill.trigger is None else "Triggered"
        print(f"    Type: {skill_type}")
        if skill.description:
            print(f"    Description: {skill.description}")
        if skill.resources:
            print(f"    Resources: {skill.resources}")
        print()

    # Create agent context with mixed skills
    agent_context = AgentContext(
        skills=skills,
        system_message_suffix="You are a helpful deployment assistant.",
    )

    # Show what the system message suffix looks like
    print_section("System Message Suffix (what the agent sees)")
    suffix = agent_context.get_system_message_suffix()
    if suffix:
        # Truncate for display
        if len(suffix) > 2000:
            print(suffix[:2000] + "\n... [truncated]")
        else:
            print(suffix)

    # Create tools and agent
    tools = [Tool(name=TerminalTool.name)]
    agent = Agent(llm=llm, tools=tools, agent_context=agent_context)

    # Collect LLM messages for inspection
    llm_messages = []

    def conversation_callback(event: Event):
        if isinstance(event, LLMConvertibleEvent):
            llm_messages.append(event.to_llm_message())

    cwd = os.getcwd()
    conversation = Conversation(
        agent=agent, callbacks=[conversation_callback], workspace=cwd
    )

    # Test 1: Verify repo skills are active (both formats)
    print_section("Test 1: Repo Skills (Always Active)")
    print("Sending: 'What deployment guidelines do you have?'")
    print("-" * 40)
    conversation.send_message("What deployment guidelines do you have?")
    conversation.run()

    # Test 2: Trigger OpenHands-style keyword skill
    print_section("Test 2: OpenHands Keyword Skill")
    print("Sending: 'How do I do a legacy-deploy?'")
    print("-" * 40)
    conversation.send_message("How do I do a legacy-deploy?")
    conversation.run()

    # Test 3: Trigger AgentSkills-style keyword skill
    print_section("Test 3: AgentSkills Keyword Skill")
    print("Sending: 'Tell me about modern-deploy with cicd'")
    print("-" * 40)
    conversation.send_message("Tell me about modern-deploy with cicd")
    conversation.run()

    # Summary
    print_section("Summary")
    print(f"Total LLM messages exchanged: {len(llm_messages)}")
    print(f"Total cost: ${llm.metrics.accumulated_cost:.4f}")

    # Cleanup
    import shutil

    shutil.rmtree(temp_dir)
    print(f"\nCleaned up temp directory: {temp_dir}")


if __name__ == "__main__":
    main()
