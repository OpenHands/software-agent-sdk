# Workspaces and sandbox backends

A workspace is where an agent's commands and files live, and the SDK object a program hands to `Conversation(workspace=...)`. `LocalWorkspace(working_dir=...)` (or a plain path, or `Workspace(working_dir=...)`) runs everything in-process on the host: `execute_command`, `file_upload` and `file_download` (local copies), `git_changes` and `git_diff`, with `pause()`/`resume()` as no-ops. `RemoteWorkspace(host=..., api_key=...)` (or `Workspace(host=...)`) talks to a running agent server over HTTP: it starts a bash command and polls its output events, moves files, reads git state, checks `alive`, and fetches LLM settings, secrets, MCP config and skills from the server; `AsyncRemoteWorkspace` is the async twin. The backends in `openhands-workspace` are `RemoteWorkspace` subclasses that first create the server and then behave like a remote workspace: `DockerWorkspace` runs the agent-server image with `docker run` (`DockerDevWorkspace` builds it from a base image first), `APIRemoteWorkspace` asks a runtime API for a pod, `OpenHandsCloudWorkspace` creates or resumes an OpenHands Cloud sandbox (and can inherit the account's LLM and secrets), `ApptainerWorkspace` runs a SIF built from the image without root, and `AgentSandboxWorkspace` claims a pod from a Kubernetes agent-sandbox warm pool. Each waits for the server's health endpoint, supports the context manager, cleans up on exit (unless told to keep the sandbox), and most support pause and resume. Every workspace can POST a completion callback on exit when `AUTOMATION_CALLBACK_URL` is set.

Source: `openhands-sdk/openhands/sdk/workspace/`, `openhands-workspace/openhands/workspace/docker/`, `openhands-workspace/openhands/workspace/remote_api/`, `openhands-workspace/openhands/workspace/cloud/`, `openhands-workspace/openhands/workspace/apptainer/`, `openhands-workspace/openhands/workspace/agent_sandbox/`

Needs: `docker` (DockerWorkspace, DockerDevWorkspace), `apptainer` (ApptainerWorkspace), `cloud` (OpenHandsCloudWorkspace, APIRemoteWorkspace runtime API), `k8s` (AgentSandboxWorkspace), `network` (image pulls, remote APIs), `llm` (examples), `git` (git_changes / git_diff)

Examples: `examples/02_remote_agent_server/02_convo_with_docker_sandboxed_server.py`, `examples/02_remote_agent_server/04_convo_with_api_sandboxed_server.py`, `examples/02_remote_agent_server/07_convo_with_cloud_workspace.py`, `examples/02_remote_agent_server/08_convo_with_apptainer_sandboxed_server.py`, `examples/02_remote_agent_server/10_cloud_workspace_share_credentials.py`, `examples/02_remote_agent_server/17_convo_with_agent_sandbox_server.py`

## Sub-features

