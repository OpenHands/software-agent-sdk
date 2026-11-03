# Workspace file downloads: browser-cache validation

Related: [OpenHands/OpenHands#18603](https://github.com/OpenHands/OpenHands/pull/18603).

File downloads use mutable paths. On the base revision, the browser can reuse an
HTTP response after the file at that path has changed. The fix passes the standard
`RequestCache` option through `HttpClient` and selects `no-store` for
`FileClient.downloadFile()` and `RemoteWorkspace.fileDownload()`. It leaves other
requests, response parsing, and Agent Server response headers unchanged.

## Revisions and environment

- Base: `acd96e6fa424c02432304395b9208f99be24b54d`.
- Cache implementation: `be042f557997575f6ba046666adbe043fef45e72`.
- Tested fix with the dependency refresh: `0eef7e6352dd39179035384eaa5e953138bd171a`.
- Client and source Agent Server: version 1.58.1; Python 3.13.13, frozen `uv.lock`.
- macOS arm64, Google Chrome 155.0.8059.85, Node.js 26.0.0.
- Browser driver: Playwright 1.64.1; browser bundler: esbuild 0.28.2.

The base client was built before the source changes. The fixed client was rebuilt
from the tested commit. The JSON files record the source commit, bundled
client SHA-256, browser version, response cache metadata, and content hashes.
Subsequent evidence-only commits do not change the tested client code.

## Actual browser results

| Supported entry point                                       | Base second read | Fixed second read |
| ----------------------------------------------------------- | ---------------- | ----------------- |
| Host-level `FileClient.downloadFile()` (PNG bytes)          | Old bytes        | Current bytes     |
| Host-level `RemoteWorkspace.downloadAsText()`               | Old text         | Current text      |
| Conversation-scoped `FileClient.downloadFile()` (PNG bytes) | Old bytes        | Current bytes     |
| Conversation-scoped `RemoteWorkspace.downloadAsText()`      | Old text         | Current text      |

Chrome recorded 8 cached download responses on base and 0 after the fix. This
includes the second reads and invalid-key controls. On base, those invalid-key
browser reads reused already downloaded data; a direct network request with the
same invalid key returned 401. With the fix, the browser receives 401 as well.
This is browser-cache reuse, **not a server authentication bypass**.

- [Base machine-readable results](base.json) / [base browser screenshot](base.png).
- [Fixed machine-readable results](fixed.json) / [fixed browser screenshot](fixed.png).

## Reproduce

Requires Google Chrome installed in its usual location. From the repository root:

```sh
uv sync --frozen --group dev
npm --prefix clients/typescript ci
npm --prefix clients/typescript run build
npm install --prefix .agent_tmp/cache-validation-tools --no-audit --no-fund \
  playwright@1.64.1 esbuild@0.28.2
node .pr/download-cache/reproduce.mjs fixed
```

To repeat the base case, use a separate checkout at the base commit, copy this
`reproduce.mjs` into `.pr/download-cache/`, run the same setup, then pass `base`
instead of `fixed`. The script asserts stale second reads on base and fresh reads
on the fix. It also asserts invalid-key 401 responses on the fixed client.

The script starts an unmodified source Agent Server on an ephemeral loopback port
with an isolated persistence directory and a disposable session key. It creates
one conversation without a message or agent run, bundles the built client for a
real Chrome context, downloads an owned fixture, overwrites it, and downloads
the same URL again. Node HTTP requests independently check the current bytes and
server authentication. The browser and server are stopped and temporary state is
removed in the cleanup path.

The initial fixture mtime is deliberately set to 2020-01-01 to make Chromium's
`Last-Modified` heuristic freshness deterministic. No request interception,
response mocks, global cache disabling, or synthetic HTTP cache implementation
is used. The workflow follows the [earlier public cache probe](https://github.com/smolpaws/OpenHands/blob/8736dcb0a517fa56f970e2bfd024948dcd202fb0/.pr/preview-18603/cache-probe.mjs),
extended here to both client entry points and conversation-scoped routes.

## Regression and build checks

Before changing production code, the focused tests with the new assertions gave
**8 failed / 105 passed**. All failures were missing cache options on the affected
requests. With the fix, the same suite gave **113 passed**. Six new parameterized
cases were added; existing download assertions were also strengthened.

From `clients/typescript/`:

```sh
npm test -- src/__tests__/api-clients.test.ts \
  src/__tests__/conversation-scope.test.ts \
  src/__tests__/remote-workspace-download.test.ts
npm run test:coverage
npm run lint
npm run build
npm run format:check
npm run test:public-type-budget
npm run check:public-type-budget
npm run test:agent-server-api-tooling
npm run check:agent-server-api
```

- Full client suite: **400 tests passed across 27 files**.
- Lint: 0 errors; 5 existing warnings on unchanged lines.
- Build, format, public type budget and API-contract checks passed.
- Per-file repository pre-commit checks and `git diff --check` passed.
- Regression tests cover host-level, conversation-scoped and legacy `cid`
  requests, explicit cache options, and unchanged defaults on unrelated requests.

## Production dependency audit

The initial remote CI security check failed on Zod 4.3.6 due to
[GHSA-mrhx-c7xc-m3w5](https://github.com/advisories/GHSA-mrhx-c7xc-m3w5).
The base and initial PR dependency files were byte-identical, and auditing the
base lockfile reproduced the same finding. The separate dependency commit
`0eef7e63` updates only Zod's lockfile version, tarball URL and integrity to
4.6.7, within OpenRouter's existing `^3.25.0 || ^4.0.0` range. The package manifest,
project version and all other locked packages are unchanged.

After a clean `npm ci`, both CI audit commands passed with zero production
vulnerabilities:

```sh
npm audit --audit-level=moderate --omit=dev
npm audit --audit-level=high --production
```

The full 400-test suite, lint, build/public type budget, format check and pre-commit
checks passed again. All four real Chrome download scenarios also passed after
the refresh; `fixed.json` and `fixed.png` now record this run. Development-only
dependency advisories remain outside the CI production gate. This lockfile
refresh fixes this repository's installation; downstream applications with their
own older lockfiles still need to update their resolution.

## Limits and scope

This is real browser → built SDK client → source Agent Server validation. The
screenshots show a diagnostic runner, not the Agent Canvas Files UI. It does not
complete the separate Canvas PR's UI acceptance test or release a new client
package. Docker/Cloud and other browsers were not exercised. No LLM calls or
agent benchmark runs were performed; the change does not alter model requests,
prompts, or tool execution.

The existing binary decoding problem in `RemoteWorkspace` belongs to
[SDK#5762](https://github.com/OpenHands/software-agent-sdk/pull/5762) and is not
changed here. PNG fidelity is tested through `FileClient`; `RemoteWorkspace` uses
a UTF-8 text fixture to isolate caching from that independent issue.

## Duplicate check

On 2026-10-24, searches of open and closed SDK PRs for file downloads, `FileClient`,
browser caching, stale bytes, and `no-store` found no matching cache fix.
`#5762` concerns binary decoding, while `#5542`/`#5766` concern temporary trajectory
ZIP files. Canvas `#18603` explicitly identifies the SDK cache fix as a dependency.
