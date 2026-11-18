## Conflicting attempts

- S04.persist-events: fail at 2026-11-11T09:08:40+00:00, pass at 2026-11-11T09:22:17+00:00, pass at 2026-11-11T09:29:38+00:00
- S05.event-log-recovery: pass at 2026-11-11T09:06:44+00:00, fail at 2026-11-11T09:08:40+00:00, pass at 2026-11-11T09:29:38+00:00
- S06.condenser-resume: fail at 2026-11-11T09:13:15+00:00, pass at 2026-11-11T09:16:43+00:00
- S06.context-overflow-request: fail at 2026-11-11T09:13:15+00:00, pass at 2026-11-11T09:16:43+00:00
- S06.context-skills: fail at 2026-11-11T09:13:15+00:00, pass at 2026-11-11T09:16:43+00:00
- S06.context-suffixes: fail at 2026-11-11T09:13:15+00:00, pass at 2026-11-11T09:16:43+00:00
- S06.hard-reset: fail at 2026-11-11T09:13:15+00:00, pass at 2026-11-11T09:16:43+00:00
- S06.memory-load: fail at 2026-11-11T09:13:15+00:00, pass at 2026-11-11T09:16:43+00:00
- S06.memory-write-path: fail at 2026-11-11T09:13:15+00:00, pass at 2026-11-11T09:16:43+00:00
- S06.soft-vs-hard: fail at 2026-11-11T09:13:15+00:00, pass at 2026-11-11T09:16:43+00:00
- S06.summarizing-size: fail at 2026-11-11T09:13:15+00:00, pass at 2026-11-11T09:16:43+00:00
- S06.summarizing-tokens: fail at 2026-11-11T09:13:15+00:00, pass at 2026-11-11T09:16:43+00:00
- S06.summary-llm-streaming: fail at 2026-11-11T09:13:15+00:00, pass at 2026-11-11T09:16:43+00:00
- S11.builtin-agents: fail at 2026-11-11T09:14:09+00:00, pass at 2026-11-11T09:18:21+00:00
- S11.definition-factory: fail at 2026-11-11T09:14:09+00:00, pass at 2026-11-11T09:18:21+00:00
- S11.delegation-visualizer: fail at 2026-11-11T09:14:09+00:00, pass at 2026-11-11T09:18:21+00:00
- S11.register-programmatic: fail at 2026-11-11T09:14:09+00:00, pass at 2026-11-11T09:18:21+00:00
- S11.task-metrics: fail at 2026-11-11T09:14:09+00:00, pass at 2026-11-11T09:18:21+00:00
- S11.task-resume: fail at 2026-11-11T09:14:09+00:00, pass at 2026-11-11T09:18:21+00:00
- S11.task-run: fail at 2026-11-11T09:14:09+00:00, pass at 2026-11-11T09:18:21+00:00
- S21.auth-selection: fail at 2026-11-11T08:27:22+00:00, pass at 2026-11-11T08:39:16+00:00
- S21.file-secrets: fail at 2026-11-11T08:27:22+00:00, pass at 2026-11-11T08:39:16+00:00
- S21.model-selection: fail at 2026-11-11T08:27:22+00:00, pass at 2026-11-11T08:39:16+00:00
- S21.secrets-env: fail at 2026-11-11T08:27:22+00:00, pass at 2026-11-11T08:39:16+00:00

## Checks

