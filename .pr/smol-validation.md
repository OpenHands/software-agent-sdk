# SmolWorkspace validation and merge prerequisites

OpenHands branch: `feat/smol-workspace`.
Base: `0f8cdf6902619d71a89e88cbf3810cb34e96b875`.
Companion commits: smolvm engine `525bed3`; Smol Python SDK `db5fdaa`.
Host: Apple Silicon macOS, Python 3.13.12. Date: November 8, 2026.

The provider and companion Smol changes are implemented locally. Keep the
OpenHands PR in draft until a compatible Smol wheel is published, clears the
repository's dependency age policy, and passes the live suite. Set the final
minimum dependency to that tested published version. Linux validation is pending;
the guide makes no completed Linux integration claim.

## Results

| Check | Result |
| --- | --- |
| Default provider, other workspace and SDK workspace tests | 450 passed; five Smol live tests deselected |
| Ruff, Pycodestyle, Pyright, import rules and tool registration on changed files | Passed |
| Base installation with Smol uninstalled | Import succeeded; selecting the provider returned the installation command |
| Published `smolmachines==1.32.4` | Availability succeeded; atomic publication API unavailable; provider rejects before allocation |
| Patched local Smol build, published OpenHands ARM64 image | Five live tests passed |
| Smol network crate | 183 tests passed |
| Smol filtered engine tests | Four passed, including real forwarding through allocated listeners |
| Smol Python contracts and opt-in VM test | Ten passed |
| Existing Smol Python cloud/async mock scripts | 127 and 11 checks passed |

The default tests ran with Smol uninstalled. They exercise a fake SDK in the
owned helper process and require no local virtualization.

The live OpenHands tests exercised:

- Two simultaneous VMs with distinct keys, ports and filesystems; real listeners
  bound only to loopback; unauthenticated commands rejected.
- Image user `openhands`, preserved default entrypoint, local conversation runtime,
  explicit environment delivery and exclusion of a test host environment value.
  An ambient test cloud token still selected local execution.
- Command cwd, stdout, stderr, exit status and timeout; binary/Unicode upload and
  download; Git changes and original/modified file content.
- A remote conversation receiving WebSocket action/observation events and
  executing a terminal tool. `TestLLM` generates its scripted responses; a small
  stdlib HTTP fixture serves them on guest loopback. Outbound networking is
  disabled and the model API key is the unused test value. No paid inference ran.
- Normal and double close, interrupted startup, parent-exit failure and invalid
  image creation before a handle is returned. Exact owned identifiers were absent
  from the local machine list after cleanup.
- A host PATH without Docker, both common Docker sockets absent, and no `dockerd`
  or `com.docker.backend` process detected.

Unit cases also cover constructor and cleanup errors together, cleanup retry,
startup cancellation of helper descendants, secret exclusion from repr/dumps,
discriminated reconstruction with fresh VM/key/port state, unsupported lifecycle
operations, unauthenticated images and images reporting the Docker runtime.

## Exact image and runtime

Published OpenHands image:

```text
ghcr.io/openhands/agent-server@sha256:89f694c3a3ec77efeaae7ab4c72352e7a5f2fd606544e6a7123d87be1d467665
```

This is the Linux ARM64 manifest from `ghcr.io/openhands/agent-server:1.60.1-python`.
Its image config declares user `openhands` and entrypoint
`["tini", "--", "/usr/local/bin/openhands-agent-server"]`. Startup preserved these
settings. `/server_info` reported Agent Server 1.60.1; the host SDK is 1.60.1.
Resources were two CPUs, 2048 MiB RAM and 20 GiB storage, with outbound networking
disabled. Smol retains its separate default root overlay.

Local Smol validation combines:

- Engine checkout `smol-machines/smolvm`, base
  `b64a23992668620818eaa4237e18ad15a8a3c8a1`, with the atomic-port patch.
- Python SDK checkout `smol-machines/smol`, base
  `b28b947188cb639ed2b6b9b13db660faaeb1230e`, with its native/Python API patch.
- A locally compiled native extension and boot helper; the helper was signed with
  the repository's hypervisor entitlements.
- Libraries and agent rootfs from the published `smolmachines==1.32.4` wheel.
- Local Python metadata `1.33.0+atomic.local`, identifying this mixed local build.

This combination is development evidence. It is not a published compatible Smol
release. The current extra range is provisional; finalize its lower bound and
lock against the compatible published wheel before merge. The existing root uv
seven-day guard remains unchanged. Only Smol's lock entry and workspace extra
metadata were added; no other third-party versions changed.

The published 1.32.4 image boot probe failed with an I/O missing-file error. The
image has repeated empty OCI layers; the current engine source contains a
repeated-layer archive fix. The clean local build completed its cold download
and image seed, then booted the image. The default 120-second provider budget
correctly stopped an incomplete cold import. Allow a larger `startup_timeout`
for a cold image on a slow connection; subsequent live tests used the warm cache.

## Reproduction

Default tests and checks:

```bash
uv sync --dev
uv run pytest tests/workspace tests/sdk/workspace -q
uv run pre-commit run --files <changed files>
```

Published-wheel acceptance, after the prerequisite release:

```bash
uv sync --dev --package openhands-workspace --extra smol
OPENHANDS_SMOL_SERVER_IMAGE='ghcr.io/openhands/agent-server@sha256:89f694c3a3ec77efeaae7ab4c72352e7a5f2fd606544e6a7123d87be1d467665' \
  uv run pytest -m smol_live tests/workspace/test_smol_workspace_live.py -q
```

For a custom image, set `OPENHANDS_SMOL_IMAGE_USER` to its declared image user.
The deterministic model fixture requires a stdlib Python interpreter in that
image, discovered through `command -v`; it does not change the server entrypoint.

The local-build run used `PYTHONPATH=/tmp/openhands-smol-atomic-runtime` and the
worktree's `.venv/bin/python -m pytest`, with the image above. The native SDK build
used the newer engine's Cargo lock as a temporary starting point. Its committed
SDK lock still targets engine 1.32.2; unrelated lock upgrades are excluded from
the contribution and belong to coordinated release bookkeeping.

## Acceptance mapping

| Spec | Evidence and remaining gate |
| --- | --- |
| S01 | Actual no-extra import/install error plus lazy-import unit test |
| S02 | Validation/availability unit cases and live local execution with an ambient test cloud token |
| S03 | Fake configuration/error contracts; real published image user, entrypoint, environment and authenticated readiness |
| S04 | Real simultaneous VMs, listener inspection and unauthorized request |
| S05 | Live commands, timeout, binary/Unicode files and Git |
| S06 | Live deterministic tool call and WebSocket events with outbound networking off |
| S07 | Unit failure matrix and live constructor/context/interruption/parent-exit cleanup |
| S08 | Unit serialization, reconstruction, secret and unsupported-operation checks |
| S09 | Live suite with outbound networking off; explicit Docker-free test |
| S10 | Default suite and checks pass; published-wheel and Linux platform acceptance pending |
