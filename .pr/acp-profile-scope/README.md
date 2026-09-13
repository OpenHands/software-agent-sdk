# Live Canvas: ACP credential scope on launch and resume

The empty profile allowlist now excludes a saved Codex credential and a credential supplied when resuming the conversation. Both failures were reproduced before the fix and disappear afterward in actual Agent Canvas conversations.

| Comparison | Before | After |
| --- | --- | --- |
| SDK commit | `902d95d15a027c199d741d093ec3416b9038a7c1` | `49936cdbc246d58ed324bc9c3c093d864c4b42ee` |
| Saved credential: ACP subprocess receives managed auth file | Yes | No |
| Resume-supplied credential: ACP subprocess receives managed auth file | Yes | No |
| Resume conversation's persisted registry contains `CODEX_AUTH_JSON` | Yes | No |
| Launch provenance records `secret_refs: []` | No | Yes |

[Animated comparison](acp-scope-before-after.gif) shows three before frames, then three after frames: the unchecked credential in the profile editor, the initial agent response, and the resumed agent response. Each frame lasts four seconds. The numbered PNGs provide individual reviewable screenshots.

## What ran

The same Canvas build (`9874c820a23e4026483ad9540ec9cb56194ce3ec`) used two sequential isolated local SDK instances. Each had fresh settings containing only synthetic credentials and a Codex ACP profile with `secret_refs=[]`. The profile was inspected in Canvas without saving changes, and the initial conversation was started from Canvas.

The agent was an explicitly configured deterministic ACP subprocess using the official Python ACP transport. The saved profile retains `acp_server="codex"` so the real Codex credential-binding path executes; Canvas labels its overridden executable as the Custom preset. The profile editor was not saved. The subprocess checked only whether the SDK-provided `CODEX_HOME/auth.json` existed and returned that boolean through the real ACP session into Canvas chat. It did not read or print credential contents, invoke Codex, or call a model. Automatic conversation titles used a separate deterministic local provider.

For the resume case, the saved credential was deleted through `typescript-client`'s `SettingsClient`. `ConversationClient.createConversation` created a new empty-scope conversation, then resumed the same ID with a synthetic `CODEX_AUTH_JSON` request secret. A message sent in Canvas ran the ACP process. All four conversations finished. Allowlisted persisted metadata independently confirms the actual resumed conversation's registry changes from `["CODEX_AUTH_JSON"]` before to `[]` after. A separate canonical `ConversationService` probe also confirms the initial managed-binding map changes from containing that name to empty.

## Scope and limitations

This proves local ACP secret exclusion on initial launch and live resume. It does not claim a real Codex authentication/model turn, Docker enforcement, or cold-restart behavior. Existing automated regressions cover additional paths separately. The subsequent explicit secret-update fix is covered by regression tests, not by this recording; its scope does not change the launch/resume observations at the recorded commit.

The initial conversation screenshots contain an unrelated file API 404 toast: this Canvas build expects the separate scoped-runtime API, which is outside this SDK branch. The chat and ACP transport worked on both versions; this report does not assert file-browser compatibility for that combination.

Two preliminary setup trials lacked the explicit title LLM profile and produced a default-provider authentication error with no real key. Those trials were excluded from the GIF and assertions. Final captures used the local title fixture. No real account credential was loaded. A 3 GiB available-memory guard protected the host; no guard interruption occurred. All private services and proof tabs were stopped after capture.