| Feature | Result | Revision | Note | Transcript |
|---|---|---|---|---|
| S01.stuck-recover | fail | 5ff99f1b6ca2 | bullet 7: pass; bullet 8: fail (block 1 exited 1) | <scratch>/sdk/S01/transcripts/S01-agent-loop-08.log |
| S02.no-response-retry | fail | 391b5546481d | bullet 9: fail (block 1 exited 1) | <scratch>/sdk/S02/transcripts/S02-llm-core-09.log |
| S03.registry-stats-wiring | fail | 391b5546481d | bullet 1: pass; bullet 2: fail (block 1 exited 1); bullet 15: pass | <scratch>/sdk/S03/transcripts/S03-llm-routing-profiles-02.log |
| S04.interactive-terminal | fail | 391b5546481d | bullet 19: fail (block 1 exited 1); bullet 25: pass | <scratch>/sdk/S04/transcripts/S04-local-conversation-19.log |
| S07.available-skills-prompt | fail | e776f878876e | bullet 2: pass; bullet 3: fail (block 1 exited 1); bullet 10: pass; bullet 15: pass | <scratch>/sdk/S07/transcripts/S07-skills-03.log |
| S07.installed-lifecycle | fail | e776f878876e | bullet 6: pass; bullet 13: fail (block 1 exited 1) | <scratch>/sdk/S07/transcripts/S07-skills-13.log |
| S08.secret-expansion | fail | edff6711505a | bullet 3: fail (block 1 exited 1) | <scratch>/sdk/S08-nokey/transcripts/S08-plugins-marketplace-03.log |
| S09.session-recovery | fail | edff6711505a | bullet 10: pass; bullet 11: fail (block 1 exited 1) | <scratch>/sdk/S09/transcripts/S09-mcp-11.log |
| S11.factory-info | fail | c5f588f089d0 | bullet 5: pass; bullet 6: fail (block 1 exited 1) | <scratch>/sdk/S11-nokey/transcripts/S11-subagents-delegation-06.log |
| S11.task-observability | fail | c5f588f089d0 | bullet 2: fail (block 1 exited 1); bullet 3: blocked (bullet is labeled blocked) | <scratch>/sdk/S11-nokey/transcripts/S11-subagents-delegation-02.log |
| S14.terminal-backend-select | fail | c707d3737e8d | bullet 2: pass; bullet 3: pass; bullet 6: fail (block 1 exited 1) | <scratch>/sdk/S14-nokey/transcripts/S14-core-tools-06.log |
| S14.terminal-input-encoding | fail | c707d3737e8d | bullet 2: pass; bullet 5: fail (block 1 exited 1) | <scratch>/sdk/S14-nokey/transcripts/S14-core-tools-05.log |
| S14.terminal-reset | fail | c707d3737e8d | bullet 2: pass; bullet 3: pass; bullet 6: fail (block 1 exited 1) | <scratch>/sdk/S14-nokey/transcripts/S14-core-tools-06.log |
| S15.action-routing | fail | c707d3737e8d | bullet 1: pass; bullet 5: fail (block 1 exited 1) | <scratch>/sdk/S15-nokey/transcripts/S15-browser-05.log |
| S15.cleanup | fail | c707d3737e8d | bullet 1: pass; bullet 3: pass; bullet 5: fail (block 1 exited 1) | <scratch>/sdk/S15-nokey/transcripts/S15-browser-05.log |
| S15.session-recovery | fail | c707d3737e8d | bullet 5: fail (block 1 exited 1) | <scratch>/sdk/S15-nokey/transcripts/S15-browser-05.log |
| S18.workspace-factory | fail | 1ecd2de9dc6b | bullet 1: pass; bullet 3: fail (block 1 exited 1) | <scratch>/sdk/S18-nokey/transcripts/S18-workspace-backends-03.log |
| S21.ask-agent | fail | 07e3bb94b792 | bullet 2: fail (block 1 exited 1); bullet 14: blocked (bullet is labeled blocked) | <scratch>/sdk/S21-effort/transcripts/S21-acp-agent-02.log |
| S21.timeouts-retry | fail | 07e3bb94b792 | bullet 5: pass; bullet 6: fail (block 1 exited 1) | <scratch>/sdk/S21-effort/transcripts/S21-acp-agent-06.log |
| S05.token-event | blocked | e776f878876e | bullet 9: blocked (bullet is labeled blocked) |  |
| S14.terminal-windows | blocked | c707d3737e8d | bullet 7: blocked (bullet is labeled blocked) |  |
| S15.chromium-detection | blocked | c707d3737e8d | bullet 2: blocked (block 1 is blocked (exit 3: a missing prerequisite)) | <scratch>/sdk/S15-nokey/transcripts/S15-browser-02.log |
| S15.degrade-on-failure | blocked | c707d3737e8d | bullet 2: blocked (block 1 is blocked (exit 3: a missing prerequisite)) | <scratch>/sdk/S15-nokey/transcripts/S15-browser-02.log |
| S15.recording-storage | blocked | c707d3737e8d | bullet 6: blocked (bullet is labeled blocked); bullet 9: blocked (bullet is labeled blocked) |  |
| S18.agent-sandbox | blocked | 1ecd2de9dc6b | bullet 11: blocked (bullet is labeled blocked) |  |
| S18.api-remote-lifecycle | blocked | 1ecd2de9dc6b | bullet 10: blocked (bullet is labeled blocked) |  |
| S18.api-remote-start | blocked | 1ecd2de9dc6b | bullet 10: blocked (bullet is labeled blocked) |  |
| S18.apptainer | blocked | 1ecd2de9dc6b | bullet 11: blocked (bullet is labeled blocked) |  |
| S18.cloud-sandbox | blocked | 1ecd2de9dc6b | bullet 10: blocked (bullet is labeled blocked) |  |
| S18.docker-dev-build | blocked | 1ecd2de9dc6b | bullet 9: blocked (bullet is labeled blocked) |  |
| S18.docker-lifecycle | blocked | 1ecd2de9dc6b | bullet 9: blocked (bullet is labeled blocked) |  |
| S18.docker-start | blocked | 1ecd2de9dc6b | bullet 9: blocked (bullet is labeled blocked) |  |
| S21.live-turn | blocked | 07e3bb94b792 | bullet 14: blocked (bullet is labeled blocked) |  |
| S01.ask-agent | pass | 5ff99f1b6ca2 | bullet 17: pass; bullet 20: pass | <scratch>/sdk/S01/transcripts/S01-agent-loop-17.log |
| S01.async-step | pass | 5ff99f1b6ca2 | bullet 15: pass | <scratch>/sdk/S01/transcripts/S01-agent-loop-15.log |
| S01.content-policy | pass | 5ff99f1b6ca2 | bullet 3: pass | <scratch>/sdk/S01/transcripts/S01-agent-loop-03.log |
| S01.context-overflow | pass | 5ff99f1b6ca2 | bullet 19: pass; bullet 23: pass | <scratch>/sdk/S01/transcripts/S01-agent-loop-19.log |
| S01.empty-reply-nudge | pass | 5ff99f1b6ca2 | bullet 3: pass; bullet 4: pass | <scratch>/sdk/S01/transcripts/S01-agent-loop-03.log |
| S01.finish-truncates-batch | pass | 5ff99f1b6ca2 | bullet 11: pass | <scratch>/sdk/S01/transcripts/S01-agent-loop-11.log |
| S01.image-input | pass | 5ff99f1b6ca2 | bullet 16: pass; bullet 24: blocked (bullet is labeled blocked) | <scratch>/sdk/S01/transcripts/S01-agent-loop-16.log |
| S01.image-non-vision | pass | 5ff99f1b6ca2 | bullet 16: pass | <scratch>/sdk/S01/transcripts/S01-agent-loop-16.log |
| S01.iterative-refinement | pass | 5ff99f1b6ca2 | bullet 22: pass | <scratch>/sdk/S01/transcripts/S01-agent-loop-22.log |
| S01.malformed-tool-call | pass | 5ff99f1b6ca2 | bullet 3: pass | <scratch>/sdk/S01/transcripts/S01-agent-loop-03.log |
| S01.max-budget | pass | 5ff99f1b6ca2 | bullet 6: pass | <scratch>/sdk/S01/transcripts/S01-agent-loop-06.log |
| S01.max-iterations | pass | 5ff99f1b6ca2 | bullet 5: pass | <scratch>/sdk/S01/transcripts/S01-agent-loop-05.log |
| S01.message-while-running | pass | 5ff99f1b6ca2 | bullet 14: pass; bullet 15: pass; bullet 21: pass | <scratch>/sdk/S01/transcripts/S01-agent-loop-14.log |
| S01.parallel-resource-locks | pass | 5ff99f1b6ca2 | bullet 12: pass | <scratch>/sdk/S01/transcripts/S01-agent-loop-12.log |
| S01.parallel-tools | pass | 5ff99f1b6ca2 | bullet 11: pass; bullet 21: pass | <scratch>/sdk/S01/transcripts/S01-agent-loop-11.log |
| S01.response-dispatch | pass | 5ff99f1b6ca2 | bullet 1: pass; bullet 2: pass; bullet 20: pass | <scratch>/sdk/S01/transcripts/S01-agent-loop-01.log |
| S01.run-to-finish | pass | 5ff99f1b6ca2 | bullet 1: pass; bullet 20: pass; bullet 23: pass | <scratch>/sdk/S01/transcripts/S01-agent-loop-01.log |
| S01.structured-output | pass | 391b5546481d | bullet 18: pass; bullet 21: pass | <scratch>/sdk/s01fix/r1/transcripts/S01-agent-loop-18.log |
| S01.stuck-detect | pass | 5ff99f1b6ca2 | bullet 7: pass; bullet 21: pass | <scratch>/sdk/S01/transcripts/S01-agent-loop-07.log |
| S01.stuck-nudge | pass | 5ff99f1b6ca2 | bullet 10: pass | <scratch>/sdk/S01/transcripts/S01-agent-loop-10.log |
| S01.stuck-thresholds | pass | 5ff99f1b6ca2 | bullet 9: pass | <scratch>/sdk/S01/transcripts/S01-agent-loop-09.log |
| S01.tool-exception | pass | 5ff99f1b6ca2 | bullet 13: pass | <scratch>/sdk/S01/transcripts/S01-agent-loop-13.log |
| S01.unknown-tool | pass | 5ff99f1b6ca2 | bullet 3: pass | <scratch>/sdk/S01/transcripts/S01-agent-loop-03.log |
| S02.anthropic-thinking | pass | 391b5546481d | bullet 5: pass; bullet 20: blocked (bullet is labeled blocked) | <scratch>/sdk/S02/transcripts/S02-llm-core-05.log |
| S02.api-mode | pass | 391b5546481d | bullet 1: pass; bullet 2: pass; bullet 17: pass | <scratch>/sdk/S02/transcripts/S02-llm-core-01.log |
| S02.completion | pass | 391b5546481d | bullet 1: pass; bullet 17: pass | <scratch>/sdk/S02/transcripts/S02-llm-core-01.log |
| S02.context-window-guard | pass | 391b5546481d | bullet 12: pass | <scratch>/sdk/S02/transcripts/S02-llm-core-12.log |
| S02.conversation-stats | pass | 391b5546481d | bullet 16: pass; bullet 18: pass | <scratch>/sdk/S02/transcripts/S02-llm-core-16.log |
| S02.error-mapping | pass | e776f878876e | bullet 7: pass | <scratch>/sdk/S05/transcripts/S05-events-07.log |
| S02.image-inline | pass | 391b5546481d | bullet 14: pass | <scratch>/sdk/S02/transcripts/S02-llm-core-14.log |
| S02.log-completions | pass | 391b5546481d | bullet 1: pass | <scratch>/sdk/S02/transcripts/S02-llm-core-01.log |
| S02.message-formatting | pass | 391b5546481d | bullet 13: pass; bullet 18: pass | <scratch>/sdk/S02/transcripts/S02-llm-core-13.log |
| S02.metrics | pass | 391b5546481d | bullet 1: pass; bullet 16: pass; bullet 17: pass; bullet 18: pass | <scratch>/sdk/S02/transcripts/S02-llm-core-01.log |
| S02.model-features | pass | 391b5546481d | bullet 6: pass | <scratch>/sdk/S02/transcripts/S02-llm-core-06.log |
| S02.native-tool-calling | pass | 391b5546481d | bullet 1: pass; bullet 7: pass | <scratch>/sdk/S02/transcripts/S02-llm-core-01.log |
| S02.pricing-override | pass | 391b5546481d | bullet 1: pass | <scratch>/sdk/S02/transcripts/S02-llm-core-01.log |
| S02.prompt-caching | pass | 391b5546481d | bullet 5: pass; bullet 11: pass | <scratch>/sdk/S02/transcripts/S02-llm-core-05.log |
| S02.prompted-tool-calling | pass | 391b5546481d | bullet 7: pass | <scratch>/sdk/S02/transcripts/S02-llm-core-07.log |
| S02.reasoning-content | pass | 391b5546481d | bullet 1: pass; bullet 3: pass | <scratch>/sdk/S02/transcripts/S02-llm-core-01.log |
| S02.reasoning-options | pass | 391b5546481d | bullet 6: pass | <scratch>/sdk/S02/transcripts/S02-llm-core-06.log |
| S02.responses-reasoning | pass | 391b5546481d | bullet 2: pass; bullet 20: blocked (bullet is labeled blocked) | <scratch>/sdk/S02/transcripts/S02-llm-core-02.log |
| S02.retries | pass | 391b5546481d | bullet 8: pass | <scratch>/sdk/S02/transcripts/S02-llm-core-08.log |
| S02.serialization | pass | 391b5546481d | bullet 15: pass | <scratch>/sdk/S02/transcripts/S02-llm-core-15.log |
| S02.stream-no-callback | pass | 391b5546481d | bullet 3: pass | <scratch>/sdk/S02/transcripts/S02-llm-core-03.log |
| S02.streaming | pass | 391b5546481d | bullet 3: pass; bullet 17: pass; bullet 18: pass | <scratch>/sdk/S02/transcripts/S02-llm-core-03.log |
| S02.timeouts | pass | 391b5546481d | bullet 4: pass | <scratch>/sdk/S02/transcripts/S02-llm-core-04.log |
| S02.token-count | pass | 391b5546481d | bullet 12: pass | <scratch>/sdk/S02/transcripts/S02-llm-core-12.log |
| S03.agent-llm-discovery | pass | 391b5546481d | bullet 3: pass | <scratch>/sdk/S03/transcripts/S03-llm-routing-profiles-03.log |
| S03.fallback-metrics | pass | 391b5546481d | bullet 6: pass | <scratch>/sdk/S03/transcripts/S03-llm-routing-profiles-06.log |
| S03.fallback-profile-resolution | pass | 391b5546481d | bullet 6: pass | <scratch>/sdk/S03/transcripts/S03-llm-routing-profiles-06.log |
| S03.fallback-skip | pass | 391b5546481d | bullet 6: pass | <scratch>/sdk/S03/transcripts/S03-llm-routing-profiles-06.log |
| S03.fallback-transient | pass | 391b5546481d | bullet 6: pass; bullet 16: pass | <scratch>/sdk/S03/transcripts/S03-llm-routing-profiles-06.log |
| S03.meta-profile-store | pass | 391b5546481d | bullet 13: pass | <scratch>/sdk/S03/transcripts/S03-llm-routing-profiles-13.log |
| S03.multimodal-router-images | pass | 391b5546481d | bullet 4: pass; bullet 17: blocked (bullet is labeled blocked) | <scratch>/sdk/S03/transcripts/S03-llm-routing-profiles-04.log |
| S03.multimodal-router-text | pass | 391b5546481d | bullet 4: pass | <scratch>/sdk/S03/transcripts/S03-llm-routing-profiles-04.log |
| S03.profile-concurrency-limits | pass | 391b5546481d | bullet 7: pass | <scratch>/sdk/S03/transcripts/S03-llm-routing-profiles-07.log |
| S03.profile-migration | pass | 391b5546481d | bullet 7: pass | <scratch>/sdk/S03/transcripts/S03-llm-routing-profiles-07.log |
| S03.profile-save-load | pass | 391b5546481d | bullet 7: pass; bullet 16: pass | <scratch>/sdk/S03/transcripts/S03-llm-routing-profiles-07.log |
| S03.profile-secrets | pass | 391b5546481d | bullet 7: pass | <scratch>/sdk/S03/transcripts/S03-llm-routing-profiles-07.log |
| S03.provider-connections | pass | 391b5546481d | bullet 8: pass | <scratch>/sdk/S03/transcripts/S03-llm-routing-profiles-08.log |
| S03.random-router | pass | 391b5546481d | bullet 5: pass | <scratch>/sdk/S03/transcripts/S03-llm-routing-profiles-05.log |
| S03.registry-crud | pass | 391b5546481d | bullet 1: pass; bullet 16: pass | <scratch>/sdk/S03/transcripts/S03-llm-routing-profiles-01.log |
| S03.registry-independent-metrics | pass | 391b5546481d | bullet 1: pass | <scratch>/sdk/S03/transcripts/S03-llm-routing-profiles-01.log |
| S03.route-task-to-model | pass | 391b5546481d | bullet 12: pass; bullet 17: blocked (bullet is labeled blocked) | <scratch>/sdk/S03/transcripts/S03-llm-routing-profiles-12.log |
| S03.router-validation | pass | 391b5546481d | bullet 5: pass | <scratch>/sdk/S03/transcripts/S03-llm-routing-profiles-05.log |
| S03.subscription-login | pass | 391b5546481d | bullet 14: pass; bullet 18: blocked (bullet is labeled blocked) | <scratch>/sdk/S03/transcripts/S03-llm-routing-profiles-14.log |
| S03.subscription-requests | pass | 391b5546481d | bullet 14: pass | <scratch>/sdk/S03/transcripts/S03-llm-routing-profiles-14.log |
| S03.switch-llm-object | pass | 391b5546481d | bullet 10: pass | <scratch>/sdk/S03/transcripts/S03-llm-routing-profiles-10.log |
| S03.switch-llm-tool | pass | 391b5546481d | bullet 11: pass; bullet 17: blocked (bullet is labeled blocked) | <scratch>/sdk/S03/transcripts/S03-llm-routing-profiles-11.log |
| S03.switch-profile | pass | 391b5546481d | bullet 9: pass; bullet 15: pass; bullet 17: blocked (bullet is labeled blocked) | <scratch>/sdk/S03/transcripts/S03-llm-routing-profiles-09.log |
| S04.arun-interrupt | pass | 391b5546481d | bullet 12: pass; bullet 24: pass | <scratch>/sdk/S04/transcripts/S04-local-conversation-12.log |
| S04.async-callback | pass | 391b5546481d | bullet 11: pass; bullet 23: pass | <scratch>/sdk/S04/transcripts/S04-local-conversation-11.log |
| S04.callbacks | pass | 391b5546481d | bullet 2: pass; bullet 7: pass | <scratch>/sdk/S04/transcripts/S04-local-conversation-02.log |
| S04.cancel-token | pass | 391b5546481d | bullet 13: pass; bullet 24: pass | <scratch>/sdk/S04/transcripts/S04-local-conversation-13.log |
| S04.close | pass | 391b5546481d | bullet 9: pass | <scratch>/sdk/S04/transcripts/S04-local-conversation-09.log |
| S04.conversation-id | pass | 391b5546481d | bullet 1: pass | <scratch>/sdk/S04/transcripts/S04-local-conversation-01.log |
| S04.custom-file-store | pass | 391b5546481d | bullet 6: pass | <scratch>/sdk/S04/transcripts/S04-local-conversation-06.log |
| S04.factory | pass | 391b5546481d | bullet 1: pass; bullet 22: pass; bullet 26: blocked (bullet is labeled blocked) | <scratch>/sdk/S04/transcripts/S04-local-conversation-01.log |
| S04.final-response | pass | 391b5546481d | bullet 20: pass; bullet 22: pass | <scratch>/sdk/S04/transcripts/S04-local-conversation-20.log |
| S04.fork | pass | 391b5546481d | bullet 16: pass; bullet 24: pass | <scratch>/sdk/S04/transcripts/S04-local-conversation-16.log |
| S04.fork-branch | pass | 391b5546481d | bullet 17: pass; bullet 24: pass | <scratch>/sdk/S04/transcripts/S04-local-conversation-17.log |
| S04.goal-loop | pass | 391b5546481d | bullet 18: pass; bullet 24: pass | <scratch>/sdk/S04/transcripts/S04-local-conversation-18.log |
| S04.pause | pass | 391b5546481d | bullet 10: pass; bullet 23: pass | <scratch>/sdk/S04/transcripts/S04-local-conversation-10.log |
| S04.persist-events | pass | 0da9cf850a0e | bullet 2: pass; bullet 3: pass; bullet 23: blocked (1 model-backed block(s) skipped (no DEEPSEEK_API_KEY)) | <scratch>/sdk/S04-persist2/transcripts/S04-local-conversation-02.log |
| S04.resume | pass | 391b5546481d | bullet 2: pass; bullet 4: pass; bullet 22: pass; bullet 23: pass | <scratch>/sdk/S04/transcripts/S04-local-conversation-02.log |
| S04.resume-agent-compat | pass | 391b5546481d | bullet 5: pass | <scratch>/sdk/S04/transcripts/S04-local-conversation-05.log |
| S04.run-error | pass | 391b5546481d | bullet 8: pass | <scratch>/sdk/S04/transcripts/S04-local-conversation-08.log |
| S04.secrets-constructor | pass | 391b5546481d | bullet 14: pass | <scratch>/sdk/S04/transcripts/S04-local-conversation-14.log |
| S04.secrets-masking | pass | 391b5546481d | bullet 14: pass; bullet 23: pass | <scratch>/sdk/S04/transcripts/S04-local-conversation-14.log |
| S04.secrets-update | pass | 391b5546481d | bullet 14: pass; bullet 23: pass | <scratch>/sdk/S04/transcripts/S04-local-conversation-14.log |
| S04.stats | pass | 391b5546481d | bullet 21: pass | <scratch>/sdk/S04/transcripts/S04-local-conversation-21.log |
| S04.title-and-tags | pass | 391b5546481d | bullet 20: pass; bullet 24: pass | <scratch>/sdk/S04/transcripts/S04-local-conversation-20.log |
| S04.visualizer-custom | pass | 391b5546481d | bullet 15: pass; bullet 24: pass | <scratch>/sdk/S04/transcripts/S04-local-conversation-15.log |
| S04.visualizer-default | pass | 391b5546481d | bullet 15: pass; bullet 23: pass | <scratch>/sdk/S04/transcripts/S04-local-conversation-15.log |
| S05.completion-log-event | pass | e776f878876e | bullet 2: pass | <scratch>/sdk/S05/transcripts/S05-events-02.log |
| S05.error-classification | pass | e776f878876e | bullet 2: pass | <scratch>/sdk/S05/transcripts/S05-events-02.log |
| S05.event-base | pass | e776f878876e | bullet 1: pass | <scratch>/sdk/S05/transcripts/S05-events-01.log |
| S05.event-cache | pass | e776f878876e | bullet 1: pass | <scratch>/sdk/S05/transcripts/S05-events-01.log |
| S05.event-log-append | pass | e776f878876e | bullet 3: pass | <scratch>/sdk/S05/transcripts/S05-events-03.log |
| S05.event-log-files | pass | e776f878876e | bullet 1: pass | <scratch>/sdk/S05/transcripts/S05-events-01.log |
| S05.event-log-recovery | pass | 0da9cf850a0e | bullet 3: pass | <scratch>/sdk/S04-persist2/transcripts/S04-local-conversation-03.log |
| S05.event-tree | pass | e776f878876e | bullet 1: pass | <scratch>/sdk/S05/transcripts/S05-events-01.log |
| S05.events-to-messages | pass | e776f878876e | bullet 1: pass; bullet 5: pass; bullet 7: pass | <scratch>/sdk/S05/transcripts/S05-events-01.log |
| S05.json-roundtrip | pass | e776f878876e | bullet 1: pass; bullet 5: pass; bullet 6: pass | <scratch>/sdk/S05/transcripts/S05-events-01.log |
| S05.openai-format | pass | e776f878876e | bullet 1: pass; bullet 5: pass; bullet 6: pass | <scratch>/sdk/S05/transcripts/S05-events-01.log |
| S05.resume-transcript | pass | e776f878876e | bullet 1: pass | <scratch>/sdk/S05/transcripts/S05-events-01.log |
| S05.streaming-delta | pass | e776f878876e | bullet 2: pass | <scratch>/sdk/S05/transcripts/S05-events-02.log |
| S05.system-prompt-blocks | pass | e776f878876e | bullet 8: pass | <scratch>/sdk/S05/transcripts/S05-events-08.log |
| S05.to-llm-message | pass | e776f878876e | bullet 1: pass | <scratch>/sdk/S05/transcripts/S05-events-01.log |
| S05.unmatched-actions | pass | e776f878876e | bullet 1: pass | <scratch>/sdk/S05/transcripts/S05-events-01.log |
| S05.visualize | pass | e776f878876e | bullet 1: pass | <scratch>/sdk/S05/transcripts/S05-events-01.log |
| S06.condenser-resume | pass | edff6711505a | bullet 11: pass; bullet 14: blocked (1 model-backed block(s) skipped (no DEEPSEEK_API_KEY)) | <scratch>/sdk/S06-nokey/transcripts/S06-context-condensers-11.log |
| S06.context-datetime | pass | edff6711505a | bullet 1: pass | <scratch>/sdk/S06-nokey/transcripts/S06-context-condensers-01.log |
| S06.context-overflow-request | pass | edff6711505a | bullet 10: pass; bullet 17: blocked (1 model-backed block(s) skipped (no DEEPSEEK_API_KEY)) | <scratch>/sdk/S06-nokey/transcripts/S06-context-condensers-10.log |
| S06.context-secrets | pass | edff6711505a | bullet 1: pass | <scratch>/sdk/S06-nokey/transcripts/S06-context-condensers-01.log |
| S06.context-skills | pass | edff6711505a | bullet 1: pass; bullet 16: blocked (1 model-backed block(s) skipped (no DEEPSEEK_API_KEY)) | <scratch>/sdk/S06-nokey/transcripts/S06-context-condensers-01.log |
| S06.context-suffixes | pass | edff6711505a | bullet 1: pass; bullet 16: blocked (1 model-backed block(s) skipped (no DEEPSEEK_API_KEY)) | <scratch>/sdk/S06-nokey/transcripts/S06-context-condensers-01.log |
| S06.hard-reset | pass | edff6711505a | bullet 8: pass; bullet 17: blocked (1 model-backed block(s) skipped (no DEEPSEEK_API_KEY)) | <scratch>/sdk/S06-nokey/transcripts/S06-context-condensers-08.log |
| S06.manual-condense | pass | edff6711505a | bullet 9: pass; bullet 13: blocked (1 model-backed block(s) skipped (no DEEPSEEK_API_KEY)) | <scratch>/sdk/S06-nokey/transcripts/S06-context-condensers-09.log |
| S06.memory-load | pass | edff6711505a | bullet 3: pass; bullet 15: blocked (1 model-backed block(s) skipped (no DEEPSEEK_API_KEY)) | <scratch>/sdk/S06-nokey/transcripts/S06-context-condensers-03.log |
| S06.memory-write-path | pass | edff6711505a | bullet 3: pass; bullet 15: blocked (1 model-backed block(s) skipped (no DEEPSEEK_API_KEY)) | <scratch>/sdk/S06-nokey/transcripts/S06-context-condensers-03.log |
| S06.noop-condenser | pass | edff6711505a | bullet 6: pass; bullet 12: pass | <scratch>/sdk/S06-nokey/transcripts/S06-context-condensers-06.log |
| S06.persona-soul | pass | edff6711505a | bullet 2: pass | <scratch>/sdk/S06-nokey/transcripts/S06-context-condensers-02.log |
| S06.pipeline-condenser | pass | edff6711505a | bullet 12: pass | <scratch>/sdk/S06-nokey/transcripts/S06-context-condensers-12.log |
| S06.soft-vs-hard | pass | edff6711505a | bullet 7: pass; bullet 17: blocked (1 model-backed block(s) skipped (no DEEPSEEK_API_KEY)) | <scratch>/sdk/S06-nokey/transcripts/S06-context-condensers-07.log |
| S06.summarizing-reasons | pass | edff6711505a | bullet 6: pass | <scratch>/sdk/S06-nokey/transcripts/S06-context-condensers-06.log |
| S06.summarizing-size | pass | edff6711505a | bullet 4: pass; bullet 13: blocked (1 model-backed block(s) skipped (no DEEPSEEK_API_KEY)); bullet 14: blocked (1 model-backed block(s) skipped (no DEEPSEEK_API_KEY)); bullet 17: blocked (1 model-backed block(s) skipped (no DEEPSEEK_API_KEY)) | <scratch>/sdk/S06-nokey/transcripts/S06-context-condensers-04.log |
| S06.summarizing-tokens | pass | edff6711505a | bullet 5: pass; bullet 17: blocked (1 model-backed block(s) skipped (no DEEPSEEK_API_KEY)) | <scratch>/sdk/S06-nokey/transcripts/S06-context-condensers-05.log |
| S06.summary-llm-streaming | pass | edff6711505a | bullet 4: pass; bullet 14: blocked (1 model-backed block(s) skipped (no DEEPSEEK_API_KEY)) | <scratch>/sdk/S06-nokey/transcripts/S06-context-condensers-04.log |
| S06.system-prompt-custom | pass | edff6711505a | bullet 2: pass | <scratch>/sdk/S06-nokey/transcripts/S06-context-condensers-02.log |
| S06.system-prompt-model-specific | pass | edff6711505a | bullet 2: pass | <scratch>/sdk/S06-nokey/transcripts/S06-context-condensers-02.log |
| S06.system-prompt-registry | pass | edff6711505a | bullet 2: pass | <scratch>/sdk/S06-nokey/transcripts/S06-context-condensers-02.log |
| S06.view | pass | edff6711505a | bullet 4: pass; bullet 6: pass | <scratch>/sdk/S06-nokey/transcripts/S06-context-condensers-04.log |
| S06.view-atomicity | pass | edff6711505a | bullet 4: pass; bullet 13: blocked (1 model-backed block(s) skipped (no DEEPSEEK_API_KEY)); bullet 18: blocked (bullet is labeled blocked) | <scratch>/sdk/S06-nokey/transcripts/S06-context-condensers-04.log |
| S07.agentskills-load | pass | e776f878876e | bullet 2: pass; bullet 12: pass | <scratch>/sdk/S07/transcripts/S07-skills-02.log |
| S07.agentskills-trigger | pass | e776f878876e | bullet 2: pass; bullet 12: pass | <scratch>/sdk/S07/transcripts/S07-skills-02.log |
| S07.disable-model-invocation | pass | e776f878876e | bullet 2: pass | <scratch>/sdk/S07/transcripts/S07-skills-02.log |
| S07.disabled-skills | pass | e776f878876e | bullet 5: pass | <scratch>/sdk/S07/transcripts/S07-skills-05.log |
| S07.inline-commands | pass | e776f878876e | bullet 2: pass | <scratch>/sdk/S07/transcripts/S07-skills-02.log |
| S07.invoke-skill-autoattach | pass | e776f878876e | bullet 2: pass; bullet 10: pass; bullet 12: pass | <scratch>/sdk/S07/transcripts/S07-skills-02.log |
| S07.invoke-skill-run | pass | e776f878876e | bullet 2: pass; bullet 10: pass; bullet 15: pass | <scratch>/sdk/S07/transcripts/S07-skills-02.log |
| S07.keyword-trigger | pass | e776f878876e | bullet 1: pass; bullet 11: pass | <scratch>/sdk/S07/transcripts/S07-skills-01.log |
| S07.load-project-flag | pass | e776f878876e | bullet 5: pass | <scratch>/sdk/S07/transcripts/S07-skills-05.log |
| S07.marketplace-install | pass | e776f878876e | bullet 6: pass; bullet 7: pass; bullet 14: pass | <scratch>/sdk/S07/transcripts/S07-skills-06.log |
| S07.merge-precedence | pass | e776f878876e | bullet 5: pass | <scratch>/sdk/S07/transcripts/S07-skills-05.log |
| S07.message-suffixes | pass | e776f878876e | bullet 1: pass; bullet 11: pass | <scratch>/sdk/S07/transcripts/S07-skills-01.log |
| S07.path-rules | pass | e776f878876e | bullet 4: pass | <scratch>/sdk/S07/transcripts/S07-skills-04.log |
| S07.project-skills | pass | e776f878876e | bullet 5: pass | <scratch>/sdk/S07/transcripts/S07-skills-05.log |
| S07.public-skills | pass | e776f878876e | bullet 8: pass; bullet 9: pass; bullet 11: pass | <scratch>/sdk/S07/transcripts/S07-skills-08.log |
| S07.repo-skill-always-on | pass | e776f878876e | bullet 1: pass; bullet 11: pass | <scratch>/sdk/S07/transcripts/S07-skills-01.log |
| S07.resources | pass | e776f878876e | bullet 2: pass; bullet 12: pass | <scratch>/sdk/S07/transcripts/S07-skills-02.log |
| S07.serialization | pass | e776f878876e | bullet 1: pass; bullet 2: pass | <scratch>/sdk/S07/transcripts/S07-skills-01.log |
| S07.skill-mcp-json | pass | e776f878876e | bullet 2: pass | <scratch>/sdk/S07/transcripts/S07-skills-02.log |
| S07.task-trigger | pass | e776f878876e | bullet 1: pass | <scratch>/sdk/S07/transcripts/S07-skills-01.log |
| S07.trigger-once | pass | e776f878876e | bullet 1: pass | <scratch>/sdk/S07/transcripts/S07-skills-01.log |
| S07.user-skills | pass | e776f878876e | bullet 5: pass | <scratch>/sdk/S07/transcripts/S07-skills-05.log |
| S08.agent-plugins-layout | pass | edff6711505a | bullet 1: pass; bullet 16: blocked (1 model-backed block(s) skipped (no DEEPSEEK_API_KEY)) | <scratch>/sdk/S08-nokey/transcripts/S08-plugins-marketplace-01.log |
| S08.agent-plugins-mcp | pass | edff6711505a | bullet 1: pass | <scratch>/sdk/S08-nokey/transcripts/S08-plugins-marketplace-01.log |
| S08.ambient-plugins | pass | edff6711505a | bullet 2: pass; bullet 7: pass | <scratch>/sdk/S08-nokey/transcripts/S08-plugins-marketplace-02.log |
| S08.claude-code-layout | pass | edff6711505a | bullet 1: pass | <scratch>/sdk/S08-nokey/transcripts/S08-plugins-marketplace-01.log |
| S08.commands-as-skills | pass | edff6711505a | bullet 1: pass | <scratch>/sdk/S08-nokey/transcripts/S08-plugins-marketplace-01.log |
| S08.conversation-attach | pass | edff6711505a | bullet 2: pass; bullet 4: pass; bullet 15: blocked (1 model-backed block(s) skipped (no DEEPSEEK_API_KEY)); bullet 16: blocked (1 model-backed block(s) skipped (no DEEPSEEK_API_KEY)) | <scratch>/sdk/S08-nokey/transcripts/S08-plugins-marketplace-02.log |
| S08.fetch-cache | pass | edff6711505a | bullet 4: pass; bullet 5: pass; bullet 6: pass; bullet 15: blocked (1 model-backed block(s) skipped (no DEEPSEEK_API_KEY)) | <scratch>/sdk/S08-nokey/transcripts/S08-plugins-marketplace-04.log |
| S08.installation-manager | pass | edff6711505a | bullet 9: pass | <scratch>/sdk/S08-nokey/transcripts/S08-plugins-marketplace-09.log |
| S08.installed-plugins | pass | edff6711505a | bullet 7: pass; bullet 8: pass; bullet 15: blocked (1 model-backed block(s) skipped (no DEEPSEEK_API_KEY)) | <scratch>/sdk/S08-nokey/transcripts/S08-plugins-marketplace-07.log |
| S08.load-plugin-runtime | pass | edff6711505a | bullet 13: pass | <scratch>/sdk/S08-nokey/transcripts/S08-plugins-marketplace-13.log |
| S08.marketplace-auto-load | pass | edff6711505a | bullet 12: pass | <scratch>/sdk/S08-nokey/transcripts/S08-plugins-marketplace-12.log |
| S08.marketplace-import-path | pass | edff6711505a | bullet 14: pass | <scratch>/sdk/S08-nokey/transcripts/S08-plugins-marketplace-14.log |
| S08.marketplace-manifest | pass | edff6711505a | bullet 10: pass; bullet 17: pass | <scratch>/sdk/S08-nokey/transcripts/S08-plugins-marketplace-10.log |
| S08.marketplace-registry | pass | edff6711505a | bullet 11: pass | <scratch>/sdk/S08-nokey/transcripts/S08-plugins-marketplace-11.log |
| S08.merge-semantics | pass | edff6711505a | bullet 1: pass | <scratch>/sdk/S08-nokey/transcripts/S08-plugins-marketplace-01.log |
| S08.path-containment | pass | edff6711505a | bullet 1: pass | <scratch>/sdk/S08-nokey/transcripts/S08-plugins-marketplace-01.log |
| S08.plugin-agents | pass | edff6711505a | bullet 2: pass | <scratch>/sdk/S08-nokey/transcripts/S08-plugins-marketplace-02.log |
| S08.resolved-refs | pass | edff6711505a | bullet 2: pass; bullet 15: blocked (1 model-backed block(s) skipped (no DEEPSEEK_API_KEY)) | <scratch>/sdk/S08-nokey/transcripts/S08-plugins-marketplace-02.log |
| S08.source-parsing | pass | edff6711505a | bullet 1: pass | <scratch>/sdk/S08-nokey/transcripts/S08-plugins-marketplace-01.log |
| S09.argument-validation | pass | edff6711505a | bullet 1: pass; bullet 12: pass | <scratch>/sdk/S09/transcripts/S09-mcp-01.log |
| S09.config-model | pass | edff6711505a | bullet 12: pass | <scratch>/sdk/S09/transcripts/S09-mcp-12.log |
| S09.config-secrets | pass | edff6711505a | bullet 1: pass; bullet 12: pass | <scratch>/sdk/S09/transcripts/S09-mcp-01.log |
| S09.config-var-expansion | pass | edff6711505a | bullet 1: pass | <scratch>/sdk/S09/transcripts/S09-mcp-01.log |
| S09.enabled-flag | pass | edff6711505a | bullet 4: pass | <scratch>/sdk/S09/transcripts/S09-mcp-04.log |
| S09.filter-tools-regex | pass | edff6711505a | bullet 2: pass; bullet 14: pass | <scratch>/sdk/S09/transcripts/S09-mcp-02.log |
| S09.http-sse-server | pass | edff6711505a | bullet 6: pass | <scratch>/sdk/S09/transcripts/S09-mcp-06.log |
| S09.listing-timeout | pass | edff6711505a | bullet 5: pass | <scratch>/sdk/S09/transcripts/S09-mcp-05.log |
| S09.oauth | pass | edff6711505a | bullet 8: pass; bullet 9: blocked (bullet is labeled blocked) | <scratch>/sdk/S09/transcripts/S09-mcp-08.log |
| S09.observation | pass | edff6711505a | bullet 1: pass; bullet 12: pass; bullet 13: pass; bullet 14: pass | <scratch>/sdk/S09/transcripts/S09-mcp-01.log |
| S09.schema-conversion | pass | edff6711505a | bullet 1: pass; bullet 12: pass; bullet 13: pass | <scratch>/sdk/S09/transcripts/S09-mcp-01.log |
| S09.secret-refs-in-calls | pass | edff6711505a | bullet 1: pass; bullet 13: pass | <scratch>/sdk/S09/transcripts/S09-mcp-01.log |
| S09.session-reuse | pass | edff6711505a | bullet 1: pass | <scratch>/sdk/S09/transcripts/S09-mcp-01.log |
| S09.startup-failure-tolerated | pass | edff6711505a | bullet 3: pass | <scratch>/sdk/S09/transcripts/S09-mcp-03.log |
| S09.static-auth | pass | edff6711505a | bullet 7: pass | <scratch>/sdk/S09/transcripts/S09-mcp-07.log |
| S09.stdio-server | pass | edff6711505a | bullet 1: pass; bullet 6: pass; bullet 13: pass; bullet 14: pass | <scratch>/sdk/S09/transcripts/S09-mcp-01.log |
| S09.tool-serialization | pass | edff6711505a | bullet 12: pass | <scratch>/sdk/S09/transcripts/S09-mcp-12.log |
| S09.tools-list-changed | pass | edff6711505a | bullet 1: pass | <scratch>/sdk/S09/transcripts/S09-mcp-01.log |
| S10.agent-hook | pass | c5f588f089d0 | bullet 4: pass; bullet 7: blocked (1 model-backed block(s) skipped (no DEEPSEEK_API_KEY)) | <scratch>/sdk/S10-nokey/transcripts/S10-hooks-04.log |
| S10.async-hooks | pass | c5f588f089d0 | bullet 1: pass | <scratch>/sdk/S10-nokey/transcripts/S10-hooks-01.log |
| S10.blocked-state-persistence | pass | c5f588f089d0 | bullet 2: pass | <scratch>/sdk/S10-nokey/transcripts/S10-hooks-02.log |
| S10.command-exec | pass | c5f588f089d0 | bullet 1: pass; bullet 3: pass; bullet 6: blocked (1 model-backed block(s) skipped (no DEEPSEEK_API_KEY)) | <scratch>/sdk/S10-nokey/transcripts/S10-hooks-01.log |
| S10.config-discovery | pass | c5f588f089d0 | bullet 1: pass; bullet 3: pass | <scratch>/sdk/S10-nokey/transcripts/S10-hooks-01.log |
| S10.config-format | pass | c5f588f089d0 | bullet 3: pass | <scratch>/sdk/S10-nokey/transcripts/S10-hooks-03.log |
| S10.definition-validation | pass | c5f588f089d0 | bullet 3: pass | <scratch>/sdk/S10-nokey/transcripts/S10-hooks-03.log |
| S10.execution-events | pass | c5f588f089d0 | bullet 1: pass; bullet 7: blocked (1 model-backed block(s) skipped (no DEEPSEEK_API_KEY)) | <scratch>/sdk/S10-nokey/transcripts/S10-hooks-01.log |
| S10.exit-codes | pass | c5f588f089d0 | bullet 1: pass; bullet 3: pass | <scratch>/sdk/S10-nokey/transcripts/S10-hooks-01.log |
| S10.llm-follows-switch | pass | c5f588f089d0 | bullet 4: pass | <scratch>/sdk/S10-nokey/transcripts/S10-hooks-04.log |
| S10.matcher | pass | c5f588f089d0 | bullet 1: pass; bullet 3: pass | <scratch>/sdk/S10-nokey/transcripts/S10-hooks-01.log |
| S10.plugin-hooks-merge | pass | c5f588f089d0 | bullet 5: pass | <scratch>/sdk/S10-nokey/transcripts/S10-hooks-05.log |
| S10.post-tool-use | pass | c5f588f089d0 | bullet 1: pass; bullet 6: blocked (1 model-backed block(s) skipped (no DEEPSEEK_API_KEY)) | <scratch>/sdk/S10-nokey/transcripts/S10-hooks-01.log |
| S10.pre-tool-use-block | pass | c5f588f089d0 | bullet 1: pass; bullet 6: blocked (1 model-backed block(s) skipped (no DEEPSEEK_API_KEY)) | <scratch>/sdk/S10-nokey/transcripts/S10-hooks-01.log |
| S10.prompt-hook | pass | c5f588f089d0 | bullet 4: pass; bullet 8: blocked (1 model-backed block(s) skipped (no DEEPSEEK_API_KEY)) | <scratch>/sdk/S10-nokey/transcripts/S10-hooks-04.log |
| S10.session-end-on-close | pass | c5f588f089d0 | bullet 1: pass | <scratch>/sdk/S10-nokey/transcripts/S10-hooks-01.log |
| S10.session-start | pass | c5f588f089d0 | bullet 1: pass | <scratch>/sdk/S10-nokey/transcripts/S10-hooks-01.log |
| S10.stop-hook | pass | c5f588f089d0 | bullet 1: pass; bullet 6: blocked (1 model-backed block(s) skipped (no DEEPSEEK_API_KEY)); bullet 7: blocked (1 model-backed block(s) skipped (no DEEPSEEK_API_KEY)) | <scratch>/sdk/S10-nokey/transcripts/S10-hooks-01.log |
| S10.user-prompt-block | pass | c5f588f089d0 | bullet 2: pass | <scratch>/sdk/S10-nokey/transcripts/S10-hooks-02.log |
| S10.user-prompt-context | pass | c5f588f089d0 | bullet 1: pass; bullet 6: blocked (1 model-backed block(s) skipped (no DEEPSEEK_API_KEY)) | <scratch>/sdk/S10-nokey/transcripts/S10-hooks-01.log |
| S11.builtin-agents | pass | c5f588f089d0 | bullet 5: pass; bullet 7: pass; bullet 12: blocked (1 model-backed block(s) skipped (no DEEPSEEK_API_KEY)) | <scratch>/sdk/S11-nokey/transcripts/S11-subagents-delegation-05.log |
| S11.conversation-autoregister | pass | c5f588f089d0 | bullet 7: pass | <scratch>/sdk/S11-nokey/transcripts/S11-subagents-delegation-07.log |
| S11.definition-factory | pass | c5f588f089d0 | bullet 4: pass; bullet 13: blocked (1 model-backed block(s) skipped (no DEEPSEEK_API_KEY)) | <scratch>/sdk/S11-nokey/transcripts/S11-subagents-delegation-04.log |
| S11.definition-model | pass | c5f588f089d0 | bullet 4: pass | <scratch>/sdk/S11-nokey/transcripts/S11-subagents-delegation-04.log |
| S11.delegate-executor | pass | c5f588f089d0 | bullet 10: pass | <scratch>/sdk/S11-nokey/transcripts/S11-subagents-delegation-10.log |
| S11.delegation-visualizer | pass | c5f588f089d0 | bullet 10: pass; bullet 11: blocked (1 model-backed block(s) skipped (no DEEPSEEK_API_KEY)) | <scratch>/sdk/S11-nokey/transcripts/S11-subagents-delegation-10.log |
| S11.file-discovery | pass | c5f588f089d0 | bullet 4: pass | <scratch>/sdk/S11-nokey/transcripts/S11-subagents-delegation-04.log |
| S11.file-precedence | pass | c5f588f089d0 | bullet 4: pass | <scratch>/sdk/S11-nokey/transcripts/S11-subagents-delegation-04.log |
| S11.markdown-schema | pass | c5f588f089d0 | bullet 4: pass | <scratch>/sdk/S11-nokey/transcripts/S11-subagents-delegation-04.log |
| S11.plugin-agents | pass | c5f588f089d0 | bullet 4: pass | <scratch>/sdk/S11-nokey/transcripts/S11-subagents-delegation-04.log |
| S11.register-if-absent | pass | c5f588f089d0 | bullet 4: pass | <scratch>/sdk/S11-nokey/transcripts/S11-subagents-delegation-04.log |
| S11.register-programmatic | pass | c5f588f089d0 | bullet 1: pass; bullet 4: pass; bullet 12: blocked (1 model-backed block(s) skipped (no DEEPSEEK_API_KEY)) | <scratch>/sdk/S11-nokey/transcripts/S11-subagents-delegation-01.log |
| S11.sub-agent-scope | pass | c5f588f089d0 | bullet 9: pass | <scratch>/sdk/S11-nokey/transcripts/S11-subagents-delegation-09.log |
| S11.task-confirmation | pass | c5f588f089d0 | bullet 8: pass | <scratch>/sdk/S11-nokey/transcripts/S11-subagents-delegation-08.log |
| S11.task-errors | pass | c5f588f089d0 | bullet 1: pass | <scratch>/sdk/S11-nokey/transcripts/S11-subagents-delegation-01.log |
| S11.task-hooks | pass | c5f588f089d0 | bullet 1: pass | <scratch>/sdk/S11-nokey/transcripts/S11-subagents-delegation-01.log |
| S11.task-limits | pass | c5f588f089d0 | bullet 1: pass | <scratch>/sdk/S11-nokey/transcripts/S11-subagents-delegation-01.log |
| S11.task-metrics | pass | c5f588f089d0 | bullet 1: pass; bullet 12: blocked (1 model-backed block(s) skipped (no DEEPSEEK_API_KEY)) | <scratch>/sdk/S11-nokey/transcripts/S11-subagents-delegation-01.log |
| S11.task-persistence | pass | c5f588f089d0 | bullet 1: pass; bullet 7: pass | <scratch>/sdk/S11-nokey/transcripts/S11-subagents-delegation-01.log |
| S11.task-resume | pass | c5f588f089d0 | bullet 1: pass; bullet 11: blocked (1 model-backed block(s) skipped (no DEEPSEEK_API_KEY)) | <scratch>/sdk/S11-nokey/transcripts/S11-subagents-delegation-01.log |
| S11.task-run | pass | c5f588f089d0 | bullet 1: pass; bullet 11: blocked (1 model-backed block(s) skipped (no DEEPSEEK_API_KEY)) | <scratch>/sdk/S11-nokey/transcripts/S11-subagents-delegation-01.log |
| S12.api-evaluate | pass | c707d3737e8d | bullet 3: pass; bullet 5: blocked (bullet is labeled blocked) | <scratch>/sdk/S12-nokey/transcripts/S12-critic-03.log |
| S12.api-followup-prompt | pass | c707d3737e8d | bullet 1: pass; bullet 3: pass | <scratch>/sdk/S12-nokey/transcripts/S12-critic-01.log |
| S12.api-key-handling | pass | c707d3737e8d | bullet 1: pass | <scratch>/sdk/S12-nokey/transcripts/S12-critic-01.log |
| S12.chat-template | pass | c707d3737e8d | bullet 1: pass | <scratch>/sdk/S12-nokey/transcripts/S12-critic-01.log |
| S12.critic-display | pass | c707d3737e8d | bullet 1: pass; bullet 3: pass | <scratch>/sdk/S12-nokey/transcripts/S12-critic-01.log |
| S12.critic-result | pass | c707d3737e8d | bullet 1: pass | <scratch>/sdk/S12-nokey/transcripts/S12-critic-01.log |
| S12.custom-refine-policy | pass | c707d3737e8d | bullet 1: pass | <scratch>/sdk/S12-nokey/transcripts/S12-critic-01.log |
| S12.end-to-end-refinement | pass | c707d3737e8d | bullet 3: pass; bullet 4: blocked (1 model-backed block(s) skipped (no DEEPSEEK_API_KEY)); bullet 5: blocked (bullet is labeled blocked) | <scratch>/sdk/S12-nokey/transcripts/S12-critic-03.log |
| S12.eval-mode | pass | c707d3737e8d | bullet 2: pass | <scratch>/sdk/S12-nokey/transcripts/S12-critic-02.log |
| S12.refinement-config | pass | c707d3737e8d | bullet 1: pass | <scratch>/sdk/S12-nokey/transcripts/S12-critic-01.log |
| S12.refinement-decision | pass | c707d3737e8d | bullet 2: pass | <scratch>/sdk/S12-nokey/transcripts/S12-critic-02.log |
| S12.refinement-followup | pass | c707d3737e8d | bullet 2: pass | <scratch>/sdk/S12-nokey/transcripts/S12-critic-02.log |
| S12.result-on-events | pass | c707d3737e8d | bullet 2: pass; bullet 4: blocked (1 model-backed block(s) skipped (no DEEPSEEK_API_KEY)) | <scratch>/sdk/S12-nokey/transcripts/S12-critic-02.log |
| S12.rule-critics | pass | c707d3737e8d | bullet 1: pass | <scratch>/sdk/S12-nokey/transcripts/S12-critic-01.log |
| S12.settings-critic | pass | c707d3737e8d | bullet 1: pass; bullet 3: pass | <scratch>/sdk/S12-nokey/transcripts/S12-critic-01.log |
| S13.analysis-event | pass | c707d3737e8d | bullet 1: pass | <scratch>/sdk/S13-nokey/transcripts/S13-security-confirmation-01.log |
| S13.confirm-approve-reject | pass | c707d3737e8d | bullet 1: pass; bullet 5: blocked (1 model-backed block(s) skipped (no DEEPSEEK_API_KEY)); bullet 9: blocked (bullet is labeled blocked) | <scratch>/sdk/S13-nokey/transcripts/S13-security-confirmation-01.log |
| S13.confirm-wait | pass | c707d3737e8d | bullet 1: pass; bullet 5: blocked (1 model-backed block(s) skipped (no DEEPSEEK_API_KEY)) | <scratch>/sdk/S13-nokey/transcripts/S13-security-confirmation-01.log |
| S13.ensemble | pass | c707d3737e8d | bullet 2: pass; bullet 7: pass | <scratch>/sdk/S13-nokey/transcripts/S13-security-confirmation-02.log |
| S13.grayswan | pass | c707d3737e8d | bullet 2: pass | <scratch>/sdk/S13-nokey/transcripts/S13-security-confirmation-02.log |
| S13.llm-analyzer | pass | c707d3737e8d | bullet 1: pass; bullet 5: blocked (1 model-backed block(s) skipped (no DEEPSEEK_API_KEY)); bullet 9: blocked (bullet is labeled blocked) | <scratch>/sdk/S13-nokey/transcripts/S13-security-confirmation-01.log |
| S13.mask-async | pass | c707d3737e8d | bullet 4: pass | <scratch>/sdk/S13-nokey/transcripts/S13-security-confirmation-04.log |
| S13.mask-models | pass | c707d3737e8d | bullet 3: pass | <scratch>/sdk/S13-nokey/transcripts/S13-security-confirmation-03.log |
| S13.mask-output | pass | c707d3737e8d | bullet 3: pass; bullet 8: blocked (1 model-backed block(s) skipped (no DEEPSEEK_API_KEY)) | <scratch>/sdk/S13-nokey/transcripts/S13-security-confirmation-03.log |
| S13.mask-tool-output | pass | c707d3737e8d | bullet 3: pass | <scratch>/sdk/S13-nokey/transcripts/S13-security-confirmation-03.log |
| S13.pattern-analyzer | pass | c707d3737e8d | bullet 2: pass | <scratch>/sdk/S13-nokey/transcripts/S13-security-confirmation-02.log |
| S13.policies | pass | c707d3737e8d | bullet 2: pass; bullet 7: pass | <scratch>/sdk/S13-nokey/transcripts/S13-security-confirmation-02.log |
| S13.policy-rails | pass | c707d3737e8d | bullet 2: pass | <scratch>/sdk/S13-nokey/transcripts/S13-security-confirmation-02.log |
| S13.risk-extraction | pass | c707d3737e8d | bullet 1: pass | <scratch>/sdk/S13-nokey/transcripts/S13-security-confirmation-01.log |
| S13.risk-levels | pass | c707d3737e8d | bullet 2: pass | <scratch>/sdk/S13-nokey/transcripts/S13-security-confirmation-02.log |
| S13.secret-injection | pass | c707d3737e8d | bullet 3: pass; bullet 8: blocked (1 model-backed block(s) skipped (no DEEPSEEK_API_KEY)) | <scratch>/sdk/S13-nokey/transcripts/S13-security-confirmation-03.log |
| S13.secret-registry | pass | c707d3737e8d | bullet 3: pass | <scratch>/sdk/S13-nokey/transcripts/S13-security-confirmation-03.log |
| S13.secret-sources | pass | c707d3737e8d | bullet 3: pass | <scratch>/sdk/S13-nokey/transcripts/S13-security-confirmation-03.log |
| S13.security-policy-prompt | pass | c707d3737e8d | bullet 1: pass; bullet 6: blocked (1 model-backed block(s) skipped (no DEEPSEEK_API_KEY)) | <scratch>/sdk/S13-nokey/transcripts/S13-security-confirmation-01.log |
| S13.settings-security | pass | c707d3737e8d | bullet 2: pass | <scratch>/sdk/S13-nokey/transcripts/S13-security-confirmation-02.log |
| S13.toolshield | pass | c707d3737e8d | bullet 2: pass | <scratch>/sdk/S13-nokey/transcripts/S13-security-confirmation-02.log |
| S14.apply-patch | pass | c707d3737e8d | bullet 1: pass; bullet 9: pass; bullet 11: pass; bullet 13: blocked (1 model-backed block(s) skipped (no DEEPSEEK_API_KEY)); bullet 14: blocked (1 model-backed block(s) skipped (no DEEPSEEK_API_KEY)) | <scratch>/sdk/S14-nokey/transcripts/S14-core-tools-01.log |
| S14.file-editor-edit | pass | c707d3737e8d | bullet 8: pass; bullet 12: blocked (1 model-backed block(s) skipped (no DEEPSEEK_API_KEY)); bullet 14: blocked (1 model-backed block(s) skipped (no DEEPSEEK_API_KEY)) | <scratch>/sdk/S14-nokey/transcripts/S14-core-tools-08.log |
| S14.file-editor-validation | pass | c707d3737e8d | bullet 8: pass | <scratch>/sdk/S14-nokey/transcripts/S14-core-tools-08.log |
| S14.file-editor-view | pass | c707d3737e8d | bullet 8: pass; bullet 14: blocked (1 model-backed block(s) skipped (no DEEPSEEK_API_KEY)) | <scratch>/sdk/S14-nokey/transcripts/S14-core-tools-08.log |
| S14.gemini-file-tools | pass | c707d3737e8d | bullet 11: pass; bullet 13: blocked (1 model-backed block(s) skipped (no DEEPSEEK_API_KEY)); bullet 14: blocked (1 model-backed block(s) skipped (no DEEPSEEK_API_KEY)) | <scratch>/sdk/S14-nokey/transcripts/S14-core-tools-11.log |
| S14.glob | pass | c707d3737e8d | bullet 1: pass; bullet 9: pass; bullet 10: pass | <scratch>/sdk/S14-nokey/transcripts/S14-core-tools-01.log |
| S14.grep | pass | c707d3737e8d | bullet 1: pass; bullet 9: pass | <scratch>/sdk/S14-nokey/transcripts/S14-core-tools-01.log |
| S14.preset-default | pass | c707d3737e8d | bullet 11: pass | <scratch>/sdk/S14-nokey/transcripts/S14-core-tools-11.log |
| S14.preset-gpt5-gemini | pass | c707d3737e8d | bullet 11: pass; bullet 13: blocked (1 model-backed block(s) skipped (no DEEPSEEK_API_KEY)) | <scratch>/sdk/S14-nokey/transcripts/S14-core-tools-11.log |
| S14.shared-working-dir | pass | c707d3737e8d | bullet 1: pass; bullet 12: blocked (1 model-backed block(s) skipped (no DEEPSEEK_API_KEY)) | <scratch>/sdk/S14-nokey/transcripts/S14-core-tools-01.log |
| S14.terminal-cleanup | pass | c707d3737e8d | bullet 2: pass; bullet 3: pass | <scratch>/sdk/S14-nokey/transcripts/S14-core-tools-02.log |
| S14.terminal-env | pass | c707d3737e8d | bullet 2: pass; bullet 3: pass | <scratch>/sdk/S14-nokey/transcripts/S14-core-tools-02.log |
| S14.terminal-exec | pass | c707d3737e8d | bullet 1: pass; bullet 2: pass; bullet 3: pass; bullet 12: blocked (1 model-backed block(s) skipped (no DEEPSEEK_API_KEY)); bullet 14: blocked (1 model-backed block(s) skipped (no DEEPSEEK_API_KEY)) | <scratch>/sdk/S14-nokey/transcripts/S14-core-tools-01.log |
| S14.terminal-interactive | pass | c707d3737e8d | bullet 2: pass; bullet 4: pass; bullet 14: blocked (1 model-backed block(s) skipped (no DEEPSEEK_API_KEY)) | <scratch>/sdk/S14-nokey/transcripts/S14-core-tools-02.log |
| S14.terminal-metadata | pass | c707d3737e8d | bullet 2: pass; bullet 3: pass | <scratch>/sdk/S14-nokey/transcripts/S14-core-tools-02.log |
| S14.terminal-output-truncation | pass | c707d3737e8d | bullet 2: pass; bullet 3: pass | <scratch>/sdk/S14-nokey/transcripts/S14-core-tools-02.log |
| S14.terminal-pane-pool | pass | c707d3737e8d | bullet 3: pass; bullet 4: pass | <scratch>/sdk/S14-nokey/transcripts/S14-core-tools-03.log |
| S14.terminal-secret-masking | pass | c707d3737e8d | bullet 2: pass; bullet 3: pass | <scratch>/sdk/S14-nokey/transcripts/S14-core-tools-02.log |
| S14.terminal-timeout | pass | c707d3737e8d | bullet 2: pass; bullet 3: pass | <scratch>/sdk/S14-nokey/transcripts/S14-core-tools-02.log |
| S14.tool-output-masking | pass | c707d3737e8d | bullet 8: pass; bullet 9: pass | <scratch>/sdk/S14-nokey/transcripts/S14-core-tools-08.log |
| S15.executor-config | pass | c707d3737e8d | bullet 4: pass | <scratch>/sdk/S15-nokey/transcripts/S15-browser-04.log |
| S15.interact | pass | c707d3737e8d | bullet 1: pass; bullet 8: blocked (1 model-backed block(s) skipped (no DEEPSEEK_API_KEY)) | <scratch>/sdk/S15-nokey/transcripts/S15-browser-01.log |
| S15.navigate-and-read | pass | c707d3737e8d | bullet 1: pass; bullet 8: blocked (1 model-backed block(s) skipped (no DEEPSEEK_API_KEY)); bullet 9: blocked (bullet is labeled blocked) | <scratch>/sdk/S15-nokey/transcripts/S15-browser-01.log |
| S15.observation | pass | c707d3737e8d | bullet 1: pass | <scratch>/sdk/S15-nokey/transcripts/S15-browser-01.log |
| S15.preset-browser | pass | c707d3737e8d | bullet 7: pass | <scratch>/sdk/S15-nokey/transcripts/S15-browser-07.log |
| S15.prompt-guidance | pass | c707d3737e8d | bullet 1: pass; bullet 2: blocked (block 1 is blocked (exit 3: a missing prerequisite)) | <scratch>/sdk/S15-nokey/transcripts/S15-browser-01.log |
| S15.recording-start-stop | pass | c707d3737e8d | bullet 1: pass; bullet 6: blocked (bullet is labeled blocked); bullet 9: blocked (bullet is labeled blocked) | <scratch>/sdk/S15-nokey/transcripts/S15-browser-01.log |
| S15.shared-executor | pass | c707d3737e8d | bullet 3: pass | <scratch>/sdk/S15-nokey/transcripts/S15-browser-03.log |
| S15.storage | pass | c707d3737e8d | bullet 1: pass | <scratch>/sdk/S15-nokey/transcripts/S15-browser-01.log |
| S15.tabs | pass | c707d3737e8d | bullet 1: pass | <scratch>/sdk/S15-nokey/transcripts/S15-browser-01.log |
| S15.toolset-create | pass | c707d3737e8d | bullet 1: pass | <scratch>/sdk/S15-nokey/transcripts/S15-browser-01.log |
| S16.oracle-consult | pass | c707d3737e8d | bullet 4: pass; bullet 8: blocked (1 model-backed block(s) skipped (no DEEPSEEK_API_KEY)) | <scratch>/sdk/S16-nokey/transcripts/S16-planning-workflow-tools-04.log |
| S16.oracle-errors | pass | c707d3737e8d | bullet 4: pass | <scratch>/sdk/S16-nokey/transcripts/S16-planning-workflow-tools-04.log |
| S16.oracle-tool | pass | c707d3737e8d | bullet 4: pass | <scratch>/sdk/S16-nokey/transcripts/S16-planning-workflow-tools-04.log |
| S16.plan-edit-restriction | pass | c707d3737e8d | bullet 2: pass | <scratch>/sdk/S16-nokey/transcripts/S16-planning-workflow-tools-02.log |
| S16.plan-file-headers | pass | c707d3737e8d | bullet 2: pass | <scratch>/sdk/S16-nokey/transcripts/S16-planning-workflow-tools-02.log |
| S16.plan-file-location | pass | c707d3737e8d | bullet 2: pass; bullet 3: pass | <scratch>/sdk/S16-nokey/transcripts/S16-planning-workflow-tools-02.log |
| S16.plan-then-execute | pass | c707d3737e8d | bullet 2: pass; bullet 7: blocked (1 model-backed block(s) skipped (no DEEPSEEK_API_KEY)) | <scratch>/sdk/S16-nokey/transcripts/S16-planning-workflow-tools-02.log |
| S16.planning-agent | pass | c707d3737e8d | bullet 2: pass; bullet 7: blocked (1 model-backed block(s) skipped (no DEEPSEEK_API_KEY)) | <scratch>/sdk/S16-nokey/transcripts/S16-planning-workflow-tools-02.log |
| S16.planning-tools | pass | c707d3737e8d | bullet 2: pass; bullet 3: pass | <scratch>/sdk/S16-nokey/transcripts/S16-planning-workflow-tools-02.log |
| S16.task-tracker-persistence | pass | c707d3737e8d | bullet 1: pass | <scratch>/sdk/S16-nokey/transcripts/S16-planning-workflow-tools-01.log |
| S16.task-tracker-plan-view | pass | c707d3737e8d | bullet 1: pass | <scratch>/sdk/S16-nokey/transcripts/S16-planning-workflow-tools-01.log |
| S16.tom-consult | pass | c707d3737e8d | bullet 5: pass; bullet 9: blocked (1 model-backed block(s) skipped (no DEEPSEEK_API_KEY)) | <scratch>/sdk/S16-nokey/transcripts/S16-planning-workflow-tools-05.log |
| S16.tom-sleeptime | pass | c707d3737e8d | bullet 5: pass; bullet 9: blocked (1 model-backed block(s) skipped (no DEEPSEEK_API_KEY)) | <scratch>/sdk/S16-nokey/transcripts/S16-planning-workflow-tools-05.log |
| S16.tom-storage-root | pass | c707d3737e8d | bullet 5: pass | <scratch>/sdk/S16-nokey/transcripts/S16-planning-workflow-tools-05.log |
| S16.workflow-errors-and-output | pass | c707d3737e8d | bullet 6: pass | <scratch>/sdk/S16-nokey/transcripts/S16-planning-workflow-tools-06.log |
| S16.workflow-fan-out | pass | c707d3737e8d | bullet 6: pass; bullet 10: blocked (1 model-backed block(s) skipped (no DEEPSEEK_API_KEY)) | <scratch>/sdk/S16-nokey/transcripts/S16-planning-workflow-tools-06.log |
| S16.workflow-lifecycle | pass | c707d3737e8d | bullet 6: pass | <scratch>/sdk/S16-nokey/transcripts/S16-planning-workflow-tools-06.log |
| S16.workflow-pipeline | pass | c707d3737e8d | bullet 6: pass | <scratch>/sdk/S16-nokey/transcripts/S16-planning-workflow-tools-06.log |
| S16.workflow-script-validation | pass | c707d3737e8d | bullet 6: pass | <scratch>/sdk/S16-nokey/transcripts/S16-planning-workflow-tools-06.log |
| S16.workflow-toolset | pass | c707d3737e8d | bullet 6: pass; bullet 10: blocked (1 model-backed block(s) skipped (no DEEPSEEK_API_KEY)) | <scratch>/sdk/S16-nokey/transcripts/S16-planning-workflow-tools-06.log |
| S17.agent-tool-init | pass | c707d3737e8d | bullet 5: pass | <scratch>/sdk/S17-nokey/transcripts/S17-custom-and-client-tools-05.log |
| S17.annotations-and-resources | pass | c707d3737e8d | bullet 1: pass; bullet 3: pass | <scratch>/sdk/S17-nokey/transcripts/S17-custom-and-client-tools-01.log |
| S17.auto-naming | pass | c707d3737e8d | bullet 2: pass | <scratch>/sdk/S17-nokey/transcripts/S17-custom-and-client-tools-02.log |
| S17.auto-registration | pass | c707d3737e8d | bullet 2: pass | <scratch>/sdk/S17-nokey/transcripts/S17-custom-and-client-tools-02.log |
| S17.catalog | pass | c707d3737e8d | bullet 2: pass | <scratch>/sdk/S17-nokey/transcripts/S17-custom-and-client-tools-02.log |
| S17.client-tool-ack | pass | c707d3737e8d | bullet 6: pass; bullet 9: blocked (1 model-backed block(s) skipped (no DEEPSEEK_API_KEY)) | <scratch>/sdk/S17-nokey/transcripts/S17-custom-and-client-tools-06.log |
| S17.client-tool-registration | pass | c707d3737e8d | bullet 7: pass | <scratch>/sdk/S17-nokey/transcripts/S17-custom-and-client-tools-07.log |
| S17.client-tool-schema | pass | c707d3737e8d | bullet 6: pass | <scratch>/sdk/S17-nokey/transcripts/S17-custom-and-client-tools-06.log |
| S17.client-tool-spec | pass | c707d3737e8d | bullet 7: pass | <scratch>/sdk/S17-nokey/transcripts/S17-custom-and-client-tools-07.log |
| S17.client-tools-conversation | pass | c707d3737e8d | bullet 6: pass; bullet 7: pass; bullet 9: blocked (1 model-backed block(s) skipped (no DEEPSEEK_API_KEY)) | <scratch>/sdk/S17-nokey/transcripts/S17-custom-and-client-tools-06.log |
| S17.custom-tool-definition | pass | c707d3737e8d | bullet 1: pass; bullet 3: pass; bullet 8: blocked (1 model-backed block(s) skipped (no DEEPSEEK_API_KEY)) | <scratch>/sdk/S17-nokey/transcripts/S17-custom-and-client-tools-01.log |
| S17.execute-tool | pass | c707d3737e8d | bullet 1: pass | <scratch>/sdk/S17-nokey/transcripts/S17-custom-and-client-tools-01.log |
| S17.injected-fields | pass | c707d3737e8d | bullet 3: pass | <scratch>/sdk/S17-nokey/transcripts/S17-custom-and-client-tools-03.log |
| S17.llm-schemas | pass | c707d3737e8d | bullet 3: pass | <scratch>/sdk/S17-nokey/transcripts/S17-custom-and-client-tools-03.log |
| S17.module-qualnames | pass | c707d3737e8d | bullet 2: pass | <scratch>/sdk/S17-nokey/transcripts/S17-custom-and-client-tools-02.log |
| S17.output-coercion | pass | c707d3737e8d | bullet 3: pass | <scratch>/sdk/S17-nokey/transcripts/S17-custom-and-client-tools-03.log |
| S17.py-type | pass | c707d3737e8d | bullet 3: pass | <scratch>/sdk/S17-nokey/transcripts/S17-custom-and-client-tools-03.log |
| S17.register-tool | pass | c707d3737e8d | bullet 2: pass | <scratch>/sdk/S17-nokey/transcripts/S17-custom-and-client-tools-02.log |
| S17.resolve-tool | pass | c707d3737e8d | bullet 1: pass; bullet 2: pass | <scratch>/sdk/S17-nokey/transcripts/S17-custom-and-client-tools-01.log |
| S17.schema-serialization | pass | c707d3737e8d | bullet 3: pass; bullet 4: pass | <scratch>/sdk/S17-nokey/transcripts/S17-custom-and-client-tools-03.log |
| S17.tool-set | pass | c707d3737e8d | bullet 1: pass; bullet 8: blocked (1 model-backed block(s) skipped (no DEEPSEEK_API_KEY)) | <scratch>/sdk/S17-nokey/transcripts/S17-custom-and-client-tools-01.log |
| S17.tool-spec | pass | c707d3737e8d | bullet 1: pass; bullet 2: pass | <scratch>/sdk/S17-nokey/transcripts/S17-custom-and-client-tools-01.log |
| S17.usability | pass | c707d3737e8d | bullet 2: pass | <scratch>/sdk/S17-nokey/transcripts/S17-custom-and-client-tools-02.log |
| S18.async-remote | pass | 1ecd2de9dc6b | bullet 2: pass | <scratch>/sdk/S18-nokey/transcripts/S18-workspace-backends-02.log |
| S18.cloud-automation-tags | pass | 1ecd2de9dc6b | bullet 7: pass | <scratch>/sdk/S18-nokey/transcripts/S18-workspace-backends-07.log |
| S18.cloud-credentials | pass | 1ecd2de9dc6b | bullet 7: pass; bullet 10: blocked (bullet is labeled blocked) | <scratch>/sdk/S18-nokey/transcripts/S18-workspace-backends-07.log |
| S18.cloud-local-mode | pass | 1ecd2de9dc6b | bullet 7: pass | <scratch>/sdk/S18-nokey/transcripts/S18-workspace-backends-07.log |
| S18.completion-callback | pass | 1ecd2de9dc6b | bullet 1: pass; bullet 5: pass | <scratch>/sdk/S18-nokey/transcripts/S18-workspace-backends-01.log |
| S18.conversation-binding | pass | 1ecd2de9dc6b | bullet 1: pass; bullet 5: pass; bullet 8: blocked (1 model-backed block(s) skipped (no DEEPSEEK_API_KEY)) | <scratch>/sdk/S18-nokey/transcripts/S18-workspace-backends-01.log |
| S18.local-ops | pass | 1ecd2de9dc6b | bullet 1: pass | <scratch>/sdk/S18-nokey/transcripts/S18-workspace-backends-01.log |
| S18.local-pause-resume | pass | 1ecd2de9dc6b | bullet 1: pass | <scratch>/sdk/S18-nokey/transcripts/S18-workspace-backends-01.log |
| S18.remote-alive | pass | 1ecd2de9dc6b | bullet 2: pass | <scratch>/sdk/S18-nokey/transcripts/S18-workspace-backends-02.log |
| S18.remote-client | pass | 1ecd2de9dc6b | bullet 2: pass | <scratch>/sdk/S18-nokey/transcripts/S18-workspace-backends-02.log |
| S18.remote-command-exec | pass | 1ecd2de9dc6b | bullet 2: pass; bullet 8: blocked (1 model-backed block(s) skipped (no DEEPSEEK_API_KEY)) | <scratch>/sdk/S18-nokey/transcripts/S18-workspace-backends-02.log |
| S18.remote-files-git | pass | 1ecd2de9dc6b | bullet 2: pass | <scratch>/sdk/S18-nokey/transcripts/S18-workspace-backends-02.log |
| S18.remote-settings | pass | 1ecd2de9dc6b | bullet 4: pass; bullet 8: blocked (1 model-backed block(s) skipped (no DEEPSEEK_API_KEY)) | <scratch>/sdk/S18-nokey/transcripts/S18-workspace-backends-04.log |
| S18.repos-and-skills | pass | 1ecd2de9dc6b | bullet 6: pass | <scratch>/sdk/S18-nokey/transcripts/S18-workspace-backends-06.log |
| S18.runtime-scope | pass | 1ecd2de9dc6b | bullet 5: pass | <scratch>/sdk/S18-nokey/transcripts/S18-workspace-backends-05.log |
| S19.acp-providers-catalog | pass | 5ff99f1b6ca2 | bullet 4: pass; bullet 5: pass | <scratch>/sdk/S19/transcripts/S19-settings-04.log |
| S19.acp-settings | pass | 5ff99f1b6ca2 | bullet 2: pass | <scratch>/sdk/S19/transcripts/S19-settings-02.log |
| S19.apply-diff | pass | 5ff99f1b6ca2 | bullet 2: pass | <scratch>/sdk/S19/transcripts/S19-settings-02.log |
| S19.condenser-settings | pass | 5ff99f1b6ca2 | bullet 1: pass; bullet 10: pass | <scratch>/sdk/S19/transcripts/S19-settings-01.log |
| S19.conversation-settings | pass | 5ff99f1b6ca2 | bullet 2: pass | <scratch>/sdk/S19/transcripts/S19-settings-02.log |
| S19.credential-binding | pass | 5ff99f1b6ca2 | bullet 9: pass | <scratch>/sdk/S19/transcripts/S19-settings-09.log |
| S19.mcp-config-secrets | pass | 5ff99f1b6ca2 | bullet 1: pass | <scratch>/sdk/S19/transcripts/S19-settings-01.log |
| S19.persisted-baselines | pass | 5ff99f1b6ca2 | bullet 6: pass; bullet 7: pass | <scratch>/sdk/S19/transcripts/S19-settings-06.log |
| S19.profile-migrations | pass | 5ff99f1b6ca2 | bullet 8: pass | <scratch>/sdk/S19/transcripts/S19-settings-08.log |
| S19.profile-model | pass | 5ff99f1b6ca2 | bullet 8: pass | <scratch>/sdk/S19/transcripts/S19-settings-08.log |
| S19.profile-refs | pass | 5ff99f1b6ca2 | bullet 8: pass | <scratch>/sdk/S19/transcripts/S19-settings-08.log |
| S19.profile-resolve | pass | 5ff99f1b6ca2 | bullet 8: pass | <scratch>/sdk/S19/transcripts/S19-settings-08.log |
| S19.profile-seed | pass | 5ff99f1b6ca2 | bullet 8: pass | <scratch>/sdk/S19/transcripts/S19-settings-08.log |
| S19.profile-store | pass | 5ff99f1b6ca2 | bullet 8: pass | <scratch>/sdk/S19/transcripts/S19-settings-08.log |
| S19.pypi-baseline-gate | pass | 5ff99f1b6ca2 | bullet 7: pass | <scratch>/sdk/S19/transcripts/S19-settings-07.log |
| S19.retired-switches | pass | 5ff99f1b6ca2 | bullet 1: pass | <scratch>/sdk/S19/transcripts/S19-settings-01.log |
| S19.schema-export | pass | 5ff99f1b6ca2 | bullet 2: pass | <scratch>/sdk/S19/transcripts/S19-settings-02.log |
| S19.schema-migrations | pass | 5ff99f1b6ca2 | bullet 2: pass | <scratch>/sdk/S19/transcripts/S19-settings-02.log |
| S19.settings-create-agent | pass | 5ff99f1b6ca2 | bullet 1: pass; bullet 10: pass | <scratch>/sdk/S19/transcripts/S19-settings-01.log |
| S19.settings-roundtrip | pass | 5ff99f1b6ca2 | bullet 1: pass; bullet 10: pass | <scratch>/sdk/S19/transcripts/S19-settings-01.log |
| S19.settings-tools | pass | 5ff99f1b6ca2 | bullet 1: pass; bullet 10: pass | <scratch>/sdk/S19/transcripts/S19-settings-01.log |
| S19.subscription-llm-settings | pass | 5ff99f1b6ca2 | bullet 2: pass; bullet 3: blocked (bullet is labeled blocked) | <scratch>/sdk/S19/transcripts/S19-settings-02.log |
| S19.tool-concurrency-persona | pass | 5ff99f1b6ca2 | bullet 1: pass | <scratch>/sdk/S19/transcripts/S19-settings-01.log |
| S19.validate-dispatch | pass | 5ff99f1b6ca2 | bullet 2: pass | <scratch>/sdk/S19/transcripts/S19-settings-02.log |
| S19.verification-settings | pass | 5ff99f1b6ca2 | bullet 1: pass | <scratch>/sdk/S19/transcripts/S19-settings-01.log |
| S20.automation-context | pass | 0da9cf850a0e | bullet 7: pass | <scratch>/sdk/S20-nokey/transcripts/S20-observability-07.log |
| S20.automation-headers | pass | 0da9cf850a0e | bullet 7: pass | <scratch>/sdk/S20-nokey/transcripts/S20-observability-07.log |
| S20.automation-kwargs | pass | 0da9cf850a0e | bullet 7: pass | <scratch>/sdk/S20-nokey/transcripts/S20-observability-07.log |
| S20.conversation-args | pass | 0da9cf850a0e | bullet 1: pass | <scratch>/sdk/S20-nokey/transcripts/S20-observability-01.log |
| S20.delegate-trace | pass | 0da9cf850a0e | bullet 5: pass | <scratch>/sdk/S20-nokey/transcripts/S20-observability-05.log |
| S20.enable-gate | pass | 0da9cf850a0e | bullet 1: pass; bullet 3: pass; bullet 6: pass; bullet 7: pass | <scratch>/sdk/S20-nokey/transcripts/S20-observability-01.log |
| S20.env-conversation-defaults | pass | 0da9cf850a0e | bullet 2: pass | <scratch>/sdk/S20-nokey/transcripts/S20-observability-02.log |
| S20.external-entry | pass | 0da9cf850a0e | bullet 2: pass; bullet 6: pass | <scratch>/sdk/S20-nokey/transcripts/S20-observability-02.log |
| S20.get-env | pass | 0da9cf850a0e | bullet 7: pass | <scratch>/sdk/S20-nokey/transcripts/S20-observability-07.log |
| S20.laminar-init | pass | 0da9cf850a0e | bullet 3: pass; bullet 10: blocked (bullet is labeled blocked) | <scratch>/sdk/S20-nokey/transcripts/S20-observability-03.log |
| S20.lazy-import | pass | 0da9cf850a0e | bullet 6: pass | <scratch>/sdk/S20-nokey/transcripts/S20-observability-06.log |
| S20.llm-span | pass | 0da9cf850a0e | bullet 4: pass; bullet 9: blocked (1 model-backed block(s) skipped (no DEEPSEEK_API_KEY)) | <scratch>/sdk/S20-nokey/transcripts/S20-observability-04.log |
| S20.logger-config | pass | 0da9cf850a0e | bullet 8: pass | <scratch>/sdk/S20-nokey/transcripts/S20-observability-08.log |
| S20.logger-quiet-thirdparty | pass | 0da9cf850a0e | bullet 8: pass | <scratch>/sdk/S20-nokey/transcripts/S20-observability-08.log |
| S20.otel-backend | pass | 0da9cf850a0e | bullet 1: pass | <scratch>/sdk/S20-nokey/transcripts/S20-observability-01.log |
| S20.parallel-span-context | pass | 0da9cf850a0e | bullet 1: pass | <scratch>/sdk/S20-nokey/transcripts/S20-observability-01.log |
| S20.rolling-log-view | pass | 0da9cf850a0e | bullet 8: pass | <scratch>/sdk/S20-nokey/transcripts/S20-observability-08.log |
| S20.root-span | pass | 0da9cf850a0e | bullet 1: pass; bullet 9: blocked (1 model-backed block(s) skipped (no DEEPSEEK_API_KEY)); bullet 10: blocked (bullet is labeled blocked) | <scratch>/sdk/S20-nokey/transcripts/S20-observability-01.log |
| S20.step-and-tool-spans | pass | 0da9cf850a0e | bullet 1: pass; bullet 9: blocked (1 model-backed block(s) skipped (no DEEPSEEK_API_KEY)) | <scratch>/sdk/S20-nokey/transcripts/S20-observability-01.log |
| S20.utility-operation-metadata | pass | 0da9cf850a0e | bullet 4: pass | <scratch>/sdk/S20-nokey/transcripts/S20-observability-04.log |
| S21.auth-selection | pass | 07e3bb94b792 | bullet 8: pass | <scratch>/sdk/S21-effort/transcripts/S21-acp-agent-08.log |
| S21.cancel-pause | pass | 07e3bb94b792 | bullet 7: pass | <scratch>/sdk/S21-effort/transcripts/S21-acp-agent-07.log |
| S21.cleanup | pass | 07e3bb94b792 | bullet 1: pass | <scratch>/sdk/S21-effort/transcripts/S21-acp-agent-01.log |
| S21.construct | pass | 07e3bb94b792 | bullet 1: pass | <scratch>/sdk/S21-effort/transcripts/S21-acp-agent-01.log |
| S21.effort | pass | 07e3bb94b792 | bullet 10: pass | <scratch>/sdk/S21-effort/transcripts/S21-acp-agent-10.log |
| S21.effort-switch | pass | 07e3bb94b792 | bullet 10: pass | <scratch>/sdk/S21-effort/transcripts/S21-acp-agent-10.log |
| S21.file-secrets | pass | 07e3bb94b792 | bullet 8: pass | <scratch>/sdk/S21-effort/transcripts/S21-acp-agent-08.log |
| S21.handshake-live | pass | 07e3bb94b792 | bullet 13: pass | <scratch>/sdk/S21-effort/transcripts/S21-acp-agent-13.log |
| S21.init-state | pass | 07e3bb94b792 | bullet 1: pass; bullet 4: pass | <scratch>/sdk/S21-effort/transcripts/S21-acp-agent-01.log |
| S21.masking | pass | 07e3bb94b792 | bullet 1: pass | <scratch>/sdk/S21-effort/transcripts/S21-acp-agent-01.log |
| S21.mcp-forwarding | pass | 07e3bb94b792 | bullet 1: pass | <scratch>/sdk/S21-effort/transcripts/S21-acp-agent-01.log |
| S21.model-discovery | pass | 07e3bb94b792 | bullet 9: pass; bullet 10: pass | <scratch>/sdk/S21-effort/transcripts/S21-acp-agent-09.log |
| S21.model-selection | pass | 07e3bb94b792 | bullet 1: pass; bullet 3: pass; bullet 8: pass | <scratch>/sdk/S21-effort/transcripts/S21-acp-agent-01.log |
| S21.model-switch | pass | 07e3bb94b792 | bullet 1: pass | <scratch>/sdk/S21-effort/transcripts/S21-acp-agent-01.log |
| S21.permissions-auto-approve | pass | 07e3bb94b792 | bullet 1: pass | <scratch>/sdk/S21-effort/transcripts/S21-acp-agent-01.log |
| S21.provider-registry | pass | 07e3bb94b792 | bullet 12: pass; bullet 13: pass | <scratch>/sdk/S21-effort/transcripts/S21-acp-agent-12.log |
| S21.secrets-env | pass | 07e3bb94b792 | bullet 1: pass; bullet 8: pass | <scratch>/sdk/S21-effort/transcripts/S21-acp-agent-01.log |
| S21.session-resume | pass | 07e3bb94b792 | bullet 3: pass | <scratch>/sdk/S21-effort/transcripts/S21-acp-agent-03.log |
| S21.settings | pass | 07e3bb94b792 | bullet 9: pass; bullet 10: pass | <scratch>/sdk/S21-effort/transcripts/S21-acp-agent-09.log |
| S21.step | pass | 07e3bb94b792 | bullet 1: pass; bullet 14: blocked (bullet is labeled blocked) | <scratch>/sdk/S21-effort/transcripts/S21-acp-agent-01.log |
| S21.tool-call-events | pass | 07e3bb94b792 | bullet 1: pass | <scratch>/sdk/S21-effort/transcripts/S21-acp-agent-01.log |
| S21.tracing | pass | 07e3bb94b792 | bullet 11: pass | <scratch>/sdk/S21-effort/transcripts/S21-acp-agent-11.log |
| S21.usage-cost | pass | 07e3bb94b792 | bullet 1: pass; bullet 14: blocked (bullet is labeled blocked) | <scratch>/sdk/S21-effort/transcripts/S21-acp-agent-01.log |
