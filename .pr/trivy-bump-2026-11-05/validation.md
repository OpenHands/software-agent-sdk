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
- Trivy rescan (binary, same DB/flags): see table in the PR description.

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
