# 1. OBJECTIVE

Add an `autotitle: bool = True` field to the `StartConversationRequest` model. When enabled, the conversation title is automatically generated (using the conversation's own LLM) the moment the first user message arrives, replacing the fragile manual `POST /generate_title` workflow.

# 2. CONTEXT SUMMARY

**Relevant files:**
- `openhands-agent-server/openhands/agent_server/models.py` — defines `StartConversationRequest` and `StoredConversation` (which inherits it)
- `openhands-agent-server/openhands/agent_server/conversation_service.py` — orchestrates conversation lifecycle; `_start_event_service` registers event subscribers; `_EventSubscriber` and `WebhookSubscriber` are existing subscriber examples
- `openhands-agent-server/openhands/agent_server/event_service.py` — `EventService.generate_title()` and `save_meta()` are the async methods to call
- `openhands-sdk/openhands/sdk/conversation/title_utils.py` — underlying title generation logic (LLM call + fallback truncation); already used by `generate_title`
- `openhands-sdk/openhands/sdk/event/__init__.py` — exports `MessageEvent` (a user-sent message has `source == "user"`)
- `tests/agent_server/test_conversation_service.py` — unit tests for `ConversationService`
- `tests/agent_server/test_conversation_router.py` — unit tests for the router endpoints

**Constraints:**
- `StoredConversation` extends `StartConversationRequest`, so any new field added there is automatically persisted to `meta.json` and reloaded on server restart.
- Title generation is a blocking LLM call and must run in a background task to avoid blocking the event stream.
- Title must only be generated once (guard against multiple user messages and server restarts).

# 3. APPROACH OVERVIEW

Add an `AutoTitleSubscriber` class in `conversation_service.py` that listens to the event stream of a conversation. When the first user `MessageEvent` arrives and the conversation's `stored.title` is still `None`, it fires a background task that calls `event_service.generate_title()`, sets `stored.title`, and calls `save_meta()`.

The subscriber is registered inside `_start_event_service` when `stored.autotitle is True` and `stored.title is None`. The `stored.title is None` guard at registration time prevents needlessly attaching the subscriber to conversations that already have a title (e.g. on server restart).

This approach was chosen over alternatives (e.g. triggering from the router after `send_message`) because:
- It works for both the `initial_message` path in `start_conversation` and for messages sent later via the event router.
- It reuses the existing subscriber pattern already established in the file.
- It is self-contained: no changes to the router or event service are needed.

# 4. IMPLEMENTATION STEPS

## Step 1 — Create a new git branch off main

**Goal:** Isolate this work from main.  
**Method:** `git checkout -b feat/autotitle-on-first-message main`

## Step 2 — Add `autotitle` field to `StartConversationRequest`

**Goal:** Expose the new option in the API.  
**Method:** In `models.py`, add to `StartConversationRequest`:

```python
autotitle: bool = Field(
    default=True,
    description=(
        "If true, automatically generate a title for the conversation from "
        "the first user message using the conversation's LLM."
    ),
)
```

Because `StoredConversation(StartConversationRequest)` inherits all fields, `autotitle` is automatically persisted in `meta.json` and loaded on server restart — no changes needed to `StoredConversation`.  
**Reference:** `models.py` → `StartConversationRequest`

## Step 3 — Add `AutoTitleSubscriber` class

**Goal:** Implement the auto-title logic as a reusable subscriber.  
**Method:** In `conversation_service.py`, add a new dataclass after the existing `_EventSubscriber`:

```python
@dataclass
class AutoTitleSubscriber(Subscriber):
    service: EventService

    async def __call__(self, event: Event) -> None:
        # Only act on incoming user messages
        if not isinstance(event, MessageEvent) or event.source != "user":
            return
        # Guard: skip if a title was already set (e.g. by a concurrent task)
        if self.service.stored.title is not None:
            return

        async def _generate_and_save() -> None:
            try:
                title = await self.service.generate_title()
                if title and self.service.stored.title is None:
                    self.service.stored.title = title
                    self.service.stored.updated_at = utc_now()
                    await self.service.save_meta()
            except Exception:
                logger.warning(
                    f"Auto-title generation failed for "
                    f"conversation {self.service.stored.id}",
                    exc_info=True,
                )

        asyncio.create_task(_generate_and_save())
```

Also add `MessageEvent` to the imports from `openhands.sdk.event`.  
**Reference:** `conversation_service.py` — after `_EventSubscriber` class

## Step 4 — Register `AutoTitleSubscriber` in `_start_event_service`

**Goal:** Wire up the subscriber for newly created conversations and conversations reloaded from disk (that never received a title).  
**Method:** In `_start_event_service`, after the existing `_EventSubscriber` registration, add:

```python
if stored.autotitle and stored.title is None:
    await event_service.subscribe_to_events(
        AutoTitleSubscriber(service=event_service)
    )
```

The `stored.title is None` guard prevents attaching a subscriber to conversations that were reloaded from disk and already have a title.  
**Reference:** `conversation_service.py` → `_start_event_service`

## Step 5 — Add tests

**Goal:** Verify the feature works end-to-end.  
**Method:** Add a new test class `TestAutoTitle` in `test_conversation_service.py` with tests for:

1. **`test_autotitle_sets_title_on_first_user_message`** — Create an `AutoTitleSubscriber` with a mock `EventService` whose `generate_title` returns `"Test Title"`. Fire a fake user `MessageEvent`. Assert `stored.title == "Test Title"` and `save_meta` was called.

2. **`test_autotitle_skips_non_user_events`** — Fire a `ConversationStateUpdateEvent` and an assistant `MessageEvent`. Assert `generate_title` was never called.

3. **`test_autotitle_skips_when_title_already_set`** — Set `stored.title = "Existing"` before firing a user `MessageEvent`. Assert `generate_title` was never called.

4. **`test_autotitle_handles_generate_title_failure`** — Make `generate_title` raise an exception. Assert the subscriber does not propagate the error (no exception raised to the caller).

5. **`test_autotitle_false_does_not_register_subscriber`** — In `test_conversation_router.py`, verify that when `autotitle=False` is passed in a `StartConversationRequest`, the router forwards the field correctly (i.e. the parsed model has `autotitle=False`).

**Reference:** `tests/agent_server/test_conversation_service.py`, `tests/agent_server/test_conversation_router.py`

# 5. TESTING AND VALIDATION

**Unit tests** (run with `pytest tests/agent_server/test_conversation_service.py tests/agent_server/test_conversation_router.py`):
- All new tests in `TestAutoTitle` pass.
- Existing tests are unaffected (the new field defaults to `True` so existing request payloads without `autotitle` continue to work).

**Manual smoke test:**
1. Start the agent server.
2. `POST /conversations` without an `autotitle` field — confirm the default is `True` in the response.
3. `POST /conversations` with `autotitle: false` — confirm `autotitle` is `false` in the stored conversation.
4. Start a conversation with `autotitle: true` and an `initial_message`. Poll `GET /conversations/{id}` — within a few seconds the `title` field should be populated.
5. Start a conversation with `autotitle: false` — confirm `title` remains `null` even after the first message.

**Success criteria:**
- `GET /conversations/{id}` returns a non-null `title` shortly after the first user message for `autotitle: true` conversations.
- `title` is never regenerated if it is already set (no duplicate LLM calls on subsequent messages or server restarts).
- Failures in title generation are logged as warnings and do not affect conversation execution.
