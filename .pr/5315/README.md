# Docker title profiles: integration evidence

Issue #5310; existing PR #5315. The proposed merge preserves published parent `a8ea83a200113577fd55339593b55e2e60097f39` and merges main `6f8c38d00e0468ac138447be905f07a8a0cd7af8`. No integration commit exists at preparation time.

Only `docker_runtime/provisioning.py`, `docker_runtime/routers.py` and `tests/agent_server/docker_runtime/test_routes.py` differ from main outside this temporary evidence bundle. Their complete patch SHA-256 is `4e7147038dbc30d84b085407129f118ecceb1e54d82c8285605357e8778b8a27`, identical to the independently re-reviewed implementation. All 1,835 tracked files outside `.pr/` match that reviewed checkout. This replaces the obsolete mediation-based patch; deleted launch APIs are not restored.

The provisioning store snapshots only the selected title profile, resolves linked provider credentials on the host and encrypts using the runtime key. The existing resolve/finalize launch path and title consumer are unchanged. A per-conversation start lock covers preparation, the inner creation response and deletion. Cancellation is propagated after the actual filesystem worker finishes, including repeated cancellation; worker errors are retained as the cancellation cause. Established snapshots, fallback, failed-start retry and release/reprovision behavior remain covered.

## Actual Canvas recordings (retained, not rerun during integration)

The workflow used the real Canvas UI, outer Agent Server and per-conversation Docker runtime. Both baseline and corrected trials selected `title-aux`, started a new chat with the same harmless message, waited for the primary reply and reloaded to display the persisted title. [Selected profile](title-settings.png).

| Workflow | Baseline | Corrected |
| --- | --- | --- |
| OpenHands agent | [Agent fallback title](baseline-openhands.png) | [Aux profile title](corrected-openhands.png) |
| ACPAgent / scripted stdio backend | [Message truncation](baseline-acp.png) | [Aux profile title](corrected-acp.png) |

Baseline recordings: October 5 PDT / October 6 UTC, clean `6f8c38d`. Corrected recordings: October 6, same base plus the reviewed complete patch above. [Provenance](provenance.json) includes each conversation, image, source hashes, observed provider/model selection, runtime log excerpts, encryption/isolation checks and original summary digests. Screenshots are unchanged captures.

Fresh integration verification inspected the retained corrected image `sha256:d91892f2fbc4430a965504bb58e8bfe97bfed8388df3172c64dc8c667a11b8ac`: all 522 selected copied build-input files and 502 installed Python files match the integration. Seven actual imported modules were hashed again. The Dockerfile, lockfile, package metadata and build definitions match the reviewed tree. Thus no source/build-input change requires another image build or UI trial. Image metadata still names the reviewed base; it is not an image built from a future merge commit. The eventual commit must be bound to the exact staged/tested tree before publication.

## Fresh integration checks (October 6)

Commands run in the isolated integration checkout with a frozen environment. Counts overlap; do not add them.

- `uv run --frozen pytest tests/agent_server/docker_runtime ../evidence/reviewer-20261006/test_repeated_cancellation.py -q`: **81 passed, 3 warnings**; 79 permanent tests plus both unchanged external probes. Production imports were verified to resolve to the integration checkout. The external probe loads its fixture from the reviewed checkout; that fixture file is byte-identical to the integrated file.
- Reversing only the router cancellation correction: **3 failed, 3 passed, 23 deselected** in `test_routes.py -k delete_waits_for_title_preparation`. Restoring exact bytes: **81 passed** again. Router SHA-256 restored to `0d2cdd2744fda4e158f31174de786034232075b90ff3f2a618989aa04936cab8`.
- Agent-server, SDK launch, title-generation and LLM/provider profile-store suites: **2,593 passed, 8 failed, 58 warnings**. The identical eight failure IDs reproduced on clean `6f8c38d` (**2 passed, 8 failed**): seven Linux-artifact Canvas-extension cases on macOS and one macOS Unix-socket path-length case.
- Fresh Linux verification in the retained corrected image, using integration tests mounted read-only with network disabled: **237 passed, 7 warnings** (Docker, launch, title/profile and the macOS socket-failure case). The Linux Canvas-extension backend module was excluded; its previously documented minimal-image fixture limitation was not rerun.
- Persisted-checker/OpenAPI/discriminator/settings-migration tests: **28 passed, 1 warning**. Official persisted-settings check: **23 golden fixtures + 8 PyPI 1.53.0 baseline payloads passed**.
- Full canonical OpenAPI and RuntimeIdentity schema equal clean main; digest `5a7a70ed221fba0d2b70122e50cf8148c6cb1317e71241ad80824dfdf69b9203`.
- Applicable pre-commit checks passed, including Pyright; YAML had no applicable changed file.

The historical independent correction re-review separately passed 81 tests; it did not independently rerun the broader suites or live trials. These integration checks are a new local run, not remote CI or maintainer acceptance.

## Limits and remote gates

Canvas revision `0ab2137ad529f2f5935edf152d25a3f8f5f59293` (1.25.0), SDK/server/client 1.53.0; Linux arm64 source-minimal runtime. The auxiliary HTTP endpoint and credentials are synthetic. ACP uses a real ACPAgent/stdio process running Canvas's existing mock backend, not authenticated Codex/Claude. No paid/private-provider call, production conversation, model-quality claim or full ACP coverage. The retained Canvas build used Node 22.22.1 despite its >=24 engine declaration; existing warnings/automation 404s did not prevent these trials. No frontend refresh fix is claimed.

No full repository suite, opt-in stress/acp_live, Windows, amd64, PyInstaller, TypeScript CI or real-provider matrix was run here. Current published head still has 12 workflows `completed/action_required`, requiring maintainer approval/execution; two metadata workflows succeeded. Fresh current-head remote CI and maintainer review remain required after publication. Existing review threads are not resolved by these local artifacts. The repository's commit-trailer rule still needs an explicit maintainer exception to the author's no-new-co-author instruction.

The two earlier `.pr/docker_title_profile_e2e*` files are historical and were archived outside the publication tree. Upstream's unrelated `.pr/validation.md` is retained unchanged and does not verify this PR. Raw local logs, harnesses and persistence directories are deliberately excluded from this small publication bundle.
