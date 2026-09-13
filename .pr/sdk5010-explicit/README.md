# Live Canvas: explicit conversation creation and attachment

**Passed:** the same uploaded bundle completed a real `deepseek-v4-flash` task in local and Docker workspaces using explicit `RemoteConversation.create` / `attach`. This refresh supersedes the historical ordinary-constructor evidence in `../sdk5010-narrowed`.

[Watch the demonstration](functional-demo.gif), or inspect the [individual screenshots](screenshots.json). The [exact executed tarball](fixture.tar.gz) contains `main.py`; [its manifest](bundle.json) records the entrypoint and hashes.

## What the recording demonstrates

| Change | Direct live evidence |
| --- | --- |
| SDK #5010 | Typed profile-based `create`, existing-conversation `attach`, `set_title`, byte upload, command/start-output polling, and scoped file download/Git changes/diff. [Missing attach](local/missing-attach.json) made exactly one GET, returned 404, and created no persisted conversation; no POST fallback occurred. |
| Automation #449 | The migrated dispatcher uploaded and executed the identical tarball through the existing workspace APIs in both runtime modes. |
| Automation #453 | The selected saved profile created each conversation, then the worker used explicit `attach` to run its model task. Earlier credential and queue-snapshot evidence is separate. |
| Docs #793 | Exact Python fences 1 and 2 at `eb1fe94f6ed01504def7520ff46d54741e08979b` ran unchanged: explicit create/profile/title/upload, then async `start_worker`. Its command read `Prepared by the dispatcher` and exited 0. Canvas displayed the created conversation and file. [Snippet hashes and results](local/docs-snippets.json). |

The bundle uploads `sdk-input.json` as bytes and reads it through `start_command` / `get_command_output`. The real agent reads that file, writes `fixture.json` containing `{"fixture":"canonical-workspace-api","verified":true}`, and reads it back. Canvas displays its actual actions and resulting file. A scoped host workspace independently downloads the result and inspects a tracked edit inside a disposable Git fixture. No application repository is accessed.

## A live failure found and fixed

The first explicit-create implementation, SDK `70c3afeb0`, serialized its unset agent as `null`. Both the unchanged automation and exact documentation snippet failed with HTTP 500 during request validation, before a worker or model started. Canvas showed **Failed to get execution context**. [Failure metadata](local/failed-creation.json) and its screenshot are retained.

SDK `4bbab2dd0ccfabb935ff42f48a9e1024f6e957b1` corrected canonical serialization using `exclude_none=True`. The same uploaded bundle and docs snippet then succeeded. The local activity screenshot shows both the failed-before and successful-after run; no example-side workaround was added.

## Exact revisions and observations

The host and Docker image both use integration `bd2c3789d8415106f962f0a0b8ca1157d70bca6b`, containing SDK source `4bbab2dd0ccfabb935ff42f48a9e1024f6e957b1`. [Package tree hashes](sdk-source.json), [image identity](image.json), and [all revisions](versions.json) are recorded. Canvas `9874c820a23e4026483ad9540ec9cb56194ce3ec` ran independently on port 9109.

Automation local `13edcb564120c32ee76735124d2184a0713e052c` and Docker `a9cc2adf35fd44220b0d4ee6caab1e6d2a0e96bb` have identical `openhands/` production source (tree `9de630f76614a6a26ce8a9088e7907bab1865f54`); later changes update pins/tests. The executed tarball SHA-256 is `0a88b63fb2447fcc0822446e364b4b0d2ccb14f8eb0490b13e54d500c7dd6bf6`.

| Runtime | Conversation/run | Recorded results |
| --- | --- | --- |
| Local | `7c8aff4b-a6c0-4768-9ef8-a500d8cac7c5` | [Completion and worker source](local/functional-output.json), [scoped file/Git checks](local/scoped-workspace-observations.json), [run history](local/completed-runs.json). |
| Docker | `4cc7f243-38f9-49b0-a07f-4d8f57dd3cc0` | [Completion and worker source](docker/functional-output.json), [scoped file/Git checks](docker/scoped-workspace-observations.json), [run history](docker/completed-runs.json). |

## Limits and cleanup

Only documentation fences 1 and 2 ran verbatim; the uploaded worker uses the documented explicit attachment API with a bounded task and timeout. The documentation setup itself makes no model call. The missing-attach assertion is an API observation accompanying the real Canvas demonstration.

The setup reused an isolated Python environment with standard offline editable SDK installs and a source-only Docker overlay. A preflight retained the check that the removed `openhands.sdk.client` module is absent. [Setup notes](setup-notes.json) disclose a readiness race, a mistyped read-only probe path, and early/empty observation attempts; these did not change the executed bundle or agent task.

One Docker worker was permitted, capped at 2,000 MiB, one CPU and 256 processes, with a 3 GiB host-memory guard. This is functional integration evidence; it does not repeat the separate credential-scope, ACP, or queue-policy matrix. Individual screenshots are real browser captures; the GIF condenses the sequence rather than showing wall-clock execution time.

Both workers exited `0` and reported the same `remote_conversation.py` SHA-256, `d763995f317876b3307c6c670d7fe38c314dd1f073a8830772100127c646654e`. [Final cleanup](docker/final-cleanup.json) confirms automatic runtime release with no remaining worker; all private services and proof tabs were then stopped. [Validation summary](validation.json).
