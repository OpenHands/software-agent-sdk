"""Git operations tool implementation."""

from collections.abc import Sequence
from typing import Literal

from pydantic import Field
from rich.text import Text

from openhands.sdk.llm import ImageContent, TextContent
from openhands.sdk.tool import (
    Action,
    Observation,
    ToolAnnotations,
    ToolDefinition,
    register_tool,
)


CommandLiteral = Literal[
    "status",
    "add",
    "commit",
    "push",
    "pull",
    "branch",
    "checkout",
    "diff",
    "log",
    "clone",
    "init",
    "reset",
    "stash",
    "remote",
]


class GitAction(Action):
    """Schema for git operations."""

    command: CommandLiteral = Field(
        description=(
            "The git command to run. Allowed options are: `status`, `add`, "
            "`commit`, `push`, `pull`, `branch`, `checkout`, `diff`, `log`, "
            "`clone`, `init`, `reset`, `stash`, `remote`."
        )
    )

    # Common parameters
    repo_path: str | None = Field(
        default=None,
        description=(
            "Path to the git repository. If not provided, uses the current "
            "working directory. Required for `clone` command (as destination path)."
        ),
    )

    # add command parameters
    files: list[str] | None = Field(
        default=None,
        description=(
            "List of file paths to add. Used by `add` command. "
            "Use ['.'] to add all changes in the current directory."
        ),
    )

    # commit command parameters
    message: str | None = Field(
        default=None,
        description="Commit message. Required for `commit` command.",
    )
    all_changes: bool = Field(
        default=False,
        description=(
            "If True, automatically stage all modified and deleted files before "
            "commit. Used with `commit` command."
        ),
    )

    # branch/checkout command parameters
    branch_name: str | None = Field(
        default=None,
        description=(
            "Branch name. Used by `branch`, `checkout`, and `push` commands. "
            "For `branch`: creates a new branch. "
            "For `checkout`: switches to the branch. "
            "For `push`: specifies which branch to push."
        ),
    )
    create_branch: bool = Field(
        default=False,
        description=(
            "If True, create a new branch and switch to it. "
            "Used with `checkout` command (equivalent to `checkout -b`)."
        ),
    )

    # push/pull command parameters
    remote: str = Field(
        default="origin",
        description="Remote name for push/pull operations. Default is 'origin'.",
    )
    force: bool = Field(
        default=False,
        description="If True, force the operation. Use with caution! Used with `push`.",
    )

    # diff command parameters
    ref_1: str | None = Field(
        default=None,
        description=(
            "First reference for diff (e.g., commit hash, branch name). "
            "Used by `diff` command. If not provided, shows working directory changes."
        ),
    )
    ref_2: str | None = Field(
        default=None,
        description=(
            "Second reference for diff (e.g., commit hash, branch name). "
            "Used by `diff` command."
        ),
    )
    path_filter: str | None = Field(
        default=None,
        description="Optional path to filter diff output. Used by `diff` command.",
    )

    # log command parameters
    max_count: int = Field(
        default=10,
        ge=1,
        le=100,
        description="Maximum number of commits to show. Used by `log` command.",
    )
    oneline: bool = Field(
        default=False,
        description=(
            "If True, show each commit on a single line. Used by `log` command."
        ),
    )

    # clone command parameters
    url: str | None = Field(
        default=None,
        description="Repository URL to clone. Required for `clone` command.",
    )

    # reset command parameters
    reset_mode: Literal["soft", "mixed", "hard"] = Field(
        default="mixed",
        description=(
            "Reset mode: 'soft' (keep changes staged), 'mixed' (unstage changes), "
            "'hard' (discard all changes). Used by `reset` command."
        ),
    )
    reset_target: str = Field(
        default="HEAD",
        description=(
            "Target commit to reset to (e.g., 'HEAD', 'HEAD~1', commit hash). "
            "Used by `reset` command."
        ),
    )

    # stash command parameters
    stash_operation: Literal["save", "pop", "list", "apply", "drop", "clear"] = Field(
        default="save",
        description=(
            "Stash operation: 'save' (stash changes), 'pop' (apply and remove), "
            "'list' (show stashes), 'apply' (apply without removing), "
            "'drop' (remove stash), 'clear' (remove all stashes)."
        ),
    )
    stash_message: str | None = Field(
        default=None,
        description="Optional message for stash. Used with `stash save`.",
    )

    # remote command parameters
    remote_operation: Literal["list", "add", "remove", "show"] = Field(
        default="list",
        description=(
            "Remote operation: 'list' (show remotes), 'add' (add remote), "
            "'remove' (remove remote), 'show' (show remote details)."
        ),
    )
    remote_name: str | None = Field(
        default=None,
        description="Remote name for add/remove/show operations.",
    )
    remote_url: str | None = Field(
        default=None,
        description="Remote URL for add operation.",
    )

    @property
    def visualize(self) -> Text:
        """Return Rich Text representation with git command style."""
        content = Text()

        # Git prompt style
        content.append("git ", style="bold cyan")
        content.append(self.command, style="bold yellow")

        # Add key parameters based on command
        if self.command == "commit" and self.message:
            content.append(f' -m "{self.message}"', style="white")
        elif self.command == "branch" and self.branch_name:
            content.append(f" {self.branch_name}", style="green")
        elif self.command == "checkout" and self.branch_name:
            if self.create_branch:
                content.append(" -b", style="white")
            content.append(f" {self.branch_name}", style="green")
        elif self.command == "clone" and self.url:
            content.append(f" {self.url}", style="white")
        elif self.command == "push":
            content.append(f" {self.remote}", style="white")
            if self.branch_name:
                content.append(f" {self.branch_name}", style="green")

        return content


