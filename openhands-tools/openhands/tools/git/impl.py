"""Git tool executor implementation."""

import os
from pathlib import Path
from typing import TYPE_CHECKING

from openhands.sdk.git.exceptions import GitCommandError, GitRepositoryError
from openhands.sdk.git.git_changes import get_changes_in_repo
from openhands.sdk.git.utils import run_git_command, validate_git_repository
from openhands.sdk.logger import get_logger
from openhands.sdk.tool import ToolExecutor


if TYPE_CHECKING:
    from openhands.sdk.conversation import LocalConversation

from openhands.tools.git.definition import GitAction, GitObservation


logger = get_logger(__name__)


class GitExecutor(ToolExecutor[GitAction, GitObservation]):
    """Executor for git operations."""

    def __init__(self, working_dir: str | None = None):
        """Initialize GitExecutor.

        Args:
            working_dir: Default working directory for git operations.
                        If not provided, uses current directory.
        """
        self.working_dir = Path(working_dir) if working_dir else Path.cwd()
        logger.info(f"GitExecutor initialized with working_dir: {self.working_dir}")

    def __call__(
        self,
        action: GitAction,
        conversation: "LocalConversation | None" = None,  # noqa: ARG002
    ) -> GitObservation:
        """Execute a git action.

        Args:
            action: The git action to execute
            conversation: Optional conversation context (unused)

        Returns:
            GitObservation with the result
        """
        try:
            # Determine repository path
            repo_path = (
                Path(action.repo_path).resolve()
                if action.repo_path
                else self.working_dir
            )

            # Route to appropriate handler
            if action.command == "status":
                return self._handle_status(repo_path)
            elif action.command == "add":
                return self._handle_add(repo_path, action)
            elif action.command == "commit":
                return self._handle_commit(repo_path, action)
            elif action.command == "push":
                return self._handle_push(repo_path, action)
            elif action.command == "pull":
                return self._handle_pull(repo_path, action)
            elif action.command == "branch":
                return self._handle_branch(repo_path, action)
            elif action.command == "checkout":
                return self._handle_checkout(repo_path, action)
            elif action.command == "diff":
                return self._handle_diff(repo_path, action)
            elif action.command == "log":
                return self._handle_log(repo_path, action)
            elif action.command == "clone":
                return self._handle_clone(action)
            elif action.command == "init":
                return self._handle_init(repo_path)
            elif action.command == "reset":
                return self._handle_reset(repo_path, action)
            elif action.command == "stash":
                return self._handle_stash(repo_path, action)
            elif action.command == "remote":
                return self._handle_remote(repo_path, action)
            else:
                return GitObservation.from_text(
                    text=f"Unknown git command: {action.command}",
                    command=action.command,
                    is_error=True,
                    success=False,
                )

        except (GitCommandError, GitRepositoryError) as e:
            logger.error(f"Git operation failed: {e}")
            return GitObservation.from_text(
                text=str(e),
                command=action.command,
                is_error=True,
                success=False,
            )
        except Exception as e:
            logger.error(f"Unexpected error in git operation: {e}")
            return GitObservation.from_text(
                text=f"Unexpected error: {e}",
                command=action.command,
                is_error=True,
                success=False,
            )

    def _handle_status(self, repo_path: Path) -> GitObservation:
        """Handle git status command."""
        validated_repo = validate_git_repository(repo_path)

        # Get porcelain status for machine-readable format
        output = run_git_command(
            ["git", "--no-pager", "status", "--porcelain", "-b"],
            cwd=validated_repo,
        )

        if not output:
            result_text = "On branch main\nnothing to commit, working tree clean"
        else:
            # Also get human-readable status
            human_output = run_git_command(
                ["git", "--no-pager", "status"],
                cwd=validated_repo,
            )
            result_text = human_output

        return GitObservation.from_text(
            text=result_text,
            command="status",
            success=True,
        )

    def _handle_add(self, repo_path: Path, action: GitAction) -> GitObservation:
        """Handle git add command."""
        if not action.files:
            return GitObservation.from_text(
                text="No files specified. Use 'files' parameter to specify files to add.",
                command="add",
                is_error=True,
                success=False,
            )

        validated_repo = validate_git_repository(repo_path)

        # Add files
        args = ["git", "add"] + action.files
        output = run_git_command(args, cwd=validated_repo)

        # Get status to show what was added
        status_output = run_git_command(
            ["git", "--no-pager", "status", "--short"],
            cwd=validated_repo,
        )

        result_text = f"Added files: {', '.join(action.files)}"
        if status_output:
            result_text += f"\n\nCurrent status:\n{status_output}"

        return GitObservation.from_text(
            text=result_text,
            command="add",
            success=True,
        )

    def _handle_commit(self, repo_path: Path, action: GitAction) -> GitObservation:
        """Handle git commit command."""
        if not action.message:
            return GitObservation.from_text(
                text="Commit message is required. Use 'message' parameter.",
                command="commit",
                is_error=True,
                success=False,
            )

        validated_repo = validate_git_repository(repo_path)

        # Build commit command
        args = ["git", "commit", "-m", action.message]
        if action.all_changes:
            args.insert(2, "-a")

        output = run_git_command(args, cwd=validated_repo)

        return GitObservation.from_text(
            text=output if output else "Changes committed successfully.",
            command="commit",
            success=True,
        )

    def _handle_push(self, repo_path: Path, action: GitAction) -> GitObservation:
        """Handle git push command."""
        validated_repo = validate_git_repository(repo_path)

        # Build push command
        args = ["git", "push", action.remote]
        if action.branch_name:
            args.append(action.branch_name)
        if action.force:
            args.insert(2, "--force")

        output = run_git_command(args, cwd=validated_repo, timeout=60)

        return GitObservation.from_text(
            text=output if output else "Pushed successfully.",
            command="push",
            success=True,
        )

    def _handle_pull(self, repo_path: Path, action: GitAction) -> GitObservation:
        """Handle git pull command."""
        validated_repo = validate_git_repository(repo_path)

        # Build pull command
        args = ["git", "pull", action.remote]
        if action.branch_name:
            args.append(action.branch_name)

        output = run_git_command(args, cwd=validated_repo, timeout=60)

        return GitObservation.from_text(
            text=output if output else "Pulled successfully.",
            command="pull",
            success=True,
        )

    def _handle_branch(self, repo_path: Path, action: GitAction) -> GitObservation:
        """Handle git branch command."""
        validated_repo = validate_git_repository(repo_path)

        if action.branch_name:
            # Create new branch
            output = run_git_command(
                ["git", "branch", action.branch_name],
                cwd=validated_repo,
            )
            result_text = f"Created branch: {action.branch_name}"
        else:
            # List branches
            output = run_git_command(
                ["git", "branch", "-a"],
                cwd=validated_repo,
            )
            result_text = f"Branches:\n{output}"

        return GitObservation.from_text(
            text=result_text,
            command="branch",
            success=True,
        )

    def _handle_checkout(self, repo_path: Path, action: GitAction) -> GitObservation:
        """Handle git checkout command."""
        if not action.branch_name:
            return GitObservation.from_text(
                text="Branch name is required. Use 'branch_name' parameter.",
                command="checkout",
                is_error=True,
                success=False,
            )

        validated_repo = validate_git_repository(repo_path)

        # Build checkout command
        args = ["git", "checkout"]
        if action.create_branch:
            args.append("-b")
        args.append(action.branch_name)

        output = run_git_command(args, cwd=validated_repo)

        action_desc = "Created and switched to" if action.create_branch else "Switched to"
        result_text = f"{action_desc} branch: {action.branch_name}"
        if output:
            result_text += f"\n{output}"

        return GitObservation.from_text(
            text=result_text,
            command="checkout",
            success=True,
        )

    def _handle_diff(self, repo_path: Path, action: GitAction) -> GitObservation:
        """Handle git diff command."""
        validated_repo = validate_git_repository(repo_path)

        # Build diff command
        args = ["git", "--no-pager", "diff"]
        if action.ref_1:
            args.append(action.ref_1)
        if action.ref_2:
            args.append(action.ref_2)
        if action.path_filter:
            args.extend(["--", action.path_filter])

        output = run_git_command(args, cwd=validated_repo)

        if not output:
            result_text = "No differences found."
        else:
            result_text = output

        return GitObservation.from_text(
            text=result_text,
            command="diff",
            success=True,
        )

    def _handle_log(self, repo_path: Path, action: GitAction) -> GitObservation:
        """Handle git log command."""
        validated_repo = validate_git_repository(repo_path)

        # Build log command
        args = ["git", "--no-pager", "log", f"--max-count={action.max_count}"]
        if action.oneline:
            args.append("--oneline")

        output = run_git_command(args, cwd=validated_repo)

        if not output:
            result_text = "No commits yet."
        else:
            result_text = output

        return GitObservation.from_text(
            text=result_text,
            command="log",
            success=True,
        )

    def _handle_clone(self, action: GitAction) -> GitObservation:
        """Handle git clone command."""
        if not action.url:
            return GitObservation.from_text(
                text="Repository URL is required. Use 'url' parameter.",
                command="clone",
                is_error=True,
                success=False,
            )

        # Build clone command
        args = ["git", "clone", action.url]
        if action.repo_path:
            args.append(action.repo_path)

        # Clone doesn't need a repo to exist first, so we run from working_dir
        output = run_git_command(args, cwd=self.working_dir, timeout=120)

        result_text = f"Cloned repository from {action.url}"
        if action.repo_path:
            result_text += f" to {action.repo_path}"
        if output:
            result_text += f"\n{output}"

        return GitObservation.from_text(
            text=result_text,
            command="clone",
            success=True,
        )

    def _handle_init(self, repo_path: Path) -> GitObservation:
        """Handle git init command."""
        # Ensure directory exists
        repo_path.mkdir(parents=True, exist_ok=True)

        output = run_git_command(["git", "init"], cwd=repo_path)

        result_text = f"Initialized git repository at {repo_path}"
        if output:
            result_text += f"\n{output}"

        return GitObservation.from_text(
            text=result_text,
            command="init",
            success=True,
        )

    def _handle_reset(self, repo_path: Path, action: GitAction) -> GitObservation:
        """Handle git reset command."""
        validated_repo = validate_git_repository(repo_path)

        # Build reset command
        mode_flag = f"--{action.reset_mode}"
        args = ["git", "reset", mode_flag, action.reset_target]

        output = run_git_command(args, cwd=validated_repo)

        result_text = (
            f"Reset to {action.reset_target} ({action.reset_mode} mode)"
        )
        if output:
            result_text += f"\n{output}"

        return GitObservation.from_text(
            text=result_text,
            command="reset",
            success=True,
        )

    def _handle_stash(self, repo_path: Path, action: GitAction) -> GitObservation:
        """Handle git stash command."""
        validated_repo = validate_git_repository(repo_path)

        # Build stash command
        if action.stash_operation == "save":
            args = ["git", "stash", "push"]
            if action.stash_message:
                args.extend(["-m", action.stash_message])
        elif action.stash_operation == "list":
            args = ["git", "stash", "list"]
        elif action.stash_operation in ("pop", "apply", "drop", "clear"):
            args = ["git", "stash", action.stash_operation]
        else:
            return GitObservation.from_text(
                text=f"Unknown stash operation: {action.stash_operation}",
                command="stash",
                is_error=True,
                success=False,
            )

        output = run_git_command(args, cwd=validated_repo)

        if not output:
            result_text = f"Stash {action.stash_operation} completed."
        else:
            result_text = output

        return GitObservation.from_text(
            text=result_text,
            command="stash",
            success=True,
        )

    def _handle_remote(self, repo_path: Path, action: GitAction) -> GitObservation:
        """Handle git remote command."""
        validated_repo = validate_git_repository(repo_path)

        # Build remote command
        if action.remote_operation == "list":
            args = ["git", "remote", "-v"]
        elif action.remote_operation == "add":
            if not action.remote_name or not action.remote_url:
                return GitObservation.from_text(
                    text=(
                        "Remote name and URL are required for add operation. "
                        "Use 'remote_name' and 'remote_url' parameters."
                    ),
                    command="remote",
                    is_error=True,
                    success=False,
                )
            args = ["git", "remote", "add", action.remote_name, action.remote_url]
        elif action.remote_operation == "remove":
            if not action.remote_name:
                return GitObservation.from_text(
                    text=(
                        "Remote name is required for remove operation. "
                        "Use 'remote_name' parameter."
                    ),
                    command="remote",
                    is_error=True,
                    success=False,
                )
            args = ["git", "remote", "remove", action.remote_name]
        elif action.remote_operation == "show":
            if not action.remote_name:
                return GitObservation.from_text(
                    text=(
                        "Remote name is required for show operation. "
                        "Use 'remote_name' parameter."
                    ),
                    command="remote",
                    is_error=True,
                    success=False,
                )
            args = ["git", "remote", "show", action.remote_name]
        else:
            return GitObservation.from_text(
                text=f"Unknown remote operation: {action.remote_operation}",
                command="remote",
                is_error=True,
                success=False,
            )

        output = run_git_command(args, cwd=validated_repo)

        if not output:
            if action.remote_operation == "list":
                result_text = "No remotes configured."
            else:
                result_text = f"Remote {action.remote_operation} completed."
        else:
            result_text = output

        return GitObservation.from_text(
            text=result_text,
            command="remote",
            success=True,
        )
