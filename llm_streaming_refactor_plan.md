# LLM Streaming Refactor Plan

## Observed LiteLLM stream event types

LiteLLM emits `ResponsesAPIStreamEvents` values while streaming. The current enum and their string payloads are:

- `response.created`
- `response.in_progress`
- `response.completed`
- `response.failed`
- `response.incomplete`
- `response.output_item.added`
- `response.output_item.done`
- `response.output_text.delta`
- `response.output_text.done`
- `response.output_text.annotation.added`
- `response.reasoning_summary_text.delta`
- `response.reasoning_summary_part.added`
- `response.function_call_arguments.delta`
- `response.function_call_arguments.done`
- `response.mcp_call_arguments.delta`
- `response.mcp_call_arguments.done`
- `response.mcp_call.in_progress`
- `response.mcp_call.completed`
- `response.mcp_call.failed`
- `response.mcp_list_tools.in_progress`
- `response.mcp_list_tools.completed`
- `response.mcp_list_tools.failed`
- `response.file_search_call.in_progress`
- `response.file_search_call.searching`
- `response.file_search_call.completed`
- `response.web_search_call.in_progress`
- `response.web_search_call.searching`
- `response.web_search_call.completed`
- `response.refusal.delta`
- `response.refusal.done`
- `error`
- `response.content_part.added`
- `response.content_part.done`

These events conceptually fall into buckets we care about for visualization and higher-level semantics:

| Category | Events | Notes |
| --- | --- | --- |
| **Lifecycle / status** | created, in_progress, completed, failed, incomplete, *_call.* events, output_item.added/done, content_part.added/done, error | remind our UI but typically not shown inline |
| **Assistant text** | output_text.delta, output_text.done, output_text.annotation.added | forms "Message" body |
| **Reasoning summary** | reasoning_summary_part.added, reasoning_summary_text.delta | feed into Reasoning blobs |
| **Function / tool arguments** | function_call_arguments.delta/done, mcp_call_arguments.delta/done | update Action sections |
| **Refusal** | refusal.delta/done | render special refusal text |

## Problems to resolve

1. **Streaming display duplicates content and forces line breaks.** We currently print each delta as its own Rich print call with `end=""`, but Live panels aren’t used and the console injects newlines between `print` calls, so output becomes `word\nword\n...`.
2. **No per-message aggregation.** All reasoning deltas accumulate into a single global area, so later messages overwrite earlier context. We need separate buffers per "logical container" (assistant message, reasoning summary, function call) associated with the owning `LLMConvertibleEvent` (e.g., `MessageEvent`, `ActionEvent`).
3. **Naming collision / clarity.** LiteLLM "events" clash with our own domain events. We should introduce a distinct abstraction, e.g. `LLMStreamChunk`, that wraps metadata about channel, indices, and owning response item.
4. **Persistence & replay.** We still want to persist raw stream parts for clients, but the visualizer should rebuild high-level fragments from these parts when replaying history.

## Proposed model hierarchy

```
LLMStreamChunk (renamed from LLMStreamEvent)
├── part_kind: Literal["assistant", "reasoning", "function_arguments", "refusal", "status", "tool_output"]
├── text_delta: str | None
├── arguments_delta: str | None
├── response_index: int | None
├── item_id: str | None
├── chunk_type: str  # raw LiteLLM value
├── is_terminal: bool
├── raw_chunk: Any  # original LiteLLM payload retained for logging/replay
└── origin_metadata: dict[str, Any]
```

Keeping the raw LiteLLM payload inside each `LLMStreamChunk` means we do **not** need a separate envelope structure; logging can simply serialize the chunk directly.

## Visualization strategy

We will leave the existing `ConversationVisualizer` untouched for default/legacy usage and introduce a new `StreamingConversationVisualizer` that renders deltas directly inside the final panels:

1. **Create/update per-response panels.** The first chunk for a `(response_id, output_index)` pair creates (or reuses) a panel for the assistant message or tool call and immediately starts streaming into it.
2. **Route text into semantic sections.** Assistant text, reasoning summaries, function-call arguments, tool output, and refusals each update their own section inside the panel.
3. **Use Rich `Live` when interactive.** In a real terminal we keep the panel on screen and update it in place; when the console is not interactive (tests, logging) we fall back to static updates.
4. **Leave the panel in place when finished.** When the final chunk arrives we stop updating but keep the panel visible; the subsequent `MessageEvent`/`ActionEvent` is suppressed to avoid duplicate re-rendering.

## Implementation roadmap

1. **Data model adjustments**
   - Rename the existing `LLMStreamEvent` class to `LLMStreamChunk` and extend it with richer fields: `part_kind`, `response_index`, `conversation_event_id` (populated later), `raw_chunk`, etc.
   - Create helper to classify LiteLLM chunks into `LLMStreamChunk` instances (including mapping item IDs to owning role/time).

2. **Conversation state integration**
   - When we enqueue the initial `MessageEvent`/`ActionEvent`, cache a lookup (e.g., `inflight_streams[(response_id, output_index)] = conversation_event_id`).
   - Update `LocalConversation` token callback wrapper to attach the resolved conversation event ID onto the `LLMStreamChunk` before emitting/persisting.

3. **Streaming visualizer**
   - Implement `StreamingConversationVisualizer` with lightweight session tracking (keyed by response/output) that owns Rich panels for streaming sections.
   - Stream updates into the same panel that will remain visible after completion; use `Live` only when running in an interactive terminal.
   - Suppress duplicate rendering when the final `MessageEvent`/`ActionEvent` arrives, since the streamed panel already contains the content.
   - Provide a factory helper (e.g., `create_streaming_visualizer`) for callers that want the streaming experience.

4. **Persistence / tests**
   - Update tests to ensure:
     - Multiple output_text deltas produce contiguous text without duplicates or extra newlines.
     - Separate reasoning items create separate entries under one message event.
     - Function call arguments stream into their own block.
   - Add snapshot/log assertions to confirm persisted JSONL remains unchanged for downstream clients.

5. **Documentation & naming cleanup**
   - Decide on final terminology (`LLMStreamChunk`, `StreamItem`, etc.) and update code comments accordingly.
   - Document the classification table for future maintainers.

## Next actions

- [ ] Refactor classifier to output `LLMStreamChunk` objects with clear `part_kind`.
- [ ] Track in-flight conversation events so parts know their owner.
- [ ] Replace print-based visualizer streaming with `Live` blocks per section.
- [ ] Extend unit tests to cover multiple messages, reasoning segments, tool calls, and the new streaming visualizer.
- [ ] Update the standalone streaming example to wire in the streaming visualizer helper.
- [ ] Manually validate with long streaming example to confirm smooth in-place updates.
