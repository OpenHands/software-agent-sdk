# Notes, history retrieval, and storage safety — validation

Working branch: `feat/notes-history-storage-safety`.
Original base: `bd5fff06c2fe79d6e0e5d192bbf22339482f2805`.
Original implementation used for the live evaluation: `69179b1b456e2a281217be9016a77924be817b3d`.
Current implementation after merging upstream: `0934c600d9e8bbf289f63e3324ec14f63bac1223`.
Published drafts: [SDK #5312](https://github.com/OpenHands/software-agent-sdk/pull/5312) and [docs #837](https://github.com/OpenHands/docs/pull/837).

## Implemented behavior

- Notes store complete successful write/append observations, including masked content. Each mutation is limited to 16,000 characters, while the accumulated note has no fixed character limit. Versioned reads page through the exact text.
- History searches and reads the active EventLog branch. Context reset changes the model's view; it never deletes source events. No second full-history store is introduced.
- The optional notes condenser reminds once per window, preserves required initial/recent context and complete tool exchanges, and supports manual or threshold resets. Existing condenser defaults remain unchanged.
- Optional local disk protection defaults to disabled. The example enables a 5% free-space floor. Server policy is configured with `OH_STORAGE_SAFETY_MIN_FREE_RATIO=0.05`; it is inherited by subagents and cannot be disabled by per-agent settings.
- Admission checks, a 5-second watchdog, and UTF-8 atomic-write headroom checks stop new work. In-flight results may use the shutdown reserve. No automatic file deletion or automatic execution restart is introduced.
- Write failures use an independent notification/error path. Explicit recovery reconciles committed events and marks unknown tool outcomes without replaying them. An ambiguous cold-start branch requires an inspected `head_event_id`.
- Python remote recovery uses the server endpoint; it never measures the client computer's disk.

## Test evidence

Dependency setup: `make build` completed successfully.

Broad regression run:

```sh
uv run pytest -q \
  tests/sdk/tool tests/sdk/context/condenser tests/sdk/agent tests/sdk/io \
  tests/sdk/conversation/local tests/sdk/conversation/test_event_store.py \
  tests/sdk/test_settings.py tests/agent_server/storage \
  tests/agent_server/test_config.py tests/agent_server/test_api.py \
  tests/agent_server/test_event_service.py tests/agent_server/test_conversation_router.py \
  tests/tools/task tests/examples/test_examples.py
```

Result: **1,970 passed, 70 skipped, 6 deselected**. Example-discovery skips are the repository's opt-in runnable examples; the new example was also executed directly below. Subsequent targeted runs cover final changes, rather than treating overlapping runs as additional unique tests:

| Scope | Result |
|---|---|
| Condenser, agent notes behavior, settings after model token-cap clamp | 155 passed |
| Server storage and EventService after sharing the metadata file store | 120 passed |
| Disk diagnostics, server storage, Python remote conversation suite | 156 passed |
| Full real HTTP/WebSocket remote-server suite | 22 passed, 1 skipped |
| Final Python remote storage-recovery wrapper over real HTTP | 1 passed, 22 deselected |
| Final storage IO and local lifecycle fault-injection suite | 19 passed |
| Final notes commit wording and agent reset behavior | 16 passed |
| Final critic admission, iterative refinement, and storage tests | 85 passed |
| TypeScript full coverage suite | 336 passed across 21 files |
| Persisted settings compatibility gate | 14 historical fixtures and 8 PyPI baseline payloads passed |
| Original exported OpenAPI quality gate | Passed; 102 explicitly allowlisted weak schema locations |

The 3 new OpenAPI allowlist entries are inherited `ToolDefinition.meta` opaque dictionaries, matching existing built-in tool metadata. They do not weaken recovery request validation.

Fault injection covers an actual temporary-file replacement failure, a second tool result failing after the first result committed, and a base-state snapshot failing after a tool observation committed. Sync and async recovery keep confirmed results and do not re-execute uncertain actions. Direct recovery from metadata-only failure was also exercised against a real EventService without an intermediate run.

The optional critic is checked immediately before its model evaluation. If space drops after the main model has completed, the critic is skipped and the original response is committed before the pause. Both sync and async tests verify that persisted response and the absence of a critic call.

Every edited Python file was checked with `uv run pre-commit run --files ...`, including Ruff, pycodestyle, Pyright, SDK attribute/import rules, and tool registration. A combined pass across all 42 changed Python files also passed; see [log](evidence/pre-commit.log). Later focused changes passed their individual checks.

From `clients/typescript`, `npm ci`, `npm run build`, `npm run lint`, `npm run format:check`, `npm run check:public-type-budget`, and `npm run test:coverage` passed. Lint reported 9 existing warnings and no errors. The public type budget remained at 103 `unknown` and zero `any`.

After the full coverage run, the new test's settings envelope was corrected to the canonical `agent_settings_diff`. All 8 focused cases and an explicit strict TypeScript check of that test passed afterward; the production client code was unchanged.

The TypeScript client adds recovery methods and request/event/condenser types. Its pinned released Agent Server schema and image version remain unchanged, following the repository's release-generation workflow. A current backend OpenAPI and generated TypeScript candidate were checked locally for shape verification; these large generated files are not included in the review artifact commit. The additive client APIs require a server containing this feature.

## Executed example

```sh
uv run examples/01_standalone_sdk/notes_retrieval/main.py --offline
```

Result: one context reset; persisted reopen recovered the exact saved launch code and region; reported cost `$0.00`. See [example log](evidence/offline-example.log).

For a real run, set `LLM_MODEL`, `LLM_API_KEY`, and optionally `LLM_BASE_URL`.

On 2026-09-24, the user ran the example with Gemini 3.1 Flash Lite. The [live log](evidence/gemini-3.1-flash-lite-live.log) confirms native notes/history calls, one context reset, the correct answer, and notes recovered after closing/reopening the conversation. The SDK reported an estimated cost of $0.007659; this is not a provider invoice. One 503 recovered through retry. The model batched reset/read/search together, so the search occurred before the actual reset and all matches were still in the active view. This smoke run does **not** establish retrieval of evicted history or a process restart.

## Strict post-reset functional test

The [strict harness](strict_notes_retrieval.py) separates reset from retrieval, with independent random note/history values. It checks committed notes, source-event eviction, absence of both values from the retained LLM messages, and actual post-reset notes read plus search/read of the original hidden history event. The history value lies beyond the search snippet. A final exact JSON answer is required in addition to the tool evidence.

```sh
LLM_MODEL=gemini/gemini-3.5-flash-lite uv run .pr/strict_notes_retrieval.py
```

The key comes from `LLM_API_KEY`. Live chat requests are spaced at least six seconds apart, including SDK retries, with a hard limit of 12 dispatch attempts. Each completion permits three total SDK attempts, with 20- then 40-second retry waits; nested LiteLLM retries and model fallbacks are disabled. This is a single-process request cap, not account-wide rate control or a billing guarantee. The separate comparison harness now has a shared pacing gate across agent and condenser calls; its protocol and validation are documented below.

Offline validation used a real `LocalConversation` and scripted `TestLLM` responses:

- [Normal protocol](evidence/strict-notes/run-a0g3aybp/report.json): all eight checks passed, exit 0.
- [Skip reset](evidence/strict-notes/run-9z8xv403/report.json): rejected at `source_events_hidden`, exit 1 as intended.
- [Skip retrieval](evidence/strict-notes/run-nv0w4gel/report.json): exact final answer was correct, but missing tool evidence caused rejection, exit 1 as intended.
- Pre-commit checks for the harness passed. Initial sandboxed attempts failed SDK initialization with `PermissionError`; the above validation completed with cache access enabled.

Each invocation retains `report.json`, `view-before-retrieval.json`, and persisted conversation events in a unique `.pr/evidence/strict-notes/run-*` directory. These are temporary PR artifacts. Offline success is harness validation only; the subsequent live result is documented below. Append, pagination, automatic reset/reminders, disk failure, and statistical model-quality comparison are outside this focused test.

The user's [first strict live attempt](evidence/strict-notes/run-62k91b0m/report.json) exhausted the previous two-attempt policy on the first completion: the event log records `LLMServiceUnavailableError` with Gemini HTTP 503, zero completed checks, and zero reported usage. Its original report remains unchanged. This is an inconclusive functional test caused by provider availability.

The harness now unwraps the current `ConversationRunError.original_exception` and labels only service-unavailable, rate-limit, and timeout failures as `INCONCLUSIVE` (exit 2), provided no acceptance check has failed. Assertion/tool failures remain `FAIL` (exit 1), while a complete pass exits 0. Offline regression checks with `LITELLM_LOCAL_MODEL_COST_MAP=True` verified [provider unavailability](evidence/strict-notes/run-z6dm7k0b/report.json), the [successful flow](evidence/strict-notes/run-uvcmw5f0/report.json), and [missing retrieval despite a correct answer](evidence/strict-notes/run-5pu5q28d/report.json). Pre-commit passed after the change; no additional live API calls were made by the agent.

### Verified live PASS: Gemini 3.5 Flash Lite

The user ran the strict harness with `gemini/gemini-3.5-flash-lite` on 2026-09-24. The [report](evidence/strict-notes/run-528nrz0g/report.json) records **PASS (8/8 checks)**, `offline=false`, seven provider attempts, `finished`, and no runtime errors. The [console log](evidence/gemini-3.5-flash-lite-strict.log), [retained-context snapshot](evidence/strict-notes/run-528nrz0g/view-before-retrieval.json), and persisted events were independently audited:

- The event chain contains 21 events with unique IDs and contiguous parent references.
- The reset removes eight events, including both original fact messages. Neither answer appears in the three retained LLM messages.
- Reset occurred at 00:38:06, followed by notes read at 00:38:12, history search at 00:38:18, original-history read at 00:38:24, and the final answer at 00:38:30 (local log times).
- Notes read returns the committed notes version and exact value. History search identifies the original event with `in_active_view=false`; its snippet omits the answer. Reading that same hidden event returns the complete 1,149-character record and the history answer.
- The final JSON matches both random values exactly. No observation has `is_error=true`.

**Metrics limitation:** this report's all-zero token/cost fields are invalid usage evidence. The SDK's automatic LLM discovery excludes subclasses, so the harness's `PacedLLM` was not registered with conversation statistics. The harness now explicitly subscribes the statistics handler and registers its LLM before execution. The original live report is preserved without inventing missing usage. The independent request counter and all functional assertions remain valid; unknown model pricing can still prevent a reliable cost estimate even after registration. This single live run demonstrates functional retrieval, not a comparative accuracy or token-saving result.

## Lightweight paired evaluation

The [evaluation script](eval_notes_retrieval.py) compares summary, notes only, and notes plus history retrieval: three synthetic scenarios (exact identifier, superseded fact, compound constraints), two repeats, 18 trials. Condition order rotates across scenarios/repeats. All arms use the same model and a 5% storage guard.

The finalized [offline report](evidence/comparison-v2-verified/report.json) has **18/18 eligible completed trials, six complete paired groups, and 18 expected matches**. It was deliberately stopped after three trials, then resumed for the remaining 15; completed trial artifacts were not replaced. Every note-arm view excludes all prior raw message/action/observation IDs after a neutral reset boundary. Every trial saves its actual retained LLM messages. The earlier [intermediate report](evidence/comparison-v2-offline/report.json) predates the correction that excludes evaluation snapshots from storage counts; use the finalized report for byte accounting. See the [final offline log](evidence/comparison-v2-verified.log).

These are scripted `TestLLM` runs: `quality_evidence=false`, zero provider requests, and `usage_recorded=false`. Their exact-match results validate the harness, not real model accuracy or efficiency.

### Verified live paired evaluation: Gemini 3.5 Flash Lite

The user's [live report](evidence/notes-comparison/report.json) is **COMPLETE: 18/18 eligible trials, six complete paired groups**, with `offline=false` and no trial error chains. Every source fact was absent from the retained view before recall, and all 12 notes-arm trials also removed earlier raw messages, actions, and observations. The recorded results are:

| Strategy | Exact matches | Input tokens | Output tokens | Provider attempts |
|---|---:|---:|---:|---:|
| Summary | 6/6 | 328,621 | 2,634 | 48 |
| Notes only | 6/6 | 447,815 | 2,575 | 60 |
| Notes plus history | 6/6 | 426,310 | 2,071 | 54 |
| Total | 18/18 | 1,202,746 | 7,280 | 162 |

These token totals include both agent and summarization calls. The SDK estimated **$0.3790238** for the run; this is a local pricing estimate, **not a provider bill** or proof that the account incurred that charge. Unlike the earlier strict report's invalid zero usage, all 18 comparison trials recorded usage.

An independent audit of the six notes-plus-history event logs found **one history search and zero history reads**; every trial read its notes. That search returned hidden history, while the other five trials answered from notes alone. There were no error observations in those six trials. All three strategies achieved the same accuracy on these small synthetic cases, and the notes arms used more total input tokens than the configured summary arm. The results establish successful execution under these conditions; they do **not** demonstrate a history-retrieval advantage, general model-quality superiority, or token savings.

From the repository root, in the terminal where the key is already exported:

```sh
LLM_MODEL=gemini/gemini-3.5-flash-lite uv run .pr/eval_notes_retrieval.py
```

The script reads `LLM_API_KEY` or `GEMINI_API_KEY`; no key belongs in a report or command argument. Optional `--llm-config` accepts additional LLM constructor options. The default output is `.pr/evidence/notes-comparison/report.json` and each attempt retains events and its own report under the associated runs directory. The completed live report is preserved: a fresh run requires a different `--output` and `--artifacts-dir`; `--resume` on a completed report performs no additional trials.

Controls and interpretation:

- Agent and summary calls share a six-second dispatch interval and **250 total provider attempts**, including SDK and condenser retries. The count is persisted before dispatch and continues across `--resume`; it is not an account-wide or cross-process quota. Nested LiteLLM retries and model fallback are disabled.
- Normal API unavailability, rate limits, timeouts, request/budget limits, and Ctrl+C produce a partial report. Repeat the same command with `--resume` to skip finalized trials and retry inconclusive trials in new conversation directories. Prior attempt artifacts and request usage remain recorded. Request and USD caps cannot be increased through resume. `--max-trials 3` offers a three-trial pilot without changing the planned 18-trial protocol.
- A hard process crash that leaves an unfinished `active_trial` blocks automatic resume as `PAUSED_UNCONFIRMED_USAGE`: the in-flight charge cannot be reconciled safely. Inspect its artifacts before starting a separately budgeted evaluation. The original report and request count remain intact.
- Exit 0 means the evaluation completed, not that every answer was correct. Exit 1 indicates a harness/runtime failure; exit 2 means paused or configuration rejected. Read each condition's `exact_match_rate`, excluded samples, and error chains.
- Accuracy and aggregate token/storage metrics use only complete eligible triples with the same scenario/repeat. Provider failures are inconclusive, not wrong answers. Per-attempt records still include failed attempt overhead.
- Summary retains roughly half the recent events; the notes arms keep the system message/latest user boundary. This compares configured end-to-end strategies, **not matched retained-token budgets**. View event counts/serialized message bytes and separate agent/condenser usage make the difference explicit.
- Source fact IDs must be absent before recall; note arms additionally require all earlier raw messages/actions/observations to be absent, preventing a retained notes-write payload from leaking answers. The summary's synthesized memory is allowed to contain answers.
- Retrieval counts, returned UTF-8 bytes, errors, and hidden-history hits are measured after reset. Actual history use is reported but is not forced: a model may answer entirely from notes. Returned bytes do not measure isolated retrieval CPU time. Elapsed time includes API latency, pacing, and retries.
- Storage counts cover only persisted conversation files under each trial's `state/`, excluding evaluator reports/snapshots. No event files are deleted.
- Both agent and condenser LLMs are explicitly registered for usage statistics. A separate blocked-network mock-provider check verified seven agent calls plus one summary call: shared request count 8, input 808/output 136, and nonzero SDK cost. A summary-provider failure retained its typed cause through condensation and was classified `INCONCLUSIVE`; all condenser retries hit the shared gate.
- The default `--budget 1` is a **soft SDK estimated-cost limit**, checked before dispatch; a response may cross it. Unknown model pricing leaves `estimated_cost_usd=null`, not a claim of free or zero usage. The request cap still applies. SDK estimates are not provider invoices and do not determine a Google project's billing tier.
- Six samples per arm support descriptive results only. The completed live results establish neither statistical significance nor a history-retrieval advantage. Eval-only `NotesOnlyIndex`/`NotesOnlyReminder` kinds require importing this script to deserialize; resume does not reopen unfinished conversations.

Pre-commit passed for the comparison script and shared [transport helper](eval_transport.py). The helper's blocked-network checks verified shared accounting across two LLMs, SDK retry counting, the hard cap, and persistence-callback failure preventing dispatch.

## Supplementary memory challenges

Run the new suite from the terminal where the Gemini key is exported:

```sh
LLM_MODEL=gemini/gemini-3.5-flash-lite uv run .pr/eval_notes_retrieval.py --suite challenge
```

This defaults to one repeat of three scenarios across three strategies (nine trials), seed `4916`, a shared **180-attempt** request cap, and six-second dispatch spacing. The cap includes agent/condenser retries and remains per report, not account-wide. The usual soft `--budget 1` estimate applies. Output goes to `.pr/evidence/notes-challenge/report.json`; API interruptions can resume with the same command plus `--resume`, subject to the existing request/cost and unfinished-process checks.

The user's [live challenge report](evidence/notes-challenge/report.json) currently records **PAUSED_INCONCLUSIVE: 0/9 completed trials, zero complete paired groups, eight provider attempts**. The first `summary`/`multi_reset` trial stopped in `window_1_distractors` after Gemini HTTP 503 (`LLMServiceUnavailableError`), before any reset. The report is retained with its attempted usage. This provider failure is not scored as an incorrect answer; there is no live challenge accuracy or strategy comparison to report yet.

[Cases](eval_memory_cases.py) use a local seeded RNG. Every strategy receives identical facts and questions for a given seed/repeat, and each trial saves `case.json` and its SHA-256. Changing the seed while resuming is rejected.

| Scenario | Protocol | Evidence |
|---|---|---|
| `multi_reset` | Three windows, each with two facts; an initial opaque release token must survive while later windows correct other fields. Ask for the final four-field state after the third reset. | Per-window reset IDs, cumulative source eviction, retained-message snapshots, metrics and persisted bytes. |
| `delayed_question` | Thirty equally presented records in two messages. Reveal which record is requested only after the reset. | Question event ID occurs after reset; exact values, notes contents and actual recovery calls are assessed separately. |
| `long_history` | Eighty entries in a 17,269-character manifest; the selected value lies beyond offset 8,000. A front index and neutral separation prevent the first title/ID search from exposing it. | Search snippets, actual read offsets, hidden original-event identity and returned target text. |

The suite permits natural strategy choices: an answer recovered from notes or a clever search still counts as correct. [Retrieval evidence](eval_retrieval_evidence.py) is scored separately. `full_read_evidence` specifically means a successful read of a hidden original source returned the target value; it does not mean every character was necessarily read. `search_to_read_evidence` also requires the search result to exist before the read action was selected, so a preselected same-batch read is not misreported as a causal search/read chain.

**Native summary limitation:** the current `LLMSummarizingCondenser` builds its input with `str(event)`, and `MessageEvent.__str__` truncates message text to a 500-character preview. Long original records therefore may not reach its summarization LLM in full. Each new report records this limitation and whether the original source preview contains the target. A difference in challenge scores must not be generalized to full-input summarization algorithms. We retain the SDK's production behavior for this comparison. The strategies also retain different amounts of context.

The [final offline challenge report](evidence/notes-challenge-verified/report.json) completed **9/9 eligible scripted trials**, with three matched groups and zero provider attempts. Each multi-reset trial completed three actual resets; the long-history retrieval trial searched the hidden original source, then read offsets 0, 8,000 and 16,000. The second page contained the target, while the search snippet did not. Fixtures matched across conditions. All outputs remain `quality_evidence=false`: scripted answers/summaries validate the workflow, not model accuracy. See the [log](evidence/notes-challenge-verified.log).

The [earlier challenge run](evidence/notes-challenge-offline/report.json) was stopped after three trials and resumed for the remaining six, confirming that completed trials are not repeated. The [baseline regression](evidence/notes-baseline-regression/report.json) completed all nine original scenario/strategy combinations after the extension.

Two [negative tests](test_memory_challenges.py) passed with real `LocalConversation` and `TestLLM`: a correct final JSON without history read never claims full-read evidence; this holds both when search omits the answer and when a short search snippet already contains it.

```sh
LITELLM_LOCAL_MODEL_COST_MAP=True uv run pytest .pr/test_memory_challenges.py -q
```

All edited Python files passed their individual pre-commit hooks. Total token/cost metrics cover the full trial; `retrieval_overhead` and `storage_growth_after_reset_bytes` specifically cover the period after the **final** reset. Earlier-window activity remains represented by total metrics/tool calls and the per-window checkpoints.

## Limits and review follow-up

- 5% protection is an admission policy, not an OS quota. Another process, an in-flight command, or opaque custom Agent/ACP execution can exhaust space before the next SDK boundary. Uncommitted output is not guaranteed to survive process exit.
- Normal low-space recovery preserves the active branch and execution status. Actual write-failure recovery requires review and leaves execution paused. It never guesses a branch or replays an uncertain tool.
- A filesystem that cannot be probed fails closed. Opening a protected conversation also checks capacity; existing event files remain available for separate read-only inspection.
- Notes append stores only its delta, but action parameters and retrieval observations still consume ordinary event-log space. No total disk quota, TTL, archive, or garbage collector is included.
- Changes to agent prompts/decisions should receive the repository's `integration-test` label when a PR is opened. The targeted real-provider tests above have run; the repository's integration/benchmark evaluation workflows have not been run for this PR.
- Documentation changes are published in [OpenHands/docs#837](https://github.com/OpenHands/docs/pull/837), commit `bf660fa4123d7a1ade2500d6492b6ce277545565`; [docs.patch](docs.patch) preserves the companion diff. Both draft descriptions cross-reference the other PR. Merge the SDK implementation before its documentation.
- Documentation validation passed: Mintlify strict build and broken-link checks, local page preview, all three Python snippets parsed, setup/read-only inspection executed with TestLLM and no model calls, and the TypeScript recovery snippet passed strict type checking. The guide's read-only cold-recovery example loaded 17 events with all 20 persisted source files' hashes and modification times unchanged.
- `.pr/` contains temporary review evidence and must not ship in the merged tree. The repository cleanup workflow exists; fork PRs should remove these artifacts before merge.

## Review artifact scope

The committed review bundle contains the referenced reports, selected context snapshots, logs, design page, documentation patch, and reproducible scripts. Bulk raw event/state files remain in the author's local evaluation directories and are not part of the bundle. Absolute `artifacts` paths inside original reports record their run locations; they are not links to published files. Reports are retained as generated, including unavailable or invalid usage where documented above.

## Publication preflight

- Before publication, all 126 local documentation links resolved to the implementation or selected review artifacts. Published source links now use the explicit implementation revision and evidence links use the public feature branch. High-confidence credential scanning found no matches in the selected artifacts; raw conversation state is excluded.
- The PR description preserves the template's human-only section exactly. The local description validator reports its sole pending error: `Add a short human-written note between HUMAN: and AGENT:`. The author must fill this themselves before marking the PR ready; the description-check workflow skips draft PRs.
- Both PRs are published as drafts from the public `cbinhan` forks, with reciprocal links and a public design preview. The account has read-only upstream permissions: GitHub rejected adding `integration-test` (`AddLabelsToLabelable`), so the SDK description requests a maintainer to add it. The HUMAN field remains reserved for the author before marking ready.

## Verification after merging current upstream

GitHub initially detected a conflict with upstream `e21d77673b738f056676044600c4ad81c5a575c8`. Merge commit `0934c600d9e8bbf289f63e3324ec14f63bac1223` retains both the upstream model-routing tool and the three optional memory tools; default built-ins remain unchanged. `make build` refreshed the upstream dependency lock, and the conflict file passed pre-commit.

| Current merged-tree check | Result |
|---|---|
| Settings, model routing/switching, memory tools, agent reset, notes condenser | [213 passed](evidence/post-merge-sdk-tools.log) |
| Storage IO/recovery, critic admission, EventLog | [55 passed](evidence/post-merge-storage.log) |
| Server API, local secret resolver, meta-profile routes, storage, real HTTP recovery | [73 passed](evidence/post-merge-server.log) |
| Fresh OpenAPI export and quality gate | [Passed: 103 allowlisted locations](evidence/post-merge-server.log) |
| TypeScript dependency install, build, and full suite | [338 passed across 21 files](evidence/post-merge-typescript.log) |

The three Python/server selections total **341 passing tests**. These runs are separate from the original 1,970-test regression above; overlapping older runs are not added as unique coverage. The OpenAPI count grows from 102 to 103 because upstream added the model-routing tool's opaque metadata entry. These post-merge checks do not call a real LLM API; the earlier live reports remain evidence from the original implementation revision.

The new upstream optional model router reads recent messages from the EventLog for its classifier. It is disabled by default and was not part of the memory evaluation; active-view eviction assertions should not be generalized to every optional auxiliary model path.


## Review follow-up: settings entry point and upstream conflict

The review at `1e2dc4e46` identified a real settings-path defect: selecting
`NotesRetrievalCondenserSettings(enabled=True)` did not supply its required tools.
Fix `4b06dc157` makes `OpenHandsAgentSettings.create_agent()` add missing
`ContextNotesTool` and `ConversationHistoryTool` specs when notes retrieval is
selected and enabled. Existing explicit specs are not duplicated; settings are
not mutated; disabled notes settings do not add tools. Direct `Agent(...)`
construction still requires explicit tools. `NewContextTool` remains optional.

The reproduction uses the supported `create_agent()` -> `Conversation` ->
`send_message()` -> `run()` path with real registered default tools and TestLLM.
It covers omitted, empty, partial, and complete tool lists, then exercises notes
write/read and history search. This is an offline runtime regression, not a live
provider evaluation or a deployed Agent Server/GUI test. On this macOS host the
terminal falls back to the supported subprocess implementation because tmux is
absent; no terminal command is invoked by the test.

| Validation | Result and evidence |
| --- | --- |
| Before fix, same four settings runtime cases on `1e2dc4e46` | [3 failed with missing required tools; explicit tools passed](evidence/review-settings-before.log) |
| Settings, notes agent and condenser after fix | [160 passed](evidence/review-settings-after.log) |
| After merging upstream `1612a78ef`, same suites plus storage guards, local recovery and auth errors/retry | [202 passed](evidence/review-merged-regression.log) |
| Offline example on merged implementation | [One context reset and successful notes restore from persisted events](evidence/review-offline-example.log) |

Merge `5b1b79010d9cc7d85723a87af3d8b76d88d28804` preserves both the
`StorageSafetyError` branch and upstream `LLMAuthenticationError` branch in sync
and async conversation loops. The regression suite covers both behaviors.
Pre-commit passed for the settings implementation, tests and conflict file.

No new paid/model API requests were made for this fix. The earlier live-evaluation
limitations above remain unchanged: the paired comparison does not establish a
retrieval advantage. GitHub fork workflows still require maintainer approval;
local results are not presented as current-head CI results.
