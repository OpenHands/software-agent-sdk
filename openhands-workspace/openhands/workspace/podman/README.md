# Podman Workspace

`PodmanWorkspace` runs the pre-built agent server image in a rootless
[Podman](https://podman.io/) container and talks to it over the agent server's HTTP
API, like `DockerWorkspace`. Podman needs no daemon, no root, and no socket.

Use it on Linux hosts that have Podman instead of Docker. Pointing `DockerWorkspace` at
Podman through a `docker` shim starts the container, but under rootless Podman the
agent cannot write to mounted directories. `PodmanWorkspace` sets the user namespace
mapping that makes mounted directories writable.

## Prerequisites

- Linux. Podman on macOS and Windows runs containers in a VM and is not supported.
- Podman 4.3 or later (for `--userns keep-id:uid=...`), set up for rootless use.
- Subordinate UID and GID ranges for your user in `/etc/subuid` and `/etc/subgid`.
  Most distributions create them when the user is added. Check with:

  ```bash
  grep "^$(id -un):" /etc/subuid /etc/subgid
  podman info --format '{{.Host.Security.Rootless}}'   # prints true
  ```

  If the ranges are missing, add them (as root) and migrate Podman's storage:

  ```bash
  sudo usermod --add-subuids 100000-165535 --add-subgids 100000-165535 "$USER"
  podman system migrate
  ```

## Usage

```python
from openhands.workspace import PodmanWorkspace

with PodmanWorkspace(
    volumes=["/path/to/project:/workspace/project"],
    working_dir="/workspace/project",
) as workspace:
    result = workspace.execute_command("ls -la")
    print(result.stdout)
```

The agent server keeps its own state (`conversations/`, `bash_events/`) under
`/workspace`, so mount projects below it, for example at `/workspace/project`.

The container is removed when the `with` block exits, when `cleanup()` is called, and
when startup fails. If `podman rm` fails, `cleanup()` raises and the next call retries.

### File ownership

The agent server image runs as `openhands` (uid 10001). By default the workspace
passes `--userns keep-id:uid=10001,gid=10001`, which maps your user to that uid, so
files the agent writes to volumes are owned by you on the host. Container root maps to
one of your subordinate UIDs.

For an image whose user has a different uid, set `userns` to match, for example
`userns="keep-id:uid=1000,gid=1000"`. `userns=None` uses Podman's default mapping, in
which container root is your user.

The first start with `keep-id` can take up to a minute while Podman makes an ID-mapped
copy of the image. Later starts reuse it and take a few seconds.

### SELinux

On SELinux hosts (Fedora, RHEL, CentOS Stream), the container cannot read volumes until
they are labeled for container use. Append `:Z` to the volume to relabel the host
directory for this container:

```python
volumes=["/path/to/project:/workspace/project:Z"]
```

`:Z` changes the label of the host directory and everything in it. Only use it on
directories that belong to the project, not on your home directory or system
directories. `:z` applies a label that several containers can share.

### GPUs

`enable_gpu=True` passes all NVIDIA GPUs with `--device nvidia.com/gpu=all`. This uses
the Container Device Interface (CDI). Install the NVIDIA Container Toolkit and generate
the CDI specification first:

```bash
sudo nvidia-ctk cdi generate --output=/etc/cdi/nvidia.yaml
nvidia-ctk cdi list   # lists nvidia.com/gpu=all
```

### VSCode

`extra_ports=True` also publishes the agent server's VSCode port on `host_port + 1`.

## Configuration Options

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `server_image` | `str` | `ghcr.io/openhands/agent-server:latest-python` | Pre-built agent server image. Pulled if not present locally. |
| `host_port` | `int \| None` | `None` | Loopback port for the agent server (auto-assigned if None) |
| `volumes` | `list[str]` | `[]` | `podman run --volume` specs. Host paths must exist and be readable. |
| `working_dir` | `str` | `/workspace` | Working directory inside the container |
| `forward_env` | `list[str]` | `["DEBUG", "SESSION_API_KEY", "OH_SESSION_API_KEYS_0"]` | Host environment variables forwarded to the container |
| `userns` | `str \| None` | `keep-id:uid=10001,gid=10001` | `podman run --userns` value |
| `platform` | `PlatformType` | `"linux/amd64"` | Platform of the image to pull and run |
| `extra_ports` | `bool` | `False` | Also publish the VSCode port on `host_port + 1` |
| `enable_gpu` | `bool` | `False` | Pass all NVIDIA GPUs with CDI |
| `detach_logs` | `bool` | `True` | Stream container logs to stdout in the background |
| `health_check_timeout` | `float` | `120.0` | Seconds to wait for the agent server to become healthy |

Forwarded variables are passed as `--env NAME`, and Podman reads the values from its
own environment, so session API keys do not appear on the `podman` command line.

## What the container isolates

The agent server runs in its own user, mount, PID, and network namespaces.

- Only the host paths listed in `volumes` are visible in the container.
- Processes in the container cannot see or signal host processes.
- The agent server's port is published on `127.0.0.1` only, and requires the session
  API key if `OH_SESSION_API_KEYS_0` or `SESSION_API_KEY` is set.
- Services bound to the host's loopback interface are not reachable from the container.
- Container root has no privileges on the host.

It does not isolate:

- The kernel. Containers share the host kernel, so a kernel vulnerability can lead to
  an escape. For a stronger boundary, use a VM-based sandbox.
- Volumes. The agent can read, change, and delete anything in a mounted directory, as
  your user.
- Outbound network access. The container can reach the internet and services bound to
  the host's non-loopback addresses (as `host.containers.internal`).
- Forwarded environment variables, which the agent can read.

## Troubleshooting

### Podman not found
```
RuntimeError: Podman is not installed. Install the 'podman' package from your distribution.
```
**Solution**: Install Podman, for example `sudo apt-get install podman` or
`sudo dnf install podman`.

### Missing subordinate ID ranges
Podman reports "cannot find mappings for user" or "insufficient UIDs or GIDs available
in user namespace" when it pulls or runs the image.

**Solution**: Add subordinate UID and GID ranges as described in
[Prerequisites](#prerequisites).

### Permission denied in a mounted directory
**Solution**: On SELinux hosts, append `:Z` to the volume. With a custom image, set
`userns` to match the image's user.

### Volume source does not exist
```
pydantic_core._pydantic_core.ValidationError: 1 validation error for PodmanWorkspace
  Value error, Volume source does not exist: /path/to/project
```
**Solution**: Create the directory or fix the path. Sources that start with `/` or `.`
are host paths; other sources are named Podman volumes.

## Complete Example

See `examples/02_remote_agent_server/18_convo_with_podman_sandboxed_server.py`. It
mounts a project directory, runs a conversation that writes a file into it, and checks
that the file is owned by the host user.

```bash
podman --version
export LLM_API_KEY=...
uv run python examples/02_remote_agent_server/18_convo_with_podman_sandboxed_server.py
```
