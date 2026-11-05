# Trivy-driven version bumps (2026-11-05)

Base: `origin/main` `232d0b0` (after #6307 merged). Scanner: the
OpenHands/security-findings Trivy invocation
(`--scanners vuln --skip-dirs '**/.openvscode-server/extensions'`, linux/amd64),
run locally against `ghcr.io/openhands/agent-server:232d0b0-python` with the
same Trivy DB for every comparison below.

## Main-image triage (232d0b0)

Total 2283 findings (CRITICAL 2, HIGH 173). CRITICAL+HIGH = 175, of which only
**19 have a fix available**; the other 156 are Debian packages with no
`FixedVersion` yet (65 in `linux-libc-dev` alone, then the `util-linux`
family, `libxml2`, curl, gnupg). Both remaining CRITICALs are in that
unfixable set. Nothing in this PR can address them; they clear when Debian
publishes fixes and the base snapshot pin moves.

### Fixable set, disposition

| Finding | Where it lives | Disposition |
|---|---|---|
| `msgpack` 1.2.1, CVE-2026-114613 (HIGH) | pip's vendored copy, re-vendored by the Dockerfile `pip-revendored` stage | **Bumped to 1.2.3 in this PR** |
| `undici` 7.29.1 ×4 editor HIGHs (+8 more at 6.28.0 in npm) | code-server 4.144.0 bundle; npm 11.21.0 bundle | Upstream-blocked (below) |
| `brace-expansion` 5.0.9 ×4 HIGHs | npm 11.21.0 bundle (both `/usr/local` and `/acp-node` copies) | Upstream-blocked (below) |
| `moby/go-archive` v0.2.0, `golang.org/x/net` v0.59.0 | `docker-buildx` v0.38.1 binary | Upstream-blocked (below) |

## This PR: msgpack 1.2.1 → 1.2.3

- `Dockerfile` `pip-revendored` stage: vendor.txt rewrite and its grep guard
  now target `msgpack==1.2.3` (the CVE-2026-114613 fix).
- `tests/agent_server/docker_runtime/test_pip_revendoring_retirement.py`:
  `FIXED_VERSIONS["msgpack"]` moved in lockstep (the retirement guard
  compares upstream pip against this floor; leaving it behind would retire
  the stage too early).

Why not retire the whole stage instead: upstream `python:3.13.18-slim-trixie`
probed 2026-11-05 still ships pip 26.2.2 vendoring `urllib3` 2.7.0 /
`msgpack` 1.1.2, so the guard correctly keeps the workaround.

Verified:

- `docker buildx build --target pip-revendored` succeeds; the built wheel's
  `pip/_vendor/msgpack/__init__.py` reports `__version__ = "1.2.3"`.
- `uv run pytest tests/agent_server/docker_runtime/test_pip_revendoring_retirement.py -m docker_live`
  → 1 passed.
- `uv run pre-commit run --files <changed>` → all passed.
- Expected scan effect: the single `msgpack` HIGH (CVE-2026-114613) drops;
  fixable CRITICAL+HIGH on main goes 19 → 18. (Full-image rescan happens in
  CI's Build & Push + the next security-findings run; the stage-level build
  above is the direct verification of the changed input.)

## Upstream-blocked (checked 2026-11-05, do not bump yet)

- **code-server**: newest release v4.145.0 (2026-11-04) still bundles
  `undici` 7.29.1 (verified in the release tarball and in
  `codercom/code-server:4.145.0`); the fix needs undici ≥ 7.30.1, which no
  code-server release ships yet. Its bundled Node is still v24.22.1, so the
  Dockerfile's Node-version assertion remains correct.
- **npm**: 11.21.0 is the newest npm 11.x. The automated pin
  (`.github/scripts/update_npm_pin.py`) is deliberately same-major with a
  7-day observation window. npm 12.2.2 does bundle fixed `undici` 6.29.1 /
  `brace-expansion` 5.0.12, but requires Node `^22.22.2 || ^24.15.0` while
  the image pins Node 24.22.1 and code-server asserts its own bundled Node —
  a major npm bump is a separate, riskier change.
- **buildx**: v0.38.2 (latest stable) still builds with
  `golang.org/x/net` v0.59.0 / `moby/go-archive` v0.2.0 (checked the release
  binary); the Docker CLI plugin comes from Docker's apt repo at latest, so
  there is no repo pin to move.