- `S18.workspace-factory`: `Workspace(working_dir=...)` returns a `LocalWorkspace` and `Workspace(host=..., api_key=...)` a `RemoteWorkspace`; `working_dir` accepts `str` or `Path`. Unit: **none (untested)**; nothing calls the `Workspace` factory. Example: none. Real-LLM: none.
- `S18.local-ops`: `LocalWorkspace.execute_command(cmd, cwd=None, timeout=...)` returns a `CommandResult` (`exit_code`, `stdout`, `stderr`, `timeout_occurred`), `file_upload`/`file_download` copy files (creating parent directories) and return a `FileOperationResult`, and `git_changes`/`git_diff` report the repository state. Unit: **none (untested)** for the workspace methods; the git helpers they call are covered by `tests/sdk/git/test_git_changes.py` and `tests/sdk/git/test_git_diff.py`. Example: none. Real-LLM: none.
- `S18.local-pause-resume`: `LocalWorkspace.pause()` and `resume()` do nothing and do not raise. Unit: `tests/workspace/test_workspace_pause_resume.py` (`test_local_workspace_pause_is_noop`, `test_local_workspace_resume_is_noop`). Example: none. Real-LLM: none.
- `S18.conversation-binding`: `Conversation(workspace=<path or LocalWorkspace>)` builds a `LocalConversation`, `Conversation(workspace=<RemoteWorkspace subclass>)` a `RemoteConversation`, with parameters forwarded to each. Unit: `tests/sdk/conversation/test_conversation_factory.py`. Example: `examples/02_remote_agent_server/02_convo_with_docker_sandboxed_server.py`. Real-LLM: none.
- `S18.completion-callback`: on context exit any workspace POSTs `{status, run_id, conversation_id, cost, error}` to `AUTOMATION_CALLBACK_URL` (Bearer `AUTOMATION_CALLBACK_API_KEY`, `AUTOMATION_RUN_ID`), with the cost registered through `register_cost` and the conversation id from `register_conversation`; it is skipped without the URL and swallows errors. Unit: `tests/sdk/workspace/test_local_workspace.py`, `tests/sdk/workspace/remote/test_remote_workspace.py` (`test_send_completion_callback_*`, `test_register_cost_stores_cost`), `tests/workspace/test_api_remote_workspace.py::test_api_remote_workspace_exit_sends_callback`, `tests/workspace/test_cloud_workspace.py` (`test_callback_*`). Example: none. Real-LLM: none.
- `S18.remote-client`: `RemoteWorkspace` normalizes `host`, creates its `httpx` client lazily with `base_url`, sends `X-Session-API-Key` when `api_key` is set plus observability headers, and can `reset_client()`. Unit: `tests/sdk/workspace/remote/test_remote_workspace.py`, `tests/sdk/workspace/remote/test_remote_workspace_mixin.py`, `tests/sdk/workspace/remote/test_client_base_url.py`. Example: `examples/02_remote_agent_server/01_convo_with_local_agent_server.py`. Real-LLM: none.
- `S18.remote-command-exec`: `execute_command` starts `start_bash_command`, polls `bash_events/search` filtered by its command id until an exit code or the timeout, decodes base64 output, and never repeats an output chunk across polls; concurrent commands do not mix output. Unit: `tests/sdk/workspace/remote/test_remote_workspace_mixin.py` (`test_execute_command_generator_*`), `tests/sdk/workspace/remote/test_polling_duplicates_output.py`, `tests/sdk/workspace/remote/test_multiple_commands_isolation.py`. Example: `examples/02_remote_agent_server/02_convo_with_docker_sandboxed_server.py` (`workspace.execute_command(...)`). Real-LLM: none.
- `S18.remote-files-git`: `file_upload`, `file_download`, `git_changes` and `git_diff` go through the server's file and git routes and accept `Path` arguments. Unit: `tests/sdk/workspace/remote/test_remote_workspace.py` (`test_file_upload`, `test_file_download`, `test_file_operations_with_path_objects`), `tests/sdk/workspace/remote/test_runtime_scope.py`. Example: none. Real-LLM: none.
- `S18.runtime-scope`: a workspace bound to a conversation sends command, file and git calls to `/api/conversations/{id}/...` with its runtime session key, can `release_runtime()`, closes its async client even when the body fails, and rejects a missing runtime credential. Unit: `tests/sdk/workspace/remote/test_runtime_scope.py`. Example: none. Real-LLM: none.
- `S18.remote-alive`: `alive` is true on a 2xx from the server's health route and false on server errors, client errors, connection errors and timeouts. Unit: `tests/sdk/workspace/remote/test_remote_workspace.py` (`test_alive_*`). Example: none. Real-LLM: none.
- `S18.async-remote`: `AsyncRemoteWorkspace` offers the same command, file and git operations as coroutines under `async with`. Unit: `tests/sdk/workspace/remote/test_async_remote_workspace.py`, `tests/sdk/workspace/remote/test_runtime_scope.py`. Example: none. Real-LLM: none.
- `S18.remote-settings`: `workspace.get_llm(profile_name=None, **overrides)` builds an `LLM` from the server's active profile (or a named one, or legacy settings), `get_secrets(names=None)` returns `LookupSecret` references, `get_mcp_config()` returns MCP servers, and `_get_secret_value` returns `None` on 404. Unit: `tests/sdk/workspace/remote/test_remote_workspace.py` (`test_get_llm_*`, `test_get_secrets_*`, `test_get_mcp_config_*`, `test_get_secret_value_*`). Example: `examples/02_remote_agent_server/13_workspace_get_llm.py`. Real-LLM: none.
- `S18.repos-and-skills`: `clone_repos(repos, target_dir=...)` accepts URLs, `owner/repo` with a provider, or dicts (`RepoSource`), clones with provider tokens and returns `RepoMapping`s; `get_repos_context()` renders them for the prompt; `load_skills_from_agent_server()` fetches skills and falls back to public skills. Unit: `tests/sdk/workspace/remote/test_remote_workspace.py` (`test_clone_repos_*`, `test_get_repos_context_*`, `test_load_skills_from_agent_server_*`), `tests/workspace/test_cloud_workspace_repos.py`. Example: none. Real-LLM: none.
- `S18.docker-start`: `DockerWorkspace(server_image=..., host_port=..., forward_env=[...], volumes=[...], network=..., platform=..., enable_gpu=..., extra_ports=..., health_check_timeout=120)` runs the container, publishes ports on 127.0.0.1, forwards `SESSION_API_KEY`/`OH_SESSION_API_KEYS_0`, streams logs, and waits for health; the removed `mount_dir` argument fails loudly. Unit: `tests/workspace/test_docker_workspace.py`. Example: `examples/02_remote_agent_server/02_convo_with_docker_sandboxed_server.py`. Real-LLM: none.
- `S18.docker-dev-build`: `DockerDevWorkspace(base_image=..., target="binary"|"source")` builds the agent-server image from a base image before starting; importing `DockerWorkspace` does not import the build code. Unit: `tests/workspace/test_docker_workspace.py` (`test_docker_dev_workspace_*`, `test_docker_workspace_no_build_import`). Example: `examples/02_remote_agent_server/02_convo_with_docker_sandboxed_server.py` (commented alternative). Real-LLM: none.
- `S18.docker-lifecycle`: `pause()`/`resume()` run `docker pause`/`docker unpause` (resume waits for health again) and raise without a container; `cleanup()` (on exit) stops and removes the container and deletes the image only with `cleanup_image=True`. Unit: `tests/workspace/test_workspace_pause_resume.py` (`test_docker_workspace_*`), `tests/workspace/test_docker_workspace.py` (`test_cleanup_without_image_deletion`, `test_cleanup_with_image_deletion`, `test_docker_workspace_resume_uses_health_check_timeout`). Example: `examples/02_remote_agent_server/02_convo_with_docker_sandboxed_server.py`. Real-LLM: none.
- `S18.api-remote-start`: `APIRemoteWorkspace(runtime_api_url=..., runtime_api_key=..., server_image=..., forward_env=[...], resource_factor=..., runtime_class=..., api_timeout=..., startup_wait_timeout=...)` starts a runtime with the forwarded variables (logging only their names), waits until it is alive and points the client at it. Unit: `tests/workspace/test_api_remote_workspace.py`. Example: `examples/02_remote_agent_server/04_convo_with_api_sandboxed_server.py`. Real-LLM: none.
- `S18.api-remote-lifecycle`: `pause()`/`resume()` call the runtime API and raise without a runtime; `cleanup()` stops the runtime, pauses it with `pause_on_close=True`, or leaves it with `keep_alive=True`; a `session_id` with a live runtime is reattached instead of started. Unit: `tests/workspace/test_workspace_pause_resume.py` (`test_api_workspace_*`); **untested**: `keep_alive`, `pause_on_close` and reattach by `session_id`. Example: none. Real-LLM: none.
- `S18.cloud-sandbox`: `OpenHandsCloudWorkspace(cloud_api_url=..., cloud_api_key=..., sandbox_spec_id=..., sandbox_id=None, keep_alive=False)` creates a sandbox (or resumes `sandbox_id`), waits until it is ready, finds the agent-server URL, and uses Bearer auth; `cleanup()` deletes the sandbox unless `keep_alive`; `resume()` resumes it and `pause()` raises `NotImplementedError`. Unit: `tests/workspace/test_cloud_workspace.py`, `tests/workspace/test_workspace_pause_resume.py` (`test_cloud_workspace_*`). Example: `examples/02_remote_agent_server/07_convo_with_cloud_workspace.py`. Real-LLM: none.
- `S18.cloud-credentials`: on a cloud workspace `get_llm()` reads the account's LLM settings (overridable), `get_secrets()` builds `LookupSecret`s from secret names, and `get_mcp_config()` converts SSE, SHTTP and stdio servers; server errors are retried, client errors are not. Unit: `tests/workspace/test_cloud_workspace_sdk_settings.py`. Example: `examples/02_remote_agent_server/10_cloud_workspace_share_credentials.py`. Real-LLM: none.
- `S18.cloud-local-mode`: with `local_agent_server_mode=True` (inside a sandbox) the workspace skips sandbox creation, reads `AGENT_SERVER_PORT`, `SANDBOX_ID` and `SESSION_API_KEY`/`OH_SESSION_API_KEYS_0` from the environment, keeps cloud credentials available, and never deletes the sandbox. Unit: `tests/workspace/test_cloud_workspace.py` (`test_local_agent_server_mode_*`). Example: none. Real-LLM: none.
- `S18.cloud-automation-tags`: `default_conversation_tags` derives automation tags from `AUTOMATION_*` variables and the trigger payload and merges them with user tags. Unit: `tests/workspace/test_cloud_workspace_automation_tags.py`. Example: none. Real-LLM: none.
- `S18.apptainer`: `ApptainerWorkspace(server_image=... or sif_file=..., host_port=..., cache_dir=..., extra_bind_mounts=[...], enable_gpu=..., use_fakeroot=True, health_check_timeout=...)` builds or reuses a SIF, starts an instance with binds (plus `OPENHANDS_APPTAINER_EXTRA_BINDS`) and `--nv` for GPU, waits for health, and stops the instance on cleanup. Unit: `tests/workspace/test_apptainer_workspace.py`, `tests/workspace/test_docker_workspace.py::test_apptainer_workspace_startup_uses_health_check_timeout`. Example: `examples/02_remote_agent_server/08_convo_with_apptainer_sandboxed_server.py`. Real-LLM: none.
- `S18.agent-sandbox`: `AgentSandboxWorkspace(warmpool=..., namespace=..., connection="port_forward"|"direct", host=..., kube_context=..., shutdown_after_seconds=...)` claims a sandbox from the warm pool, port-forwards (retrying) or connects directly (needs `host`), waits for health, pauses and resumes by flipping `operatingMode` (resume reconnects on a fresh port), and terminates the claim on cleanup or a failed connect. Unit: `tests/workspace/test_agent_sandbox_workspace.py`. Example: `examples/02_remote_agent_server/17_convo_with_agent_sandbox_server.py`. Real-LLM: none.

