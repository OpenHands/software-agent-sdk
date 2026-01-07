"""Example: Loading Skills from Disk (AgentSkills Standard)

This example demonstrates how to load skills following the AgentSkills standard
from a directory on disk.

Skills are modular, self-contained packages that extend an agent's capabilities
by providing specialized knowledge, workflows, and tools. They follow the
AgentSkills standard which includes:
- SKILL.md file with frontmatter metadata (name, description, triggers)
- Optional resource directories: scripts/, references/, assets/

See the example_skills/ directory for a complete skill structure.
"""

import os
from pathlib import Path

from pydantic import SecretStr

from openhands.sdk import LLM, Agent, AgentContext, Conversation
from openhands.sdk.context.skills import (
    discover_skill_resources,
    load_skills_from_dir,
)
from openhands.sdk.tool import Tool
from openhands.tools.file_editor import FileEditorTool
from openhands.tools.terminal import TerminalTool


def main():
    # Get the directory containing this script
    script_dir = Path(__file__).parent
    example_skills_dir = script_dir / "example_skills"

    # =========================================================================
    # Part 1: Loading Skills from a Directory
    # =========================================================================
    print("=" * 80)
    print("Part 1: Loading Skills from a Directory")
    print("=" * 80)

    print(f"Loading skills from: {example_skills_dir}")

    # Discover resources in the skill directory
    skill_subdir = example_skills_dir / "rot13-encryption"
    resources = discover_skill_resources(skill_subdir)
    print("\nDiscovered resources in rot13-encryption/:")
    print(f"  - scripts: {resources.scripts}")
    print(f"  - references: {resources.references}")
    print(f"  - assets: {resources.assets}")

    # Load skills from the directory
    repo_skills, knowledge_skills, agent_skills = load_skills_from_dir(
        example_skills_dir
    )

    print("\nLoaded skills from directory:")
    print(f"  - Repo skills: {list(repo_skills.keys())}")
    print(f"  - Knowledge skills: {list(knowledge_skills.keys())}")
    print(f"  - Agent skills (SKILL.md): {list(agent_skills.keys())}")

    # Access the loaded skill and show all AgentSkills standard fields
    if agent_skills:
        skill_name = list(agent_skills.keys())[0]
        loaded_skill = agent_skills[skill_name]
        print("\nLoaded skill details (AgentSkills standard fields):")
        print(f"  - Name: {loaded_skill.name}")
        desc = loaded_skill.description or ""
        print(f"  - Description: {desc[:70]}...")
        print(f"  - License: {loaded_skill.license}")
        print(f"  - Compatibility: {loaded_skill.compatibility}")
        print(f"  - Metadata: {loaded_skill.metadata}")
        if loaded_skill.resources:
            print("  - Resources:")
            print(f"    - Scripts: {loaded_skill.resources.scripts}")
            print(f"    - References: {loaded_skill.resources.references}")
            print(f"    - Assets: {loaded_skill.resources.assets}")
            print(f"    - Skill root: {loaded_skill.resources.skill_root}")

    # =========================================================================
    # Part 2: Using Skills with an Agent
    # =========================================================================
    print("\n" + "=" * 80)
    print("Part 2: Using Skills with an Agent")
    print("=" * 80)

    # Check for API key
    api_key = os.getenv("LLM_API_KEY")
    if not api_key:
        print("Skipping agent demo (LLM_API_KEY not set)")
        print("\nTo run the full demo, set the LLM_API_KEY environment variable:")
        print("  export LLM_API_KEY=your-api-key")
        return

    # Configure LLM
    model = os.getenv("LLM_MODEL", "anthropic/claude-sonnet-4-5-20250929")
    llm = LLM(
        usage_id="skills-demo",
        model=model,
        api_key=SecretStr(api_key),
    )

    # Create agent context with loaded skills
    agent_context = AgentContext(
        skills=list(agent_skills.values()),
        # Disable public skills for this demo to keep output focused
        load_public_skills=False,
    )

    # Create agent with tools so it can read skill resources
    tools = [
        Tool(name=TerminalTool.name),
        Tool(name=FileEditorTool.name),
    ]
    agent = Agent(llm=llm, tools=tools, agent_context=agent_context)

    # Create conversation
    conversation = Conversation(agent=agent, workspace=os.getcwd())

    # Test the skill (triggered by "encrypt" keyword)
    # The skill provides instructions and a script for ROT13 encryption
    print("\nSending message with 'encrypt' keyword to trigger skill...")
    conversation.send_message("Encrypt the message 'hello world'.")
    conversation.run()

    print(f"\nTotal cost: ${llm.metrics.accumulated_cost:.4f}")


if __name__ == "__main__":
    main()
