# Embedded-path skill installation: verification

Fixes [enyst/agent-sdk#39](https://github.com/enyst/agent-sdk/issues/39).

The fetcher already selected `skills/github` from
`github:OpenHands/extensions/skills/github`. The installation loader saw the
original `repo_path=None`, classified the selected directory as a repository
root, and loaded it under the name `extensions`. Strict validation then rejected
the skill's declared name `github`.

The patch shares the existing source/subpath resolution between fetching and
loading. It preserves the public parser's two-value return, original persisted
source and `repo_path`, explicit path conflict/containment checks, and the
repository-root naming behavior introduced by upstream #5905. Updates use the
same path resolution. No REST or TypeScript contract changes are needed.

## Revisions and live evidence

- Before: `5966c781ed014efc96c028a99bddd4ff78426741` (frozen fork main).
- After: `31897798e8bd8e62ef56bdb86dba6588efbbc557` (committed product and tests).
- Harness: maintenance PR [#43](https://github.com/enyst/agent-sdk/pull/43),
  `fe5ee276`, using the maintained CLI and recipes with `--checkout` selecting
  this fix's own virtual environment and source tree.
- Runtime: Linux, Python 3.13.5, source-run Agent Server/SDK 1.55.1. Requests use
  the actual authenticated HTTP install endpoint and clone the public GitHub
  repository. This matches the issue's source-server reproduction; no packaged
  binary or Docker-image behavior is claimed.

| Run ID | Revision | Explicit `repo_path` control | Embedded path |
| --- | --- | --- | --- |
| `20261023-054655-a752-map-f25` | before | 200, `github` | 422, invalid skill |
| `20261023-054705-bb2c-map-f25` | before | 200, `github` | 422, invalid skill |
| `20261023-065443-4190-map-f25` | after | 200, `github` | 200, `github` |
| `20261023-065459-fd2d-map-f25` | after | 200, `github` | 200, `github` |

[Safe request/response extracts](fix-39-live-evidence.json) include run revisions,
timestamps, request bodies, and response status/name/source/path/commit fields.
The same remote repository commit was installed by both forms. Both fixed runs
completed their normal scoped server shutdown.

Reproduction command, run from the fix checkout:

```sh
export PATH=/workspace/.tools/tmux/usr/bin:/workspace/agent-sdk-verify/.agents/skills/verify-agent-server/scripts:$PATH
docker-init -s -- /workspace/agent-sdk-verify/.agents/skills/verify-agent-server/scripts/control-agent-server \
  map run --file F25 --only F25.install-documented-sources \
  --fresh --repeat 2 --record --keep-going \
  --checkout /workspace/agent-sdk-fix-39
```

Each fixed run reports two passes (preconditions and explicit-path control),
one XPASS, zero failures, and zero blockers. Exit 1 is expected because the
maintenance recipe still marks #39 as a known bug. Its 200/name assertions now
succeed. This product PR does not edit that recipe.

## Regression checks

The new parameterized test uses real git clones and updates, with a process-local
git URL rewrite redirecting the GitHub-shaped source to a temporary repository.
It exercises the actual parser, fetcher, installer, strict skill loader, and
metadata. Embedded paths (including a trailing slash), explicit paths, and
repository-root skills retain their names through installation and update;
updates preserve disabled state and refresh content and resolved commit.

Before the product patch, the new matrix produced one failure and two passes.
The failure was `SkillValidationError: Invalid skill name 'sample-skill':
Name 'sample-skill' does not match directory 'extensions'` for the embedded path.

After the product patch:

```sh
uv run pytest tests/sdk/extensions tests/sdk/skills/test_installed_skills.py \
  tests/sdk/plugin/test_installed_plugins.py -m 'not network' -q
# 156 passed, 3 network tests deselected

uv run pytest tests/agent_server/test_skills_router.py \
  tests/agent_server/test_skills_service.py -q
# 93 passed
```

The 156-test suite includes existing local source, explicit subpath, parser,
containment, conflict, and real-git manifestless plugin root/legacy-update-name
coverage. External-network behavior is covered by the live runs above.
All changed Python files pass repository pre-commit checks, including Ruff,
pycodestyle, Pyright, and import/registration rules. `git diff --check` passes.

No dependency, schema, package version, or source-format change is introduced.