## How to get to it (agent POV)

- `from openhands.sdk.workspace import Workspace, LocalWorkspace, RemoteWorkspace, BaseWorkspace, CommandResult, FileOperationResult`; `from openhands.sdk.workspace.remote.async_remote_workspace import AsyncRemoteWorkspace`.
- `from openhands.workspace import DockerWorkspace, DockerDevWorkspace, APIRemoteWorkspace, OpenHandsCloudWorkspace, ApptainerWorkspace, AgentSandboxWorkspace, RepoSource, RepoMapping` (`AgentSandboxWorkspace` needs `openhands-workspace[agent-sandbox]`).
- Pattern: `with DockerWorkspace(server_image=..., platform=...) as workspace: conversation = Conversation(agent=agent, workspace=workspace); ...` gives a `RemoteConversation`; `workspace.execute_command(...)`, `file_upload`, `file_download`, `git_changes`, `git_diff`, `pause()`, `resume()`, `alive`, `get_llm()`, `get_secrets()`, `get_mcp_config()`, `clone_repos()` are the direct calls.
- Environment read by backends: `SESSION_API_KEY` / `OH_SESSION_API_KEYS_0` (Docker, Apptainer, cloud local mode), `OPENHANDS_APPTAINER_EXTRA_BINDS`, `AGENT_SERVER_PORT`, `SANDBOX_ID`, `AUTOMATION_CALLBACK_URL`, `AUTOMATION_CALLBACK_API_KEY`, `AUTOMATION_RUN_ID`; `forward_env` names extra variables to copy into the container (default `DEBUG`, `SESSION_API_KEY`, `OH_SESSION_API_KEYS_0`).
- Example environment: `SDK_SHA`/`GITHUB_SHA` (image tag), `RUNTIME_API_URL`, `RUNTIME_API_KEY` (example 04), `OPENHANDS_CLOUD_API_KEY`, `OPENHANDS_CLOUD_API_URL` (examples 07, 10), `AGENT_SANDBOX_WARMPOOL`, `AGENT_SANDBOX_NAMESPACE` (example 17), plus `LLM_API_KEY`, `LLM_MODEL`, `LLM_BASE_URL`.
- The agent server side of these workspaces (routes, conversation runtime) is in the agent-server feature map.

