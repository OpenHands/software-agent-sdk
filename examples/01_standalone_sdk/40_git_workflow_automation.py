#!/usr/bin/env python3
"""
Git Workflow Automation Example

This example demonstrates how to use the GitTool to automate common
git workflows. The agent performs a complete development workflow:
1. Check repository status
2. Create a new feature branch
3. Make code changes
4. Stage and commit changes
5. View commit history

This showcases the GitTool's ability to handle version control operations
autonomously as part of a larger development workflow.
"""

import os
import tempfile
from pathlib import Path

from openhands.sdk import LLM, Agent, Conversation, Tool
from openhands.sdk.logger import get_logger
from openhands.tools.file_editor import FileEditorTool
from openhands.tools.git import GitTool
from openhands.tools.terminal import TerminalTool


logger = get_logger(__name__)


def main():
    """Run the git workflow automation example."""
    # Create a temporary workspace for this example
    with tempfile.TemporaryDirectory() as workspace_dir:
        workspace_path = Path(workspace_dir)
        logger.info(f"Working in temporary directory: {workspace_path}")

        # Initialize a git repository
        os.system(f'git init "{workspace_path}"')
        os.system(f'git -C "{workspace_path}" config user.name "Example User"')
        os.system(f'git -C "{workspace_path}" config user.email "user@example.com"')

        # Create initial README
        readme_path = workspace_path / "README.md"
        readme_path.write_text("# Example Project\n\nThis is a demo project.\n")

        # Initial commit
        os.system(f'git -C "{workspace_path}" add .')
        os.system(f'git -C "{workspace_path}" commit -m "Initial commit"')

        # Configure LLM
        api_key = os.getenv("LLM_API_KEY")
        if not api_key:
            logger.error(
                "LLM_API_KEY environment variable is not set. "
                "Please set it to run this example."
            )
            return

        model = os.getenv("LLM_MODEL", "anthropic/claude-sonnet-4-5-20250929")
        base_url = os.getenv("LLM_BASE_URL")

        llm = LLM(
            model=model,
            base_url=base_url,
            api_key=api_key,
        )

        # Create agent with GitTool, FileEditorTool, and TerminalTool
        agent = Agent(
            llm=llm,
            tools=[
                Tool(name=GitTool.name),
                Tool(name=FileEditorTool.name),
                Tool(name=TerminalTool.name),
            ],
        )

        # Create conversation
        conversation = Conversation(agent=agent, workspace=str(workspace_path))

        # Task description
        task = """
        Please help me with the following git workflow:

        1. Check the current git status
        2. Create a new branch called 'feature/add-description'
        3. Add a new section to README.md with a project description:
           ## Description
           This project demonstrates automated git workflows using AI agents.
        4. Stage and commit your changes with message: "docs: add project description"
        5. Show the git log to verify the commit

        After completing these steps, confirm that everything was done successfully.
        """

        logger.info("Starting git workflow automation...")
        logger.info("=" * 80)

        # Send task to agent
        conversation.send_message(task)

        # Run the conversation
        conversation.run()

        logger.info("=" * 80)
        logger.info("Git workflow automation complete!")
        logger.info(f"Repository location: {workspace_path}")
        logger.info(
            "(Note: This is a temporary directory and will be cleaned up "
            "when the script exits)"
        )


if __name__ == "__main__":
    print("Git Workflow Automation Example")
    print("=" * 80)
    print(
        "This example demonstrates using the GitTool to automate git operations."
    )
    print(
        "The agent will create a branch, make changes, commit, and view history."
    )
    print("=" * 80)
    print()

    main()

    print()
    print("=" * 80)
    print("Example complete! Check the logs above to see the git operations.")
    print("=" * 80)
