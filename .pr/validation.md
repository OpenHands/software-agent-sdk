# Browser-only download guard in Node.js

Base: `9c3571a694547734002518bd94b3cb41a187f9b6`. Tested on macOS, Node.js 26.0.0, npm 11.12.1.

## Reproduce

Install and build from `clients/typescript`:

```sh
npm ci
npm run build
```

Then from the repository root:

```sh
node .pr/repro_ts_download.mjs
```

The script imports the emitted JavaScript package, creates a public
`RemoteWorkspace`, and intercepts `fetch` with an in-process `Response` while
counting calls. It uses no Jest helpers, real network request, server, or browser.
The error names the existing Node alternatives before any fetch request occurs.
The portable `.pr/` script was rerun successfully; see [fixed.log](fixed.log).

[baseline.log](baseline.log) is the recorded before-fix Jest failure: the original
implementation threw `ReferenceError: document is not defined`. The original
package was not rebuilt for a separate standalone baseline run.

## Regression coverage

From `clients/typescript`:

```sh
npm run lint
npm run build
npm run test:coverage -- --runInBand
npm run format:check
```

Recorded during implementation: 19 suites / 318 tests passed; lint had zero
errors and eight existing warnings; build and formatting passed. The new test
file had one failure and three passes before the guard, then four passes. It
covers early rejection without a fetch, both Node alternatives, and download
behavior with a DOM test double. The full suite was not rerun when packaging
these artifacts.

## Limits

The Node 22.12/24.x CI matrix, Docker integration, and real browsers were not
run locally. The root pre-commit runner's Python hooks do not match these
TypeScript files; its always-run dynamic-attribute gate reports the same four
pre-existing Python violations as the base. Client checks above are the
applicable validation.

The change adds an environment check and `@throws` to an existing convenience
method. It introduces no public API, server endpoint, dependency, or generated
contract change.