## Driving it

Preconditions:
- `uv sync --dev` has run; `control-sdk` is on `PATH` (`export PATH="$PWD/.agents/skills/verify-sdk/scripts:$PATH"`). `sdk-map run` does both.
- `tmux` and `git` on `PATH`. The remote bullets start the repo's own agent server (`python -m openhands.agent_server`) as a subprocess on a free `127.0.0.1` port inside the run, stop it at the end, and need nothing else: that is the server `DockerWorkspace` runs in a container, without the container.
- An agent running inside that server cannot use the scripted `TestLLM` (it does not survive serialization to the server), so those programs point `LLM(base_url=...)` at a local OpenAI-compatible stand-in (`programs/S18/servers.py`, `FakeLLMServer`) that answers with the same script steps. Callback URLs and the Cloud API are local capture servers from the same file.
- Live bullets: `DEEPSEEK_API_KEY` exported. Backend bullets: the named runtime (Docker daemon, `apptainer`, a Kubernetes cluster, Cloud or runtime-API keys); none is available here.

- **Local workspace (`S18.workspace-factory`, `S18.local-ops`, `S18.local-pause-resume`, `S18.completion-callback`, `S18.conversation-binding`).** `Workspace(working_dir=str|Path)` builds a `LocalWorkspace`; commands, copies and git state come back as `CommandResult`, `FileOperationResult`, `GitChange`/`GitDiff`; a scripted agent runs in it under `AUTOMATION_CALLBACK_URL`, and the context exit POSTs the outcome.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S18/local_workspace \
    --check factory_type eq LocalWorkspace --check factory_path_type eq LocalWorkspace --check working_dir_is_str eq true \
    --check exit_code eq 0 --check stdout_lines.0 eq hi --check pwd_is_root eq true \
    --check failing_exit eq 3 --check failing_stderr eq oops --check cwd_override eq /tmp --check timeout_occurred eq true \
    --check upload_ok eq true --check upload_size eq 8 --check upload_created_parents eq true \
    --check download_ok eq true --check download_content eq payload \
    --check missing_download_success eq false --check missing_download_error eq true \
    --check git_changes contains ADDED:new.txt --check git_changes contains UPDATED:f.txt \
    --check git_diff_original eq one --check git_diff_modified matches 'one\ntwo' \
    --check pause_resume_error missing --check conversation_type eq LocalConversation --check agent_file eq from-agent \
    --check callback_posts eq 1 --check callback_status eq COMPLETED --check callback_run_id eq run-42 \
    --check callback_has_cost eq true --check callback_auth eq "Bearer cb-token" \
    --check failed_status eq FAILED --check failed_error_code eq RuntimeError \
    --check posts_after_unset eq 0 --check unreachable_error missing
  ```
  An exception inside the workspace context posts `FAILED` with a classified `error`; without the URL nothing is posted; an unreachable URL is logged, not raised.
- **Remote workspace against a local agent server (`S18.remote-client`, `S18.remote-command-exec`, `S18.remote-files-git`, `S18.remote-alive`, `S18.async-remote`).** The server requires a session key; the program uses the workspace's direct calls, then `AsyncRemoteWorkspace`, then stops the server.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S18/remote_workspace --arg ops \
    --check type eq RemoteWorkspace --check host_normalized eq true --check client_lazy eq true \
    --check client_base_url eq true --check sends_session_key eq true --check reset_client_cleared eq true \
    --check wrong_key.exit eq -1 --check wrong_key.stderr contains 401 \
    --check exec.exit eq 0 --check exec.lines.0 eq hi --check exec_pwd_ok eq true \
    --check failing_exit eq 4 --check failing_output eq bad \
    --check long_lines eq 40 --check long_unique_in_order eq true --check concurrent_isolated eq true \
    --check timeout_occurred eq true \
    --check upload_ok eq true --check upload_bytes_ok eq true --check download_ok eq true \
    --check missing_download_success eq false \
    --check git_changes contains ADDED:new.txt --check git_changes contains UPDATED:f.txt \
    --check git_diff_modified matches 'one\ntwo' \
    --check alive eq true --check alive_wrong_port eq false --check alive_after_stop eq false \
    --check async.exec eq async-hi --check async.exit eq 0 --check async.upload eq true \
    --check async.download eq true --check async.git_changes eq 2 --check async.alive eq true
  ```
  A wrong session key does not raise: `execute_command` returns `exit_code` -1 with the HTTP 401 in `stderr` (errors of the command protocol are folded into the result). Forty lines printed over about two seconds arrive once and in order across polls; two commands run from two threads keep their own output.