class GitObservation(Observation):
    """Observation from git operations."""

    command: CommandLiteral = Field(
        description="The git command that was executed."
    )
    success: bool = Field(
        default=True,
        description="Whether the git operation succeeded.",
    )

    @property
    def visualize(self) -> Text:
        """Return Rich Text representation of git operation result."""
        text = Text()

        if self.is_error:
            text.append("❌ ", style="red bold")
            text.append(self.ERROR_MESSAGE_HEADER, style="bold red")
            text.append("\n")

        # Add command indicator
        text.append(f"[git {self.command}] ", style="cyan")

        # Add content
        if self.success and not self.is_error:
            text.append("✓ ", style="green bold")

        content_text = self.text
        if content_text:
            text.append(content_text, style="white")

        return text


# Register the tool
GitTool = register_tool(
    action=GitAction,
    observation=GitObservation,
    annotations=ToolAnnotations(
        system_annotations=(
            "# Git Tool\n\n"
            "Execute git operations in the workspace. This tool provides a structured "
            "interface to git commands.\n\n"
            "## Available Commands\n\n"
            "- **status**: Show the working tree status\n"
            "- **add**: Add file contents to the staging area\n"
            "- **commit**: Record changes to the repository\n"
            "- **push**: Update remote refs along with associated objects\n"
            "- **pull**: Fetch from and integrate with another repository\n"
            "- **branch**: List, create, or delete branches\n"
            "- **checkout**: Switch branches or restore working tree files\n"
            "- **diff**: Show changes between commits, commit and working tree, etc\n"
            "- **log**: Show commit logs\n"
            "- **clone**: Clone a repository into a new directory\n"
            "- **init**: Create an empty Git repository\n"
            "- **reset**: Reset current HEAD to the specified state\n"
            "- **stash**: Stash the changes in a dirty working directory\n"
            "- **remote**: Manage set of tracked repositories\n\n"
            "## Examples\n\n"
            "Check repository status:\n"
            "```\n"
            '{"command": "status"}\n'
            "```\n\n"
            "Add files and commit:\n"
            "```\n"
            '{"command": "add", "files": ["."]}\n'
            '{"command": "commit", "message": "feat: add new feature"}\n'
            "```\n\n"
            "Create and switch to a new branch:\n"
            "```\n"
            '{"command": "checkout", "branch_name": "feature-xyz", "create_branch": true}\n'
            "```\n\n"
            "Push changes:\n"
            "```\n"
            '{"command": "push", "remote": "origin", "branch_name": "main"}\n'
            "```\n\n"
            "View recent commits:\n"
            "```\n"
            '{"command": "log", "max_count": 5, "oneline": true}\n'
            "```\n"
        ),
        default_retry_prompt=(
            "The git operation failed. Common issues:\n"
            "- Repository not initialized: Use `init` command first\n"
            "- Not in a git repository: Specify correct `repo_path`\n"
            "- Commit without staged files: Use `add` command first\n"
            "- Push without commits: Make at least one commit first\n"
            "- Branch doesn't exist: Check branch name or create it\n"
            "- Merge conflicts: Resolve conflicts manually\n"
            "- Authentication issues: Ensure credentials are configured\n\n"
            "Check the error message and try again with corrected parameters."
        ),
    ),
)
