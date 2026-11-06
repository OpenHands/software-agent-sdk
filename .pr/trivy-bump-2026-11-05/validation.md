# Trivy-driven dependency upgrades (2026-11-05)

Base: `origin/main` `232d0b0` (after #6307 merged). Scanner: the
OpenHands/security-findings Trivy invocation
(`--scanners vuln --skip-dirs '**/.openvscode-server/extensions'`, linux/amd64),
one Trivy DB for every comparison below. The shipped `-python` tag is the
**binary** flavor image, so all headline counts compare binary-to-binary;
a source-flavor scan is listed separately and is not comparable (it ships
the full SDK venv).

## Main-image triage (232d0b0, binary)

Total 2283 findings (CRITICAL 2, HIGH 173). CRITICAL+HIGH = 175, of which only
**19 had a fix available**; the other 156 are Debian packages with no
`FixedVersion` yet (65 in `linux-libc-dev` alone, then the `util-linux`
family, `libxml2`, curl, gnupg — including both remaining CRITICALs).
Those clear when Debian publishes fixes and the snapshot pin moves.

### Fixable set on main, disposition after this PR

| Finding (main) | Where it lives | Disposition |
|---|---|---|
| `msgpack` 1.2.1, CVE-2026-114613 (HIGH) | pip's vendored copy, re-vendored by the Dockerfile `pip-revendored` stage | **Bumped to 1.2.3** |
| `undici` 7.29.1 ×4 editor HIGHs | code-server 4.144.0 bundle | **4.145.0 + in-place undici 7.30.1 swap** |
| `undici` 6.x / `brace-expansion` 5.0.9, 16 findings | npm 11.21.0 bundle (`/usr/local` + `/acp-node`) | **npm 12.2.2** (bundles undici 6.29.1, brace-expansion 5.0.12) |
| `moby/go-archive` v0.2.0, `golang.org/x/net` v0.59.0 | `docker-buildx` v0.38.1 Go binary | No upstream fix yet (below) |

## This PR — aggressive upgrades

Following direction to be aggressive rather than conservative, this PR
takes major/newest versions instead of waiting:

- **`msgpack` 1.2.1 → 1.2.3** (CVE-2026-114613). Dockerfile `pip-revendored`
  pin + grep guard, and `FIXED_VERSIONS["msgpack"]` in
  `tests/agent_server/docker_runtime/test_pip_revendoring_retirement.py`
  moved in lockstep (leaving it would retire the stage too early).
  Upstream `python:3.13.18` still ships pip 26.2.2 (msgpack 1.1.2), so the
  stage itself must stay.
- **npm 11.21.0 → 12.2.2** (major). Bundles fixed `undici` 6.29.1 and
  `brace-expansion` 5.0.12 in both the `/usr/local` and `/acp-node` trees.
  npm 12 requires Node `^22.22.2 || ^24.15.0`; the image's Node is newer,
  so the engine floor clears. The automated pin
  (`.github/scripts/update_npm_pin.py`) previously held the same major
  hostage with a 7-day observation window — it now selects the newest
  stable release (still 7-day-observed), so it won't pin npm back to 11.x.
- **code-server 4.144.0 → 4.145.0** (both arch digests from the GitHub
  release) **plus an in-place undici swap**: 4.145.0 still bundles undici
  7.29.1 (CVE-2026-109383/-109829/-109869/-87018; fixed in 7.30.1, which no
  code-server release ships yet — verified in the release tarball and in
  `codercom/code-server:4.145.0`). The Dockerfile downloads undici 7.30.1
  from the npm registry (sha256-pinned), replaces
  `lib/vscode/node_modules/undici`, and asserts the installed version.
  Drop the swap once code-server ships ≥ 7.30.1. The bundled-Node
  assertion (v24.22.1) is unchanged — that binary is upstream's.

## Future-proofing (head `939b0da`)

The one-off bumps above decay: left alone, the next editor/npm release is
another manual Trivy fire drill. This follow-on commit makes the refresh
persistent and removes the exact-version traps the bumps introduced:

- **Scheduled code-server refresher** (`.github/scripts/update_code_server_pin.py`
  + `.github/workflows/update-code-server-pin.yml`, weekly, mirroring the
  npm/Debian-snapshot bots): selects the newest stable, non-draft
  code-server release after the same 7-day observation window, then
  updates `CODE_SERVER_VERSION` and **both** arch digests atomically from
  the GitHub release assets (fail-closed if either digest is missing —
  a partial pin would produce a Dockerfile that can never build). Lands
  as a draft PR for review; no-ops when already current (verified against
  the live release API: 4.145.0 stays 4.145.0).
- **Self-retiring undici swap + bundled-Node floor**: the editor block no
  longer asserts exact bundled versions. `CODE_SERVER_NODE_MIN_VERSION`
  (24.22.1, the Brotli fix) and `CODE_SERVER_UNDICI_MIN_VERSION` (7.30.1)
  are floors compared with `sort -V`: the undici download/replace only
  runs while the bundled copy is below the floor, so the first
  code-server release shipping ≥ 7.30.1 makes the swap inert with no
  Dockerfile edit, while anything older still fails the build. Raising a
  floor is a one-line ARG change; the refresher workflow does not need to
  know the swap exists.
- **npm major-crossing is explicit**: `update_npm_pin.py` defaults to
  same-major again; crossing majors requires `--allow-major` /
  `ALLOW_MAJOR_UPGRADE=true`, which the scheduled workflow sets
  deliberately (bundled-dependency fixes are only guaranteed per major).
  A future major therefore arrives as its own reviewed draft PR, not as
  a silent side effect of a Trivy PR.
- **Pin-consistency guards** (`tests/cross`): Node (`node:` image stage
  vs tarball sites) and uv (both `COPY --from` sites) must agree across
  the Dockerfile, and the code-server block must keep its single version
  pin, both digest pins, and floor ARGs. Duplicated hand pins now fail CI
  if they drift.
- Verified: `test_update_npm_pin.py`, `test_update_code_server_pin.py`,
  `test_agent_server_build_metadata.py` — 22 passed locally; pre-commit
  clean. Full PR CI on `939b0da`: **50 SUCCESS, 1 skipped, 0 failures**
  (incl. all `Build & Push` variants, which exercise the refactored
  floor logic). CI-built `ghcr.io/openhands/agent-server:939b0da-python-amd64`
  pulled and probed: Node v24.22.2, npm 12.2.2 (undici 6.29.1), editor
  4.145.0 with bundled Node v24.22.1 / undici 7.30.1, uv 0.13.7; container
  logs show VS Code service started. Trivy numbers are unchanged from
  `c122226` (same pinned versions; the refactor changes no shipped bits).
- **Node 24.22.1 → 24.22.2** for the `node-runtime` stage and the ACP
  runtime tarball (both arch sha256s updated). Newest 24.x LTS patch;
  no Trivy findings either way, taken as hygiene with the npm major.
- **`uv` 0.13.5 → 0.13.7** (both `COPY --from` sites: builder + base-image).

## Verification

- Full **binary** image built locally from this Dockerfile
  (`--target binary`, same args as CI): builds clean.
- In-image probes: Node v24.22.2 (runtime + ACP), npm 12.2.2 (both trees),
  uv 0.13.7, system pip vendors msgpack (1, 2, 3) / urllib3 2.8.0,
  code-server reports `4.145.0 … with Code 1.145.0` with bundled
  `undici` 7.30.1; both npm trees bundle undici 6.29.1 / brace-expansion
  5.0.12.
- `tests/agent_server/test_code_server_live.py` run against the
  code-server tree extracted from the *built image* (auth, token
  rotation, WebSocket paths): passed. Also passed against a standalone
  4.145.0 + undici-7.30.1 patch before the image build.
- `tests/cross/test_agent_server_build_metadata.py`: 14 passed.
  `test_pip_revendoring_retirement.py` (docker_live): passed.
  `pre-commit` on changed files: passed.
- Agent-server container boots from the bumped binary image and serves
  `/health`.
- **CI-built binary (`ghcr.io/openhands/agent-server:c122226-python-amd64`,
  head `c122226`) rescan, binary-to-binary, same DB/flags**: **total
  2283 (main) → 2232 (this PR); C+H 175 → 158; fixable C+H 19 → 2**
  (C2/H156/M888/L1114). Per-CVE: `brace-expansion` CVE-2026-102276/-102278 2→0
  each, all four editor `undici` HIGHs →0 (plus CVE-2026-19534 ×2),
  `msgpack` CVE-2026-114613 →0. **Zero new C+H vs main.** The only
  remaining fixable C+H are the two `docker-buildx` Go modules below.
  (Release 1.59.0 for reference: 3160 total, C+H 308.)
- CI caught a real bug the local build couldn't: the undici-swap probes
  used plain `node`, which doesn't exist in the golang/java base-image
  variants (no system Node there) — `Build & Push (golang-*/java-*)`
  failed on head `1bec757`. Fixed in `c122226` by probing with
  code-server's bundled `"${CODE_SERVER_ROOT}/lib/node"`; all
  `Build & Push` variants then passed (python-amd64 10m48s).
- Manual end-to-end check: isolated Agent Canvas stack
  (`run-code-server-canvas.sh`, SDK `main@232d0b0`, editor tree
  4.145.0/undici 7.30.1) raised at `https://oh-pr6307.ngrok-free.app`
  for a by-hand VS Code tab sanity pass (no `/setup`, file edit/save,
  settings kept) by the PR owner.

## Remaining, genuinely not fixable from this repo today

- **156 unfixable CRITICAL+HIGH** — Debian packages with no `FixedVersion`
  (incl. both CRITICALs). Wait for Debian + snapshot-pin move.
- **`docker-buildx` Go modules** (`moby/go-archive` v0.2.0 →
  CVE fixed in 0.3.0; `golang.org/x/net` v0.59.0 → 0.60.0): buildx v0.38.2
  (latest stable, checked binary) is still built with the vulnerable
  modules, and the plugin arrives at latest from Docker's apt repo — no
  repo pin exists to move. Clears when a buildx built with the fixed
  modules ships.
- **Source-flavor only**: the SDK venv's `nodejs-wheel-binaries` 24.19.0
  (pyright's Node) bundles npm 11.17.0 / undici 6.26.0. 24.19.0 is already
  the newest release on PyPI, and the *binary* image (the shipped tag)
  doesn't contain it. Same for the venv's vendored pip (msgpack 1.1.2) —
  not present in the binary image.