- **Workspace factory with a `Path`, remote (`S18.workspace-factory`).** The same factory call with a `pathlib.Path` working directory and a host.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S18/remote_workspace --arg ops --check path_working_dir_error missing --check type eq RemoteWorkspace
  ```
  Known failure (reproduced 2026-10-13, not yet filed): `Workspace(host=..., working_dir=Path(...))` (and `RemoteWorkspace(working_dir=Path(...))`) raises `pydantic_core.ValidationError: 1 validation error for RemoteWorkspace working_dir Input should be a valid string [type=string_type, input_type=PosixPath]`. `RemoteWorkspaceMixin` redeclares `working_dir: str` without the `BeforeValidator` that `BaseWorkspace` uses to accept a `Path`; the local branch accepts one.
- **Settings from the server (`S18.remote-settings`).** LLM settings, an LLM profile, two secrets and an MCP server are stored through the server's settings API; the workspace reads them back.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S18/remote_workspace --arg settings \
    --check patch_status eq 200 --check patch_mcp_status eq 200 \
    --check get_llm.model eq openai/settings-model --check get_llm.base_url eq "http://127.0.0.1:9/v1" --check get_llm.key_ok eq true \
    --check get_llm_override.model eq openai/override --check get_llm_override.temperature eq 0.3 \
    --check get_llm_profile.model eq openai/fast-model --check get_llm_profile.usage_id eq profile:fast \
    --check get_llm_active_profile eq openai/fast-model --check missing_profile_error eq FileNotFoundError \
    --check secrets contains PROJECT_TOKEN --check secret_type eq LookupSecret --check secret_url_ok eq true \
    --check secret_has_session_header eq true --check secret_resolves eq true --check secrets_filtered len-eq 1 \
    --check mcp_servers contains fetch --check mcp_fetch_command eq uvx \
    --check secret_value_missing missing --check secret_value_present eq tok-1
  ```
  Without an active profile `get_llm()` returns the legacy `agent_settings.llm`; after activating `fast` it returns that profile. A `LookupSecret` carries the session header and resolves against the server.
