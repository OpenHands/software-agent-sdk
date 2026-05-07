from collections.abc import Generator
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.request import urlopen

import httpx
import tenacity
from pydantic import PrivateAttr, ValidationError

from openhands.sdk.git.models import GitChange, GitDiff
from openhands.sdk.logger import get_logger
from openhands.sdk.settings import SecretsListResponse, SettingsResponse
from openhands.sdk.workspace.base import BaseWorkspace
from openhands.sdk.workspace.models import CommandResult, FileOperationResult
from openhands.sdk.workspace.remote.remote_workspace_mixin import RemoteWorkspaceMixin
from openhands.sdk.workspace.repo import (
    CloneResult,
    RepoMapping,
    RepoSource,
    clone_repos as _clone_repos_helper,
    get_repos_context as _get_repos_context_helper,
)


if TYPE_CHECKING:
    from openhands.sdk.context import AgentContext
    from openhands.sdk.llm.llm import LLM
    from openhands.sdk.secret import LookupSecret
    from openhands.sdk.settings import OpenHandsAgentSettings
    from openhands.sdk.settings.model import ACPAgentSettings, LLMAgentSettings
    from openhands.sdk.skills import Skill


logger = get_logger(__name__)

# Number of retry attempts for transient API failures
_MAX_RETRIES = 3


def _is_retryable_error(error: BaseException) -> bool:
    """Return True for transient errors that are worth retrying."""
    if isinstance(error, httpx.HTTPStatusError):
        return error.response.status_code >= 500
    return isinstance(error, (httpx.ConnectError, httpx.TimeoutException))


