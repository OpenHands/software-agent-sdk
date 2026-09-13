# Live Canvas: orchestration through the existing SDK APIs

**Passed:** the same uploaded bundle completed a real DeepSeek task in a local workspace and a Docker workspace. Both runs exited `0`, produced the expected file, and appeared as **Successful** in Agent Canvas.

[Watch the 24-second demonstration](functional-demo.gif). The [numbered screenshots](screenshots.json) are also available individually for documentation. The [exact executed tarball](fixture.tar.gz) contains the reproducible `main.py` fixture; its entrypoint and hashes are in the [bundle manifest](bundle.json).

## What this validates

| PR | Direct evidence |
| --- | --- |
| [SDK #5010](https://github.com/OpenHands/software-agent-sdk/pull/5010) | Ordinary `RemoteConversation` creation from a profile and attachment to an existing conversation; `set_title`; workspace byte upload, background command/start-output polling, scoped download and Git changes/diff; explicit runtime release. |
| [Automation #449](https://github.com/OpenHands/automation/pull/449) | Migrated dispatcher uploaded and executed an identical bundle through the canonical workspace APIs in local and Docker modes; both runs completed successfully. |
| [Automation #453](https://github.com/OpenHands/automation/pull/453) | The selected saved profile created each conversation, and the bundle attached to that server-resolved conversation through the ordinary constructor. This recording does not re-test the earlier credential-scope or queue-snapshot matrix. |
| [Docs #793](https://github.com/OpenHands/docs/pull/793) | Exact Python fences 1 and 2 from revision `5856a140abcde00edf60826a2f0603603244cc94` executed: create/profile/title/upload, then the async `start_worker` helper. Its command read back `Prepared by the dispatcher` and exited `0`. Canvas displayed the created conversation and file. |

The bundle uploaded `sdk-input.json` as bytes and verified it with `start_command` / `get_command_output`. A real `deepseek-v4-flash` agent then read that file, wrote `fixture.json` containing `{"fixture":"canonical-workspace-api","verified":true}`, and read its result back. Canvas displayed the agent actions and file in both modes. A scoped host workspace independently downloaded the result and observed a tracked edit in a disposable Git fixture using `git_changes` and `git_diff`.

The bundle interface matches the documentation's ordinary constructor, message, run, and event-history operations. Its task text and timeout are deliberately bounded for this demonstration; only the first two documentation fences were executed verbatim. The documentation setup itself made no model call.

## Exact versions and results

- SDK PR source: `a2c0cf2d38967a7abd383223f3d3f192ccf99290`; runtime integration used by both host and image: `045a1f1de62d1c21945af85527357d2decf2059e`. [Source tree hashes](sdk-source.json) and [image identity](image.json) are recorded.
- Canvas: `9874c820a23e4026483ad9540ec9cb56194ce3ec`, independently served on port 9109.
- Automation: local `b6f4ca4fa4dcf05882e0760d70641168896fb38f`; Docker `57ab0d8d3c31be9f3649dc9973a268f8927c598e`. Their `openhands/` production source is identical; the intervening changes update dependency pins/tests.
- Identical tarball SHA-256: `6317ff2a9a523d9799eb0b48d6fef5a75c9c2296c581a7946eda372db76f28e7` ([manifest](bundle.json)).
- Both actual workers reported the same `remote_conversation.py` SHA-256: `b905f1571ea81efc53315a296c77f8777f8b9740208077c397f1d069cb69ef8a`, from the expected host/image installation paths.

| Runtime | Conversation/run | Result |
| --- | --- | --- |
| Local | `a36b92b9-6890-4599-9993-7f1860a43996` | [Completed, exit 0, SDK/task markers](local/functional-output.json); [scoped file/Git checks](local/scoped-workspace-observations.json). |
| Docker | `28ea6182-857f-419f-8228-89ffd4c8fcea` | [Completed, exit 0, matching SDK/task markers](docker/functional-output.json); [scoped file/Git checks](docker/scoped-workspace-observations.json). |

[Documentation snippet hashes and result](local/docs-snippets.json) identify the exact snippets and the additional created conversation.

## Setup failures and limits

Two preliminary local runs stopped **before any model or task call** at the fixture's version guard: the reused Python environment still exposed the old editable SDK client module. The harness was corrected using an isolated environment and standard offline, dependency-free editable installs of all four SDK packages. The guard remained enabled, and the successful workers recorded their actual source paths/hashes. Those failed runs are excluded from the demonstration; their private logs and screenshots are retained.

The original Docker worker was released after completion. Later workspace inspection provisioned a successor for that completed conversation; it was explicitly released through `RemoteWorkspace.release_runtime`. [Final cleanup](docker/final-cleanup.json) confirms no worker remains. The recording does not claim that post-completion workspace reads are free of runtime provisioning.

Only one Docker worker was active at a time, limited to 2,000 MiB, one CPU and 256 processes. A 3 GiB available-memory guard stayed active without interruption. All private services and proof tabs were stopped afterward. No application repository was modified. This is functional integration evidence, not a new credential-security test, benchmark, or replacement for the separate profile-scope evidence. The earlier #5010 error-query recording is historical and does not validate this narrowed PR.