- **A conversation in the remote workspace, runtime scope and callback (`S18.conversation-binding`, `S18.runtime-scope`, `S18.completion-callback`).** `Conversation(workspace=RemoteWorkspace)` runs a scripted agent inside the server (its LLM is the local scripted endpoint); a second workspace scoped to that conversation runs commands and file calls in its runtime; the workspace context posts the callback with the conversation id.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S18/remote_workspace --arg conversation \
    --check conversation_type eq RemoteConversation --check status eq finished \
    --check actions eq 2 --check observations eq 2 --check callback_events gt 0 \
    --check agent_file eq remote-agent --check registered_conversation eq true \
    --check scoped_prefix eq true --check scoped_exec.0 eq remote-agent \
    --check scoped_upload eq true --check scoped_outside_upload eq false \
    --check unscoped_release_error contains "conversation scope" \
    --check unknown_scope.exit eq -1 --check unknown_scope.stderr contains 404 \
    --check runtime_key_error eq HTTPStatusError \
    --check callback.posts eq 1 --check callback.status eq COMPLETED \
    --check callback.conversation_id_ok eq true --check callback.run_id eq remote-run-1 --check callback.has_cost eq true
  ```
  A scoped upload outside the conversation's workspace is refused (422). The plain local server has no `/runtime/credentials` route (that is the Docker-runtime server's), so `get_runtime_session_key()` raises `HTTPStatusError` here; issuing and releasing a runtime credential needs the server in Docker-runtime mode (blocked, needs `docker`).
- **Repositories and skills (`S18.repos-and-skills`).** Three local `file://` repositories (a string, a dict with `ref`, a `RepoSource`) are cloned, rendered for the prompt, and their `.openhands/skills` are loaded through the server's `/api/skills`.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S18/remote_workspace --arg repos \
    --check clone.success_count eq 3 --check clone.failed len-eq 0 \
    --check clone.dirs contains alpha --check clone.dirs contains beta --check clone.dirs contains gamma \
    --check cloned_files_exist eq true --check short_url_without_provider eq rejected \
    --check short_url_with_provider eq github --check context_mentions len-eq 3 --check empty_context eq "" \
    --check skills contains alpha-notes --check skills contains beta-notes \
    --check skills len-eq 3 --check context_skills len-eq 3 --check context_load_public eq false \
    --check fallback_skills eq 0 --check fallback_load_public eq true
  ```
  Cloning provider repositories with tokens (`owner/repo` plus a provider) needs network and a provider token; the token lookup itself (`_get_secret_value`) is proven in the settings bullet.
- **Cloud workspace in local agent-server mode (`S18.cloud-local-mode`, `S18.cloud-credentials`, `S18.cloud-automation-tags`).** As an automation inside a Cloud sandbox: `AGENT_SERVER_PORT`, `SANDBOX_ID`, `SESSION_API_KEY` and `AUTOMATION_*` are set, the agent server is local, and the Cloud API is a local stand-in whose first account read fails with a 500.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S18/cloud_local \
    --check host eq true --check session_key_from_env eq true --check exec.0 eq in-sandbox --check alive eq true \
    --check get_llm.model eq openai/cloud-model --check get_llm.base_url_ok eq true --check get_llm.requests eq 2 \
    --check get_llm_override eq openai/other --check get_llm_profile eq openai/fast-model \
    --check missing_profile eq FileNotFoundError \
    --check secrets contains GITHUB_TOKEN --check secret_url_ok eq true --check secret_session_header eq true \
    --check secrets_filtered len-eq 1 \
    --check mcp.fs eq stdio --check mcp.sse_0 eq sse --check mcp.shttp_0 eq streamable-http \
    --check forbidden_error eq 403 --check forbidden_requests eq 1 \
    --check default_tags.automationtrigger eq cron --check default_tags.automationid eq a1 \
    --check default_tags.automationname eq Nightly --check default_tags.automationrunid eq run-7 \
    --check conversation_tags.team eq infra --check conversation_tags.automationrunid eq run-7 \
    --check conversation_status eq finished --check agent_file eq cloud \
    --check callback.posts eq 1 --check callback.status eq COMPLETED --check callback.run_id eq run-7 \
    --check callback.conversation_id_ok eq true --check callback.auth eq "Bearer cloud-key" \
    --check sandbox_creates eq 0 --check sandbox_deletes eq 0
  ```
  The 500 is retried (two account reads), the 403 is not (one). The conversation's tags merge the automation tags with the user's; the workspace never creates or deletes a sandbox in this mode.
