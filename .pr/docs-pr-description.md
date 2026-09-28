- [x] I have read and reviewed the documentation changes to the best of my ability.
- [x] If the change is significant, I have run the documentation site locally and confirmed it renders as expected.

**Summary of changes**

Document the opt-in `NotesRetrievalCondenser`, native context notes/history tools, and local storage protection. The guide explains committed notes versions, paginated retrieval, context reset behavior, the disabled-by-default storage policy, and explicit recovery through Python, TypeScript, or Agent Server HTTP APIs.

Add the guide to SDK navigation and link it from the existing condenser guide. Distinguish ordinary low-space retry from write-failure recovery and explicit branch selection after an ambiguous restart.

Companion SDK PR: https://github.com/OpenHands/software-agent-sdk/pull/5312. Merge the implementation before these docs.

**Validation**

- `npm exec --yes --package=mint -- mint --telemetry=false broken-links` — passed, no broken links.
- `npm exec --yes --package=mint -- mint --telemetry=false validate` — strict build validation passed.
- Local Mintlify preview inspected in Chromium: SDK navigation, guide layout, and the Python/TypeScript recovery tabs rendered correctly.
- Python setup and read-only EventLog inspection executed against the companion SDK with `TestLLM`; all three Python snippets parsed and no LLM API was called.
- TypeScript recovery snippet passed strict `tsc` against the companion client sources.
- The guide appears exactly once in SDK navigation; linked documentation targets and `git diff --check` passed.

_Prepared with Codex assistance on behalf of cbinhan._