class RemoteWorkspace(RemoteWorkspaceMixin, BaseWorkspace):
    """Remote workspace implementation that connects to an OpenHands agent server.

    RemoteWorkspace provides access to a sandboxed environment running on a remote
    OpenHands agent server. This is the recommended approach for production deployments
    as it provides better isolation and security.

    Example:
        >>> workspace = RemoteWorkspace(
        ...     host="https://agent-server.example.com",
        ...     working_dir="/workspace"
        ... )
        >>> with workspace:
        ...     result = workspace.execute_command("ls -la")
        ...     content = workspace.read_file("README.md")
    """

    _client: httpx.Client | None = PrivateAttr(default=None)

    def reset_client(self) -> None:
        """Reset the HTTP client to force re-initialization.

        This is useful when connection parameters (host, api_key) have changed
        and the client needs to be recreated with new values.
        """
        if self._client is not None:
            try:
                self._client.close()
            except Exception:
                pass
        self._client = None

    @property
    def client(self) -> httpx.Client:
        client = self._client
        if client is None:
            # Configure reasonable timeouts for HTTP requests
            # - connect: 10 seconds to establish connection
            # - read: 600 seconds (10 minutes) to read response (for LLM operations)
            # - write: 10 seconds to send request
            # - pool: 10 seconds to get connection from pool
            timeout = httpx.Timeout(
                connect=10.0, read=self.read_timeout, write=10.0, pool=10.0
            )
            client = httpx.Client(
                base_url=self.host,
                timeout=timeout,
                headers=self._headers,
                limits=httpx.Limits(max_connections=self.max_connections),
            )
            self._client = client
        return client

    def _execute(self, generator: Generator[dict[str, Any], httpx.Response, Any]):
        try:
            kwargs = next(generator)
            while True:
                response = self.client.request(**kwargs)
                kwargs = generator.send(response)
        except StopIteration as e:
            return e.value

    def get_server_info(self) -> dict[str, Any]:
        """Return server metadata from the agent-server.

        This is useful for debugging version mismatches between the local SDK and
        the remote agent-server image.

        Returns:
            A JSON-serializable dict returned by GET /server_info.
        """
        response = self.client.get("/server_info")
        response.raise_for_status()
        data = response.json()
        assert isinstance(data, dict)
        return data

    def execute_command(
        self,
        command: str,
        cwd: str | Path | None = None,
        timeout: float = 30.0,
    ) -> CommandResult:
        """Execute a bash command on the remote system.

        This method starts a bash command via the remote agent server API,
        then polls for the output until the command completes.

        Args:
            command: The bash command to execute
            cwd: Working directory (optional)
            timeout: Timeout in seconds

        Returns:
            CommandResult: Result with stdout, stderr, exit_code, and other metadata
        """
        generator = self._execute_command_generator(command, cwd, timeout)
        result = self._execute(generator)
        return result

    def file_upload(
        self,
        source_path: str | Path,
        destination_path: str | Path,
    ) -> FileOperationResult:
        """Upload a file to the remote system.

        Reads the local file and sends it to the remote system via HTTP API.

        Args:
            source_path: Path to the local source file
            destination_path: Path where the file should be uploaded on remote system

        Returns:
            FileOperationResult: Result with success status and metadata
        """
        generator = self._file_upload_generator(source_path, destination_path)
        result = self._execute(generator)
        return result

    def file_download(
        self,
        source_path: str | Path,
        destination_path: str | Path,
    ) -> FileOperationResult:
        """Download a file from the remote system.

        Requests the file from the remote system via HTTP API and saves it locally.

        Args:
            source_path: Path to the source file on remote system
            destination_path: Path where the file should be saved locally

        Returns:
            FileOperationResult: Result with success status and metadata
        """
        generator = self._file_download_generator(source_path, destination_path)
        result = self._execute(generator)
        return result

    def git_changes(self, path: str | Path) -> list[GitChange]:
        """Get the git changes for the repository at the path given.

        Args:
            path: Path to the git repository

        Returns:
            list[GitChange]: List of changes

        Raises:
            Exception: If path is not a git repository or getting changes failed
        """
        generator = self._git_changes_generator(path)
        result = self._execute(generator)
        return result

    def git_diff(self, path: str | Path) -> GitDiff:
        """Get the git diff for the file at the path given.

        Args:
            path: Path to the file

        Returns:
            GitDiff: Git diff

        Raises:
            Exception: If path is not a git repository or getting diff failed
        """
        generator = self._git_diff_generator(path)
        result = self._execute(generator)
        return result

    @property
    def alive(self) -> bool:
        """Check if the remote workspace is alive by querying the health endpoint.

        Returns:
            True if the health endpoint returns a successful response, False otherwise.
        """
        try:
            health_url = f"{self.host}/health"
            with urlopen(health_url, timeout=5.0) as resp:
                status = getattr(resp, "status", 200)
                return 200 <= status < 300
        except Exception:
            return False

    @property
    def default_conversation_tags(self) -> dict[str, str] | None:
        """Default tags to apply to conversations created with this workspace.

        Subclasses (e.g., OpenHandsCloudWorkspace) can override this to provide
        context-specific tags like automation metadata.

        Returns:
            Dictionary of tag key-value pairs, or None if no default tags.
        """
        return None

    def register_conversation(self, conversation_id: str) -> None:
        """Register a conversation ID with this workspace.

        Called by RemoteConversation after creation to associate the conversation
        with the workspace. Subclasses can override to track conversation IDs
        for callbacks or other purposes.

        Args:
            conversation_id: The conversation ID to register
        """
        # Default implementation is a no-op
        pass

    @property
    def conversation_id(self) -> str | None:
        """Get the most recently registered conversation ID.

        Returns:
            The conversation ID if one has been registered, None otherwise.
        """
        return None

    # ── Settings Methods ──────────────────────────────────────────────────
    # These methods fetch configuration from the agent-server's persisted
    # settings endpoints. Subclasses like OpenHandsCloudWorkspace may override
    # to use alternative endpoints (e.g., Cloud API).

    def _fetch_agent_settings(
        self,
    ) -> "OpenHandsAgentSettings | LLMAgentSettings | ACPAgentSettings":
        """Call ``GET /api/settings`` and return a validated settings model.

        Uses ``X-Expose-Secrets: plaintext`` so secret fields (e.g. LLM
        api_key) are returned as plain strings.  The outer response is
        validated via :class:`SettingsResponse`, then the ``agent_settings``
        dict is validated through :func:`validate_agent_settings` which
        picks the correct discriminated-union variant
        (``OpenHandsAgentSettings`` or ``ACPAgentSettings``).
        """
        from openhands.sdk.settings import validate_agent_settings

        headers = dict(self._headers)
        headers["X-Expose-Secrets"] = "plaintext"

        response = self.client.get("/api/settings", headers=headers)
        response.raise_for_status()

        data = SettingsResponse.model_validate(response.json())
        return validate_agent_settings(data.agent_settings)

    @tenacity.retry(
        stop=tenacity.stop_after_attempt(_MAX_RETRIES),
        wait=tenacity.wait_exponential(multiplier=1, min=1, max=5),
        retry=tenacity.retry_if_exception(_is_retryable_error),
        reraise=True,
    )
    def get_llm(self, **llm_kwargs: Any) -> "LLM":
        """Fetch LLM settings from the agent-server's persisted settings.

        Calls ``GET /api/settings`` with ``X-Expose-Secrets: plaintext`` header
        to retrieve the full LLM configuration and returns a fully usable
        ``LLM`` instance.  All persisted LLM fields (model, api_key,
        base_url, temperature, max_output_tokens, …) are preserved.

        Args:
            **llm_kwargs: Additional keyword arguments that override
                persisted values (e.g., ``model``, ``temperature``).

        Returns:
            An LLM instance configured with the persisted settings.

        Raises:
            httpx.HTTPStatusError: If the API request fails.
            RuntimeError: If the workspace host is not set.

        Example:
            >>> with DockerWorkspace(...) as workspace:
            ...     llm = workspace.get_llm()
            ...     agent = Agent(llm=llm, tools=get_default_tools())
        """
        from openhands.sdk.llm.llm import LLM

        if not self.host or self.host == "undefined":
            raise RuntimeError("Workspace host is not set")

        settings = self._fetch_agent_settings()

        if not llm_kwargs:
            return settings.llm

        # Dump persisted LLM config and merge overrides, then
        # reconstruct so Pydantic validators run on the merged values
        llm_data = settings.llm.model_dump(context={"expose_secrets": "plaintext"})
        llm_data.update(llm_kwargs)
        return LLM(**llm_data)

    @tenacity.retry(
        stop=tenacity.stop_after_attempt(_MAX_RETRIES),
        wait=tenacity.wait_exponential(multiplier=1, min=1, max=5),
        retry=tenacity.retry_if_exception(_is_retryable_error),
        reraise=True,
    )
    def get_secrets(self, names: list[str] | None = None) -> dict[str, "LookupSecret"]:
        """Build ``LookupSecret`` references for the agent-server's secrets.

        Fetches the list of available secret **names** from the agent-server
        (no raw values) and returns a dict of ``LookupSecret`` objects whose
        URLs point to per-secret endpoints. The agent-server resolves each
        ``LookupSecret`` lazily, so raw values **never** transit through
        the SDK client.

        The returned dict is compatible with ``conversation.update_secrets()``.

        Args:
            names: Optional list of secret names to include. If ``None``,
                all available secrets are returned.

        Returns:
            A dictionary mapping secret names to ``LookupSecret`` instances.

        Raises:
            httpx.HTTPStatusError: If the API request fails.
            RuntimeError: If the workspace host is not set.

        Example:
            >>> with DockerWorkspace(...) as workspace:
            ...     secrets = workspace.get_secrets()
            ...     conversation.update_secrets(secrets)
            ...
            ...     # Or a subset
            ...     gh = workspace.get_secrets(names=["GITHUB_TOKEN"])
            ...     conversation.update_secrets(gh)
        """
        from openhands.sdk.secret import LookupSecret

        if not self.host or self.host == "undefined":
            raise RuntimeError("Workspace host is not set")

        response = self.client.get("/api/settings/secrets", headers=self._headers)
        response.raise_for_status()

        # Validate response using shared SDK model
        data = SecretsListResponse.model_validate(response.json())

        result: dict[str, LookupSecret] = {}
        for item in data.secrets:
            if names is not None and item.name not in names:
                continue
            result[item.name] = LookupSecret(
                url=f"{self.host}/api/settings/secrets/{item.name}",
                headers=dict(self._headers),
                description=item.description,
            )

        return result

    @tenacity.retry(
        stop=tenacity.stop_after_attempt(_MAX_RETRIES),
        wait=tenacity.wait_exponential(multiplier=1, min=1, max=5),
        retry=tenacity.retry_if_exception(_is_retryable_error),
        reraise=True,
    )
    def get_mcp_config(self) -> dict[str, Any]:
        """Fetch MCP configuration from the agent-server's persisted settings.

        Calls ``GET /api/settings`` with ``X-Expose-Secrets: plaintext`` header
        to retrieve the MCP configuration and returns a dict compatible with
        ``MCPConfig.model_validate()`` and the ``Agent(mcp_config=...)`` kwarg.

        Returns:
            A dictionary with ``mcpServers`` key containing server configurations
            (compatible with ``MCPConfig.model_validate()``), or an empty dict
            if no MCP config is set.

        Raises:
            httpx.HTTPStatusError: If the API request fails.
            RuntimeError: If the workspace host is not set.

        Example:
            >>> with DockerWorkspace(...) as workspace:
            ...     llm = workspace.get_llm()
            ...     mcp_config = workspace.get_mcp_config()
            ...     agent = Agent(llm=llm, mcp_config=mcp_config, tools=...)
            ...
            ...     # Or validate as MCPConfig:
            ...     from fastmcp.mcp_config import MCPConfig
            ...     config = MCPConfig.model_validate(mcp_config)
        """
        from openhands.sdk.settings import OpenHandsAgentSettings

        if not self.host or self.host == "undefined":
            raise RuntimeError("Workspace host is not set")

        settings = self._fetch_agent_settings()

        # mcp_config only exists on OpenHandsAgentSettings, not ACPAgentSettings
        if not isinstance(settings, OpenHandsAgentSettings):
            return {}

        if settings.mcp_config is None:
            return {}

        return settings.mcp_config.model_dump(exclude_none=True, exclude_defaults=True)

    # ── Repository Cloning Methods ─────────────────────────────────────────

    def _get_secret_value(self, name: str) -> str | None:
        """Fetch a secret value directly from the agent server's settings API.

        Unlike get_secrets() which returns LookupSecret references, this method
        fetches the actual secret value for use in operations like git cloning.
        Retries up to 3 times on transient failures.

        Args:
            name: Name of the secret to fetch (e.g., "github_token", "gitlab_token")

        Returns:
            The secret value as a string, or None if not found or an error occurred.
        """
        if not self.host or self.host == "undefined":
            return None

        # Validate secret name to prevent path traversal
        if not name or "/" in name or ".." in name:
            logger.warning(f"Invalid secret name: {name}")
            return None

        # Use retry logic for transient failures
        @tenacity.retry(
            stop=tenacity.stop_after_attempt(_MAX_RETRIES),
            wait=tenacity.wait_exponential(multiplier=1, min=1, max=5),
            retry=tenacity.retry_if_exception(_is_retryable_error),
            reraise=True,
        )
        def _fetch_secret() -> httpx.Response:
            resp = self.client.get(
                f"/api/settings/secrets/{name}",
                headers=self._headers,
            )
            resp.raise_for_status()
            return resp

        try:
            resp = _fetch_secret()
            return resp.text
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                logger.debug(f"Secret '{name}' not found")
            else:
                logger.warning(f"Failed to fetch secret '{name}': {e}")
            return None
        except Exception as e:
            logger.warning(f"Error fetching secret '{name}': {e}")
            return None

    def clone_repos(
        self,
        repos: list[RepoSource | dict[str, Any] | str],
        target_dir: str | Path | None = None,
    ) -> CloneResult:
        """Clone repositories to the workspace directory.

        Clones specified repositories to meaningful directory names (e.g.,
        'openhands-cli' instead of 'repo_0'). Automatically fetches GitHub,
        GitLab, and Bitbucket tokens from the agent server's secrets for
        authentication.

        Args:
            repos: List of repositories to clone. Can be:
                - List of RepoSource objects
                - List of dicts with 'url', optional 'ref', and 'provider' keys
                - List of full URL strings (e.g., "https://github.com/owner/repo")
                Note: Short URLs (owner/repo) require explicit 'provider' field.
            target_dir: Directory to clone into. Defaults to self.working_dir.

        Returns:
            CloneResult containing:
                - success_count: Number of successfully cloned repos
                - failed_repos: List of repo URLs that failed to clone
                - repo_mappings: Dict mapping URLs to RepoMapping objects

        Example:
            >>> with RemoteWorkspace(...) as workspace:
            ...     # Clone with full URLs (provider auto-detected)
            ...     result = workspace.clone_repos([
            ...         "https://github.com/owner/repo1",
            ...         {"url": "https://gitlab.com/owner/repo2", "ref": "main"},
            ...     ])
            ...
            ...     # Clone with short URLs (provider required)
            ...     result = workspace.clone_repos([
            ...         {"url": "owner/repo1", "provider": "github"},
            ...         {"url": "owner/repo2", "provider": "gitlab", "ref": "v1.0"},
            ...     ])
            ...
            ...     # Access cloned repo paths
            ...     for url, mapping in result.repo_mappings.items():
            ...         print(f"{url} -> {mapping.local_path}")
        """
        # Normalize repos to RepoSource objects using model_validate
        # This ensures consistent validation for all input formats
        normalized_repos: list[RepoSource] = []
        try:
            for repo in repos:
                if isinstance(repo, RepoSource):
                    normalized_repos.append(repo)
                else:
                    # model_validate handles dicts and strings via model_validator
                    normalized_repos.append(RepoSource.model_validate(repo))
        except ValidationError as e:
            raise ValueError(f"Invalid repository specification: {e}") from e

        # Determine target directory
        if target_dir is None:
            target_path = Path(self.working_dir)
        elif isinstance(target_dir, str):
            target_path = Path(target_dir)
        else:
            target_path = target_dir

        # Clone repositories using _get_secret_value as token fetcher
        # This fetches tokens lazily based on each repo's provider
        return _clone_repos_helper(
            repos=normalized_repos,
            target_dir=target_path,
            token_fetcher=self._get_secret_value,
        )

    def get_repos_context(self, repo_mappings: dict[str, RepoMapping]) -> str:
        """Generate context string describing cloned repositories for the agent.

        This method produces a markdown-formatted string that can be prepended
        to agent prompts to inform the agent about available repositories.

        Args:
            repo_mappings: Dict mapping URLs to RepoMapping objects, typically
                obtained from CloneResult.repo_mappings after calling clone_repos().

        Returns:
            Markdown-formatted context string, or empty string if no repos.

        Example:
            >>> with RemoteWorkspace(...) as workspace:
            ...     result = workspace.clone_repos(["owner/repo"])
            ...     context = workspace.get_repos_context(result.repo_mappings)
            ...     prompt = f"{context}\\n\\n{user_prompt}"
        """
        return _get_repos_context_helper(repo_mappings)

    # ── Skill Loading Methods ──────────────────────────────────────────────

    def _call_skills_api(
        self,
        project_dir: str,
        load_public: bool = False,
        load_user: bool = False,
        load_project: bool = False,
        load_org: bool = False,
        timeout: float = 60.0,
    ) -> list[dict[str, Any]]:
        """Call the agent-server /api/skills endpoint.

        Returns list of skill dicts, or empty list on error.
        Retries up to 3 times on transient failures.
        """
        payload = {
            "load_public": load_public,
            "load_user": load_user,
            "load_project": load_project,
            "load_org": load_org,
            "project_dir": project_dir,
            "org_config": None,
            "sandbox_config": None,
        }

        headers: dict[str, str] = {"Content-Type": "application/json"}
        headers.update(self._headers)

        # Use retry logic for transient failures
        @tenacity.retry(
            stop=tenacity.stop_after_attempt(_MAX_RETRIES),
            wait=tenacity.wait_exponential(multiplier=1, min=1, max=5),
            retry=tenacity.retry_if_exception(_is_retryable_error),
            reraise=True,
        )
        def _fetch_skills() -> httpx.Response:
            resp = self.client.post(
                f"{self.host}/api/skills",
                json=payload,
                headers=headers,
                timeout=timeout,
            )
            resp.raise_for_status()
            return resp

        try:
            resp = _fetch_skills()
            data = resp.json()
            logger.debug(f"Agent-server sources: {data.get('sources', {})}")
            return data.get("skills", [])
        except httpx.HTTPStatusError as e:
            logger.error(f"Agent-server HTTP error {e.response.status_code}")
            return []
        except Exception as e:
            logger.error(f"Failed to connect to agent-server: {e}")
            return []

    def _add_skills_to_dict(
        self,
        skills_by_name: dict[str, dict[str, Any]],
        skill_list: list[dict[str, Any]],
    ) -> None:
        """Add skills to dict, keyed by name (later values override)."""
        for skill_data in skill_list:
            name = skill_data.get("name", "unknown")
            skills_by_name[name] = skill_data

    def _load_skills_multi_dir(
        self,
        project_dirs: list[str],
        load_public: bool,
        load_user: bool,
        load_project: bool,
        load_org: bool,
        timeout: float,
    ) -> dict[str, dict[str, Any]]:
        """Load skills when multiple project directories are specified."""
        skills_by_name: dict[str, dict[str, Any]] = {}

        # Load global skills (public/user/org) once
        logger.debug("Loading public/user/org skills...")
        global_skills = self._call_skills_api(
            project_dir=self.working_dir,
            load_public=load_public,
            load_user=load_user,
            load_project=False,
            load_org=load_org,
            timeout=timeout,
        )
        self._add_skills_to_dict(skills_by_name, global_skills)

        # Load project skills from each directory
        if not load_project:
            return skills_by_name

        for dir_path in project_dirs:
            logger.debug(f"Loading project skills from {dir_path}...")
            proj_skills = self._call_skills_api(
                project_dir=dir_path,
                load_project=True,
                timeout=timeout,
            )
            self._add_skills_to_dict(skills_by_name, proj_skills)

        return skills_by_name

    def _load_skills_single_dir(
        self,
        load_public: bool,
        load_user: bool,
        load_project: bool,
        load_org: bool,
        timeout: float,
    ) -> dict[str, dict[str, Any]]:
        """Load all skills from the working directory."""
        logger.debug("Loading all skills from working_dir...")
        all_skills = self._call_skills_api(
            project_dir=self.working_dir,
            load_public=load_public,
            load_user=load_user,
            load_project=load_project,
            load_org=load_org,
            timeout=timeout,
        )

        skills_by_name: dict[str, dict[str, Any]] = {}
        self._add_skills_to_dict(skills_by_name, all_skills)
        return skills_by_name

    def _convert_skills_dict_to_list(
        self, skills_by_name: dict[str, dict[str, Any]]
    ) -> list["Skill"]:
        """Convert skill dicts to SDK Skill objects."""
        loaded_skills: list[Skill] = []
        for skill_data in skills_by_name.values():
            try:
                skill = self._convert_skill_data_to_skill(skill_data)
                loaded_skills.append(skill)
            except Exception as e:
                skill_name = skill_data.get("name", "unknown")
                logger.warning(f"Failed to convert skill {skill_name}: {e}")
        return loaded_skills

    def _convert_skill_data_to_skill(self, skill_data: dict[str, Any]) -> "Skill":
        """Convert skill dict from API response to SDK Skill object.

        Args:
            skill_data: Dict with name, content, triggers, source, description, etc.

        Returns:
            Skill object
        """
        from openhands.sdk.skills import KeywordTrigger, Skill, TaskTrigger

        trigger = None
        triggers = skill_data.get("triggers", [])

        if triggers:
            # Determine trigger type based on content (same logic as OpenHands)
            # Note: Validate elements are strings before calling .startswith()
            if any(isinstance(t, str) and t.startswith("/") for t in triggers):
                trigger = TaskTrigger(triggers=triggers)
            else:
                trigger = KeywordTrigger(keywords=triggers)

        return Skill(
            name=skill_data.get("name", "unknown"),
            content=skill_data.get("content", ""),
            trigger=trigger,
            source=skill_data.get("source"),
            description=skill_data.get("description"),
            is_agentskills_format=skill_data.get("is_agentskills_format", False),
        )

    def load_skills_from_agent_server(
        self,
        project_dirs: list[str | Path] | None = None,
        load_public: bool = True,
        load_user: bool = True,
        load_project: bool = True,
        load_org: bool = True,
        timeout: float = 60.0,
    ) -> tuple[list["Skill"], "AgentContext"]:
        """Load skills via the agent-server's /api/skills endpoint.

        This method calls the agent-server running inside the sandbox to load
        skills from all configured sources, mirroring how V1 conversations
        load skills in OpenHands.

        When project_dirs is provided (e.g., directories of cloned repos),
        project skills are loaded from EACH directory separately and merged.
        Skills are deduplicated by name, with later directories taking
        precedence over earlier ones.

        Args:
            project_dirs: List of directories to load project skills from.
                If None, uses self.working_dir only.
            load_public: Load public skills from OpenHands/extensions repo.
            load_user: Load user skills from ~/.openhands/skills/.
            load_project: Load project skills from workspace directories.
            load_org: Load organization-level skills.
            timeout: Request timeout in seconds.

        Returns:
            Tuple of (list of Skill objects, AgentContext).
            The AgentContext is pre-configured with loaded skills and
            load_public_skills=False to avoid duplicates (or True if no skills loaded).

        Example:
            >>> with RemoteWorkspace(...) as workspace:
            ...     # Load all skills using working_dir
            ...     skills, context = workspace.load_skills_from_agent_server()
            ...
            ...     # Load skills from cloned repos
            ...     result = workspace.clone_repos(["owner/repo1", "owner/repo2"])
            ...     repo_dirs = [m.local_path for m in result.repo_mappings.values()]
            ...     skills, context = workspace.load_skills_from_agent_server(
            ...         project_dirs=repo_dirs
            ...     )
            ...
            ...     # Use with agent
            ...     agent = agent.model_copy(update={"agent_context": context})
        """
        from openhands.sdk.context import AgentContext

        # Validate workspace is ready for API calls
        # Note: self.host defaults to "undefined" so check for that too
        if not self.host or self.host == "undefined":
            raise RuntimeError(
                "Workspace not initialized. Ensure the workspace is started "
                "before loading skills."
            )

        logger.info("Loading skills via agent-server...")
        logger.debug(f"Agent-server URL: {self.host}")

        # Load skills based on whether multiple project dirs are specified
        if project_dirs:
            dirs = [str(d) if isinstance(d, Path) else d for d in project_dirs]
            skills_by_name = self._load_skills_multi_dir(
                dirs, load_public, load_user, load_project, load_org, timeout
            )
        else:
            skills_by_name = self._load_skills_single_dir(
                load_public, load_user, load_project, load_org, timeout
            )

        # Convert to SDK Skill objects
        loaded_skills = self._convert_skills_dict_to_list(skills_by_name)

        logger.info(f"Loaded {len(loaded_skills)} skills")
        if loaded_skills:
            logger.debug(f"Skills: {[s.name for s in loaded_skills]}")

        # Create AgentContext - fall back to public skills if none loaded
        if loaded_skills:
            agent_context = AgentContext(skills=loaded_skills, load_public_skills=False)
        else:
            logger.warning("No skills loaded, falling back to public skills")
            agent_context = AgentContext(skills=[], load_public_skills=True)

        return loaded_skills, agent_context
