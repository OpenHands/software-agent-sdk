# Local Smol workspace

`SmolWorkspace` owns one ephemeral local microVM running Agent Server. Each
workspace has its own VM, filesystem and authentication keys. Conversations
using the same workspace share that VM and filesystem.

## Validation status

This contribution requires Smol's proposed `Machine.publish_atomic` API and an
engine that retains its allocated loopback listener. The published
`smolmachines==1.32.4` wheel lacks this API. Construction reports the missing
capability before allocating a VM. A compatible published wheel and a successful
Agent Server image integration run are required before enabling this provider
in a release. See `.pr/smol-validation.md` for the contribution's test results.

| Host | Prerequisites | OpenHands live validation |
| --- | --- | --- |
| Apple Silicon macOS | macOS 11+, Hypervisor.framework | Local build passes; published wheel pending |
| Linux x86_64 | KVM access and a compatible Smol wheel | Pending |
| Linux arm64 | KVM access and a compatible Smol wheel | Pending |

Smol's `local_availability()` checks the installed runtime before allocation.
The provider always selects `ConnectOptions(target="local")`, including when
cloud credentials are present. Intel Macs and Windows are outside this provider's
initial host targets. On Linux, check access to `/dev/kvm`; your login may need
membership in the `kvm` group.

## Installation and a command

```bash
pip install 'openhands-workspace[smol]'
export OPENHANDS_SERVER_IMAGE='your-registry/agent-server@sha256:...'
```

Use a published Agent Server image for the guest architecture. Supply a tag or
digest explicitly; a digest makes repeated runs reproducible. The image must
contain the server and its execution tools, bind `0.0.0.0:8000`, and give its
configured user write access to `/workspace` (or your `working_dir`).
Smol retrieves the OCI image. Startup preserves the image's user and entrypoint.
An explicit `server_command=[...]` replaces the entrypoint/CMD as argv.

```python
import os
from openhands.workspace import SmolWorkspace

with SmolWorkspace(
    server_image=os.environ["OPENHANDS_SERVER_IMAGE"],
    network=False,
) as workspace:
    result = workspace.execute_command("printf 'hello from the VM\\n'")
    print(result.stdout)
```

The complete conversation example is [main.py](main.py). It uses the existing
OpenHands conversation and tool interfaces. Run it manually on a supported host;
it is excluded from the example workflow because it requires local virtualization.

## Resources and networking

Defaults are two CPUs, 2048 MiB RAM and a 20 GiB storage disk. `cpus`, `memory_mb`
and `storage_gb` accept positive integers. Smol also creates its root overlay
(10 GiB by default). Disks use sparse allocation, so configured capacity differs
from physical host usage. Leave space for the image cache, downloaded layers,
overlay and files written during execution. The Agent Server images can require
several gigabytes beyond their compressed download size.

`network=True` permits guest outbound access, including model requests and
package downloads. `network=False` keeps the guest's outbound network disabled
while retaining local control connections. Host-side OCI retrieval still needs
registry access on a cold cache. The allocated Agent Server listener binds
loopback. The generated session key authenticates HTTP and WebSocket clients.

## Files and credentials

Use `file_upload` and `file_download` to transfer data. The workspace uses private
guest storage. Host directories, Docker sockets, SSH agents and home directories
are never mounted by this provider.

Model credentials go through the conversation's existing LLM configuration.
`server_env` explicitly supplies server environment variables; host environment
variables are not forwarded into the guest. Authentication keys and the local
runtime settings are reserved. Each VM receives separate random session and
encryption keys. `server_env`, generated keys, the endpoint and runtime handles
are excluded from model dumps and representations. Save explicit secrets
separately if you need to reconstruct the workspace later.

```python
settings = workspace.model_dump(mode="json")
# Later, with SmolWorkspace imported so its discriminator is registered:
fresh_workspace = SmolWorkspace.model_validate(settings)
try:
    print(fresh_workspace.machine_id, fresh_workspace.endpoint)
finally:
    fresh_workspace.close()
```

Reconstruction starts a fresh VM with fresh keys. It creates no attachment to a
saved machine. Pause and resume raise `NotImplementedError`. Persistence, host
mounts, checkpoints, GPU access and cloud execution are outside this provider's
initial interface.

## Shutdown and troubleshooting

Use the context manager or call `close()`. Closing stops the VM and deletes its
private storage; cached images remain. Repeated close calls are safe. Context
exit runs the inherited completion hook while the server is reachable and then
cleans up, including when the hook raises.

`startup_timeout` defaults to 120 seconds and includes native startup and
application readiness. A helper process owns the native runtime so a blocked
native call cannot hold the caller indefinitely. The readiness checks cover
`/health` and an authenticated command in the requested working directory.
Allow a larger startup budget for a cold image on a slow connection.
`cleanup_timeout` defaults to 30 seconds. Interruptions and constructor failures
also run bounded cleanup. Abrupt interpreter or host termination can require
manual recovery.

Startup errors identify availability, image/VM startup, port publication or
application readiness. Check the image's architecture, entrypoint, port and
working-directory permissions when startup fails. Startup never builds an
image, installs packages or invokes Docker. Native and guest exception text is
withheld because it can contain environment values or registry credentials.

If cleanup fails, the error includes the exact owned machine identifier and a
recovery instruction. After fixing the host/runtime problem, delete that machine:

```python
import smol
smol.Machine.delete_by_name(
    "the-exact-identifier-in-the-error",
    smol.ConnectOptions(target="local"),
)
```

Use that exact identifier; avoid deleting machines by a shared prefix. The
`machine_id`, `endpoint` and `resolved_image_digest` properties help diagnose a
workspace without revealing authentication keys.

## Tests

Default workspace tests use a fake Smol client and require no virtualization.
Live tests need a compatible published wheel and image:

```bash
uv sync --dev --package openhands-workspace --extra smol
uv run pytest tests/workspace/test_smol_workspace.py
OPENHANDS_SMOL_SERVER_IMAGE='image@sha256:...' \
  uv run pytest -m smol_live tests/workspace/test_smol_workspace_live.py
```

The live model fixture runs on guest loopback with outbound networking disabled.
It uses scripted `TestLLM` responses and performs no paid model inference.