- **Live: a real model in a local agent server (`S18.conversation-binding`, `S18.remote-command-exec`, `S18.remote-settings`).** Examples 01 (remote conversation with hooks and a direct `execute_command`) and 13 (`get_llm()` from the server's settings, then a conversation).
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --llm deepseek-flash --field run)
  control-sdk exec --model-env --expect-output EXAMPLE_COST -- python examples/02_remote_agent_server/01_convo_with_local_agent_server.py
  control-sdk exec --model-env --expect-output EXAMPLE_COST -- python examples/02_remote_agent_server/13_workspace_get_llm.py
  ```
- **Docker sandbox (`S18.docker-start`, `S18.docker-dev-build`, `S18.docker-lifecycle`), blocked: needs a running Docker daemon and network to pull `ghcr.io/openhands/agent-server`.** Example 02 starts the container, runs `execute_command` and a conversation, and removes the container on exit (`docker ps -a` is clean afterwards).
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --llm deepseek-flash --field run)
  control-sdk exec --model-env --expect-output EXAMPLE_COST -- python examples/02_remote_agent_server/02_convo_with_docker_sandboxed_server.py
  ```
- **Runtime API and Cloud sandboxes (`S18.api-remote-start`, `S18.api-remote-lifecycle`, `S18.cloud-sandbox`, `S18.cloud-credentials`), blocked: need `RUNTIME_API_URL`/`RUNTIME_API_KEY` or `OPENHANDS_CLOUD_API_KEY` and network.**
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --llm deepseek-flash --field run)
  control-sdk exec --model-env -- python examples/02_remote_agent_server/04_convo_with_api_sandboxed_server.py
  control-sdk exec --model-env -- python examples/02_remote_agent_server/07_convo_with_cloud_workspace.py
  control-sdk exec --model-env -- python examples/02_remote_agent_server/10_cloud_workspace_share_credentials.py
  ```
- **Apptainer and agent-sandbox (`S18.apptainer`, `S18.agent-sandbox`), blocked: need `apptainer`, or a Kubernetes cluster with the agent-sandbox controller and `openhands-workspace[agent-sandbox]`.**
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --llm deepseek-flash --field run)
  control-sdk exec --model-env -- python examples/02_remote_agent_server/08_convo_with_apptainer_sandboxed_server.py
  control-sdk exec --model-env -- python examples/02_remote_agent_server/17_convo_with_agent_sandbox_server.py
  ```

