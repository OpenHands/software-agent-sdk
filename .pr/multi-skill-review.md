# Consolidated review — 2026-10-04

Scope: the issue #4976 contribution against the local upstream baseline,
including the uncommitted compatibility fixes from 2026-10-03. Read-only
production-code review; this report does not implement recommendations.

## Repository style and maintainability check — 2026-10-04

Rechecked the full branch against root/SDK `AGENTS.md`, the repository review
guide, and nearby LLM code (`llm_profile_store.py`, `llm_response.py`, and
`utils/runtime_metadata.py`). No additional structural blocker was found.

- Provider normalization remains at the LLM boundary, using Pydantic models;
  opaque fields stay `object` rather than being arbitrarily constrained.
- Runtime Protocols follow existing SDK practice. Generic Pydantic events
  receive explicit projection where static attribute inspection is inadequate.
- The three focused helper modules are 91, 99, and 102 lines. `llm.py` shrinks
  from 3,306 to 3,254 lines; no changed file crosses the 1,000-line threshold.
- No new dependency, type-ignore suppression, import-path hack, public export,
  persisted field, or REST contract is introduced.
- Permanent regressions live under `tests/sdk/llm`; the exhaustive development
  matrix and transport/packaging evidence remain temporary `.pr/` artifacts,
  as required by repository policy.

No cosmetic rewrite was made: merging the distinct provider projections or
introducing a general adapter framework would obscure their different
consumption contracts. The conditional malformed-input concern below remains
documented rather than being represented as resolved by a style check.

## Review approaches

The article lists five approaches:
https://galvered.com/blog/best-code-review-skill/

| Approach | Source and execution | Outcome |
| --- | --- | --- |
| Vercel Code Review | `vercel-labs/open-agents/.agents/skills/code-review/SKILL.md`; separate reviewer | Confirmed generic-event regression; no additional concrete tokenizer defect |
| Thermo-Nuclear Code Quality Review | Installed skill and matching `cursor/plugins/cursor-team-kit/skills/thermo-nuclear-code-quality-review/SKILL.md`; primary reviewer | Helper extraction is appropriate; no production file crosses 1,000 lines because of this change; no broad rewrite recommended |
| Adversarial Reviewer | `alirezarezvani/claude-skills/engineering-team/skills/adversarial-reviewer/SKILL.md`; primary reviewer, sequential production-breakage/maintainability/security perspectives | Runtime protocols hide dynamic provider fields; no separate demonstrated credential vulnerability |
| Sentry Code Review | `getsentry/skills/skills/code-review/SKILL.md`; separate reviewer | Remaining tolerant-input compatibility changes; 72 focused tests passed |
| Plain `/review` | Article provides no source for the author's private copy | General correctness/test/scope review used as a fallback; not claimed to be the unavailable original skill |

All four published instructions were read. Duplicate findings were merged.
Severity is based on demonstrated impact, not inflated merely because several
reviewers identified the same issue. Findings from malformed provider inputs
are distinguished from schema-conforming provider data.

## 1. P2 — Generic output-item events are silently discarded

**Resolved locally on 2026-10-04.** The adapter now projects generic LiteLLM
events into declared fields. Six failing generic-event regressions now pass
across sync, async, and async-with-sync-stream requests; all 240 reconstruction
matrix rows use `GenericEvent`. The discussion below records the original
finding. The speculative dynamic completion-wrapper concern was not changed.

Location: `openhands-sdk/openhands/sdk/llm/_response_stream.py:52` and
`openhands-sdk/openhands/sdk/llm/llm.py:1127`.

On the supported Python 3.12+ versions, runtime Protocol checks use static
attribute inspection. LiteLLM generic Pydantic objects expose extra fields
through `__getattr__`; direct attribute access succeeds but the Protocol check
fails.

Reproduction:

```python
event = BaseLiteLLMOpenAIResponseObject(
    type="response.output_item.done",
    item={
        "type": "message",
        "content": [{"type": "output_text", "text": "hello"}],
    },
)
assert event.item
assert not isinstance(event, OutputItemEvent)
assert list(response_events([event])) == []
```

The installed LiteLLM Responses stream union explicitly includes `GenericEvent`
and `BaseLiteLLMOpenAIResponseObject`. A full SDK request with this item-done
event and a typed completion carrying `output=[]` produces an empty Message;
the upstream implementation collects the item and returns `"hello"`.

The same root cause applies to a wrapper exposing only `completed_response`
through `__getattr__`: a minimal proxy works upstream and errors in the current
implementation. No concrete installed producer of that wrapper shape was
found, so this is supporting compatibility evidence, not a separate finding.

Recommendation: normalize generic provider events to a declared output-item
contract at the adapter boundary. Keep core consumption typed. Do not simply
broaden the Protocol union: static checks still miss these dynamic fields.
Cover generic-event reconstruction in the existing sync/async parameterized
test rather than multiplying the entire stream-state product. Consider the
same boundary treatment for dynamically exposed wrapper completion state.

## 2. Conditional compatibility concern — Previously tolerated empty/fallback values

Location: `openhands-sdk/openhands/sdk/llm/_message_normalization.py:35` and
`:50`, used by `normalize_response_output`.

| Input | Upstream | Current |
| --- | --- | --- |
| Message `content=""` or `content={}` | Empty assistant Message | ValidationError |
| Reasoning `summary=""` | Empty summary | ValidationError |
| Reasoning `summary=[None]` | Summary `[""]` | ValidationError |
| Reasoning `content=[42]` | Content `[""]` | ValidationError |

These values violate standard provider schemas. However, the old generic
conversion intentionally used falsy-collection defaults and missing-text
defaults; the refactor now aborts the response instead. No real provider
currently emitting these malformed shapes was demonstrated.

Recommendation: preserve the existing tolerant defaults with small boundary
normalizers, using one falsy-collection case and one primitive reasoning-part
case in permanent tests. Otherwise explicitly narrow the behavior-preservation
claim. Do not build a general compatibility framework.

New validation exceptions can also include previously discarded input in
`str(ValidationError)`, which may reach ConversationErrorEvent.detail. This is
not a proven credential leak. If intentional validation errors remain,
`hide_input_in_errors=True` is an optional defensive measure for these private
provider projections.

## Test and evidence recommendations

- Keep the eight recently added compatibility tests; those fixes are correct.
- Add provider event representation as a boundary axis: concrete typed event,
  generic LiteLLM/Pydantic event, and existing attribute object. The current
  matrix uses attribute objects for item-done events and misses generic extras.
- In the 80 completion-error matrix cases, assert prior callback events outside
  the helper that raises. Its current callback assertions occur after response
  construction and are unreachable when the expected exception occurs.
- Report full-suite evidence accurately: the most recent run has 6,460 passes
  and one unrelated model-feature failure. A passing custom matrix is not an
  all-green SDK run. No full-suite rerun was performed during this review.

## Architecture recommendation

Keep the three private helpers and the narrow scope. The existing large LLM
file shrinks; authentication uses an already-declared field; no dependency or
public persisted schema change is introduced. The main problem is choosing a
runtime Protocol check where a provider normalization adapter is needed.
Replacing that gate is more useful than reorganizing the whole LLM class or
reducing tokenizer protocols merely for line count.

Verdict: fix finding 1 before opening the PR. Prefer resolving the small
compatibility defaults in finding 2 as well, then rerun the affected tests and
validation matrix. No production code, commits, PRs, or external comments were
changed by this review.