## Gotchas

- `DockerWorkspace` and `ApptainerWorkspace` default to `platform="linux/amd64"`; the examples compute the platform and pick the arm64 image themselves. On an arm host without that, the amd64 image runs under emulation or fails.
- The examples pick `ghcr.io/openhands/agent-server:<SDK_SHA[:7]>-python-<arch>` when `SDK_SHA` or `GITHUB_SHA` is set, else `latest-python`; a stale `GITHUB_SHA` in your shell points at an image that may not exist.
- `examples/02_remote_agent_server/17_convo_with_agent_sandbox_server.py` (needs a cluster and the `agent-sandbox` extra) and `05_vscode_with_docker_sandboxed_server.py` (interactive `input()`) are in `_EXCLUDED_EXAMPLES` of `tests/examples/test_examples.py`.
- Example 07's docstring tells you to run `06_convo_with_cloud_workspace.py`; the file is `07_convo_with_cloud_workspace.py`. Its LLM must be reachable from the cloud sandbox (not a local proxy).
- `OpenHandsCloudWorkspace.pause()` raises `NotImplementedError`; only `resume()` works on the cloud backend.
- Backends clean up in `__exit__` and `__del__`; a process killed hard can leave containers, runtimes or sandboxes running. `keep_alive=True` (API and cloud) leaves them on purpose.
- Docker readiness waits up to `health_check_timeout` (120 s by default) and must be positive; the first image pull can take longer than that on a slow link, so pull the image first.
- Nothing in the repository's test suite starts a real container, runtime or sandbox; every `tests/workspace` test mocks `docker`, `apptainer`, Kubernetes and HTTP, so the examples are the only end-to-end proof.
