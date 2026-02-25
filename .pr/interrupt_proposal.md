# Proposal: `conversation.interrupt()` - Immediate Agent Interruption with LLM Cancellation

## Problem Statement

Currently, `conversation.pause()` sets the execution status to `PAUSED`, but it **waits for the current LLM call to complete** before taking effect:

```python
def pause(self) -> None:
    """Pause agent execution.
    ...
    Note: If called during an LLM completion, the pause will not take
    effect until the current LLM call completes.
    """
```

This means if the LLM is generating a long response (especially during streaming), the user has to wait for the entire response to finish before the agent actually pauses. This creates a poor user experience when the user wants to immediately stop the agent.

## Proposed Solution

Add a new `conversation.interrupt()` method that:

1. **Immediately cancels any in-flight LLM responses** (both streaming AND non-streaming)
2. **Pauses the agent execution** (like `pause()`)
3. **Emits an interrupt event** for visibility

### Key Components

#### 1. LLM Cancellation Mechanism

Add a thread-safe cancellation flag to the `LLM` class:

```python
class LLM(BaseModel, RetryMixin, NonNativeToolCallingMixin):
    # Private attribute for cancellation
    _cancel_event: threading.Event = PrivateAttr(default_factory=threading.Event)
    
    def cancel(self) -> None:
        """Request cancellation of any in-flight LLM calls.
        
        This method can be called from any thread. For streaming calls,
        cancellation happens at the next chunk boundary. For non-streaming
        calls, cancellation happens after the current HTTP request completes
        (the response is discarded).
        
        Cancellation also immediately aborts any pending retries.
        """
        self._cancel_event.set()
    
    def clear_cancel(self) -> None:
        """Clear the cancellation flag.
        
        Called internally before starting a new LLM call to reset
        the cancellation state.
        """
        self._cancel_event.clear()
    
    def is_cancelled(self) -> bool:
        """Check if cancellation has been requested."""
        return self._cancel_event.is_set()
```

#### 2. New Exception for Cancelled LLM Calls

```python
# In openhands/sdk/llm/exceptions/
class LLMCancelledError(Exception):
    """Raised when an LLM call is cancelled by user request."""
    pass
```

#### 3. Cancellation for Streaming Calls

For streaming, check the cancellation flag between each chunk:

In `LLM._transport_call()` for Chat Completions API:

```python
if enable_streaming and on_token is not None:
    assert isinstance(ret, CustomStreamWrapper)
    chunks = []
    for chunk in ret:
        # Check for cancellation between chunks
        if self.is_cancelled():
            self.clear_cancel()
            raise LLMCancelledError("LLM streaming cancelled by user interrupt")
        on_token(chunk)
        chunks.append(chunk)
    ret = litellm.stream_chunk_builder(chunks, messages=messages)
```

Similarly in `LLM.responses()` for Responses API streaming:

```python
for event in ret:
    # Check for cancellation
    if self.is_cancelled():
        self.clear_cancel()
        raise LLMCancelledError("LLM streaming cancelled by user interrupt")
    if stream_callback is None:
        continue
    # ... rest of streaming logic
```

#### 4. Cancellation for Non-Streaming Calls

For non-streaming calls, the synchronous `litellm_completion()` blocks until the HTTP response is complete. To return control immediately on interrupt, we run non-streaming calls in a background thread and poll for cancellation:

```python
from concurrent.futures import ThreadPoolExecutor, Future
from typing import Any

class LLM:
    # Shared thread pool for non-streaming calls (created lazily)
    _executor: ThreadPoolExecutor | None = PrivateAttr(default=None)
    
    def _get_executor(self) -> ThreadPoolExecutor:
        """Lazily create thread pool for interruptible non-streaming calls."""
        if self._executor is None:
            self._executor = ThreadPoolExecutor(
                max_workers=4,
                thread_name_prefix="llm-nonstream-"
            )
        return self._executor
```

**Wrap non-streaming calls** to run in background thread with cancellation polling:

```python
def _transport_call(self, *, messages, enable_streaming, on_token, **kwargs):
    with self._litellm_modify_params_ctx(self.modify_params):
        # ... existing setup ...
        
        if enable_streaming:
            # Streaming path - existing logic with chunk-by-chunk cancellation
            ret = litellm_completion(messages=messages, **kwargs)
            chunks = []
            for chunk in ret:
                if self.is_cancelled():
                    self.clear_cancel()
                    raise LLMCancelledError("LLM streaming cancelled")
                on_token(chunk)
                chunks.append(chunk)
            return litellm.stream_chunk_builder(chunks, messages=messages)
        else:
            # Non-streaming path - run in thread for immediate interruptibility
            return self._interruptible_completion(messages=messages, **kwargs)

def _interruptible_completion(self, **call_kwargs) -> ModelResponse:
    """Run a non-streaming LLM call with support for immediate interruption.
    
    The actual HTTP call runs in a background thread. The main thread polls
    for cancellation every 100ms. If cancelled, control returns immediately
    while the HTTP request completes in the background (result discarded).
    """
    executor = self._get_executor()
    
    # Submit the blocking call to background thread
    future: Future[ModelResponse] = executor.submit(
        litellm_completion,
        model=self.model,
        api_key=self._get_litellm_api_key_value(),
        api_base=self.base_url,
        api_version=self.api_version,
        timeout=self.timeout,
        drop_params=self.drop_params,
        seed=self.seed,
        **call_kwargs,
    )
    
    # Poll for completion or cancellation
    poll_interval = 0.1  # 100ms
    while not future.done():
        if self.is_cancelled():
            self.clear_cancel()
            # Don't wait for the future - return immediately
            # The HTTP request continues in background but result is discarded
            raise LLMCancelledError(
                "LLM call cancelled (request abandoned, returning immediately)"
            )
        # Wait a short time before checking again
        try:
            return future.result(timeout=poll_interval)
        except TimeoutError:
            continue
    
    # Future completed - return result or propagate exception
    return future.result()
```

**Modify the retry decorator** to check cancellation between retries:

```python
# In retry_mixin.py or llm.py retry decorator
def retry_decorator(self, ...):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            for attempt in range(num_retries + 1):
                # Check cancellation before each attempt
                if self.is_cancelled():
                    self.clear_cancel()
                    raise LLMCancelledError("LLM call cancelled before attempt")
                try:
                    return func(*args, **kwargs)
                except LLMCancelledError:
                    # Don't retry cancellation - propagate immediately
                    raise
                except retry_exceptions as e:
                    # Check cancellation before retry
                    if self.is_cancelled():
                        self.clear_cancel()
                        raise LLMCancelledError("LLM call cancelled during retry") from e
                    if attempt == num_retries:
                        raise
                    # ... existing retry logic (sleep with cancellation check)
                    self._interruptible_sleep(wait_time)
            return wrapper
    return decorator

def _interruptible_sleep(self, duration: float) -> None:
    """Sleep that can be interrupted by cancellation."""
    interval = 0.1
    elapsed = 0.0
    while elapsed < duration:
        if self.is_cancelled():
            self.clear_cancel()
            raise LLMCancelledError("LLM call cancelled during retry wait")
        time.sleep(min(interval, duration - elapsed))
        elapsed += interval
```

#### 5. Clear Cancel Flag Before Each Call

In both `completion()` and `responses()` methods:

```python
def completion(self, ...):
    # Clear any previous cancellation before starting new call
    self.clear_cancel()
    # ... rest of method
```

#### 6. New `InterruptEvent`

```python
# In openhands/sdk/event/
class InterruptEvent(Event):
    """Event emitted when the conversation is interrupted."""
    type: Literal["interrupt"] = "interrupt"
    reason: str = "User requested interrupt"
```

#### 7. New `interrupt()` Method in Conversation

In `BaseConversation`:

```python
@abstractmethod
def interrupt(self) -> None:
    """Interrupt the agent immediately, cancelling any in-flight LLM calls.
    
    Unlike pause(), which waits for the current step to complete,
    interrupt() attempts to cancel ongoing LLM calls immediately:
    
    - Streaming calls: Cancelled at the next chunk boundary (milliseconds)
    - Non-streaming calls: Current request completes but response is 
      discarded; any pending retries are aborted immediately
    
    This method is thread-safe and can be called from any thread.
    """
    ...
```

In `LocalConversation`:

```python
def interrupt(self) -> None:
    """Interrupt the agent immediately, cancelling any in-flight LLM calls."""
    # Cancel the main agent LLM
    self.agent.llm.cancel()
    
    # Cancel any LLMs in the registry (fallbacks, etc.)
    for llm in self.llm_registry.all():
        llm.cancel()
    
    # Set paused status
    with self._state:
        if self._state.execution_status in [
            ConversationExecutionStatus.IDLE,
            ConversationExecutionStatus.RUNNING,
        ]:
            self._state.execution_status = ConversationExecutionStatus.PAUSED
            interrupt_event = InterruptEvent()
            self._on_event(interrupt_event)
            logger.info("Agent execution interrupted")
```

#### 8. Handle `LLMCancelledError` in Run Loop

In `LocalConversation.run()`:

```python
try:
    while True:
        # ... existing loop logic ...
        self.agent.step(self, on_event=self._on_event, on_token=self._on_token)
        
except LLMCancelledError:
    # Gracefully handle interruption - don't treat as error
    logger.info("Agent step cancelled by interrupt")
    # Status is already set to PAUSED by interrupt()
    
except Exception as e:
    self._state.execution_status = ConversationExecutionStatus.ERROR
    # ... existing error handling
```

### Comparison: `pause()` vs `interrupt()`

| Aspect | `pause()` | `interrupt()` |
|--------|-----------|---------------|
| When it takes effect | After current step completes | Immediately* |
| Streaming calls | Completes normally | Cancelled at next chunk |
| Non-streaming calls | Completes normally | Response discarded after completion |
| Retries | All retries execute | Remaining retries aborted |
| Response data | Full response received | Partial/no response |
| Use case | Graceful pause | Emergency stop |
| Thread safety | Yes | Yes |

*Both streaming and non-streaming return control immediately (within ~100ms polling interval).

### Cancellation Timing by Call Type

| Call Type | Cancellation Point | Latency | HTTP Request |
|-----------|-------------------|---------|--------------|
| Streaming | Between chunks | ~10-100ms | Abandoned mid-stream |
| Non-streaming (in progress) | Next poll interval | ~100ms max | Continues in background* |
| Non-streaming (in retry sleep) | Next poll interval | ~100ms max | N/A |
| Non-streaming (not started) | Before call | Immediate | Never starts |

*The background HTTP request completes but result is discarded. Tokens are consumed.

### Edge Cases

1. **Non-streaming calls in progress**: Control returns immediately (~100ms). The HTTP request continues in a background thread but the result is discarded. Tokens/cost are still incurred, but the user doesn't wait.

2. **Retries**: Cancellation aborts retries immediately. Retry sleep uses interruptible polling.

3. **Multiple LLMs**: Interrupt cancels all LLMs in the registry.

4. **Already paused**: If already paused, interrupt is a no-op.

5. **Concurrent interrupts**: Multiple interrupt calls are idempotent.

6. **Fallback chains**: If using fallback LLMs, cancellation stops the entire chain.

7. **Thread pool cleanup**: The executor uses a small fixed pool (4 workers). Abandoned requests complete in background threads. On process exit, daemon threads are cleaned up automatically.

8. **Exception in background thread**: If the LLM call raises an exception after cancellation, it's silently discarded (we've already returned `LLMCancelledError`).

### LiteLLM's `/responses` Cancel API

Note: LiteLLM provides `litellm.cancel_responses(response_id=...)` for the Responses API. This is for **server-side** cancellation of stored responses (when `store=True`). This is different from what we're implementing here:

- **LiteLLM cancel_responses**: Cancels a stored response on the server (requires response_id, only works with certain providers)
- **Our interrupt()**: Cancels the client-side streaming loop (works with all providers, doesn't require response_id)

We could potentially integrate with `cancel_responses` in the future for providers that support it, but the client-side cancellation is the primary mechanism that works universally.

### Approach Comparison: Background Threads vs Async LLM

We have two main architectural options for implementing interruptible LLM calls:

---

## Option 1: Background Thread with Polling (Current Proposal)

Run blocking `litellm_completion()` in a `ThreadPoolExecutor`, poll for cancellation.

### Pros
| Benefit | Details |
|---------|---------|
| **No API changes** | Works with existing sync `LLM.completion()` API |
| **Drop-in replacement** | Conversation, Agent code unchanged |
| **Simple mental model** | Users don't need to understand async/await |
| **Universal provider support** | Works regardless of provider async support |
| **Immediate control return** | ~100ms max latency via polling |

### Cons
| Drawback | Details |
|----------|---------|
| **HTTP continues in background** | Tokens consumed even after cancel |
| **Thread overhead** | Small pool (4 workers), but still threads |
| **100ms polling latency** | Not instant, though usually acceptable |
| **Potential memory pressure** | Abandoned futures hold references until HTTP completes |
| **Pool exhaustion risk** | If many calls cancelled rapidly, pool may fill |

---

## Option 2: Async LLM Class

Create a new `AsyncLLM` class (or mode) using `litellm.acompletion()` with `asyncio.Task.cancel()`.

### Pros
| Benefit | Details |
|---------|---------|
| **True task cancellation** | `asyncio.CancelledError` stops the coroutine |
| **No background threads** | Pure async, no thread pool overhead |
| **Instant cancellation** | No polling delay |
| **Better resource cleanup** | Cancelled tasks don't continue consuming resources |
| **Modern Python pattern** | Aligns with async ecosystem |
| **Potential token savings** | If cancelled before response starts, may save tokens* |

*Depends on provider behavior - request may already be processing server-side.

### Cons
| Drawback | Details |
|----------|---------|
| **Requires async context** | Users must use `await`, run in event loop |
| **Major refactor needed** | Agent.step(), Conversation.run() must become async |
| **Two code paths** | Maintain both sync and async versions, or break existing users |
| **Learning curve** | Users must understand async/await patterns |
| **Tool integration complexity** | Existing sync tool executors need adaptation |
| **Testing complexity** | Async tests require pytest-asyncio, different patterns |
| **HTTP cancellation not guaranteed** | Even with async, server may have started processing |

---

## Option 3: Async Internally, Sync API (Recommended)

Use `litellm.acompletion()` internally but expose synchronous `LLM.completion()` API. Run async calls in a background thread with an event loop. This gives us async cancellation benefits without breaking changes.

```python
import asyncio
import threading
from concurrent.futures import Future
from typing import Any

class LLM:
    # Background event loop for async calls (created lazily)
    _loop: asyncio.AbstractEventLoop | None = PrivateAttr(default=None)
    _loop_thread: threading.Thread | None = PrivateAttr(default=None)
    
    # Track current task for cancellation
    _current_task: asyncio.Task | None = PrivateAttr(default=None)
    _task_lock: threading.Lock = PrivateAttr(default_factory=threading.Lock)
    
    def _ensure_loop(self) -> asyncio.AbstractEventLoop:
        """Lazily create background event loop thread."""
        if self._loop is None:
            self._loop = asyncio.new_event_loop()
            self._loop_thread = threading.Thread(
                target=self._loop.run_forever,
                daemon=True,
                name="llm-async-loop"
            )
            self._loop_thread.start()
        return self._loop
    
    def completion(self, messages, ...) -> LLMResponse:  # Sync API - unchanged!
        """Synchronous completion - uses async internally for cancellation."""
        loop = self._ensure_loop()
        
        # Submit async call to background loop
        future: Future[LLMResponse] = asyncio.run_coroutine_threadsafe(
            self._async_completion_with_tracking(messages, ...),
            loop
        )
        
        try:
            return future.result()  # Blocks caller until complete
        except asyncio.CancelledError:
            raise LLMCancelledError("LLM call cancelled")
    
    async def _async_completion_with_tracking(self, messages, ...) -> LLMResponse:
        """Internal async implementation with task tracking."""
        # Store current task for cancellation
        with self._task_lock:
            self._current_task = asyncio.current_task()
        
        try:
            # Use litellm's async API
            response = await litellm.acompletion(
                model=self.model,
                messages=messages,
                ...
            )
            return self._process_response(response)
        finally:
            with self._task_lock:
                self._current_task = None
    
    def cancel(self) -> None:
        """Cancel any in-flight LLM call immediately."""
        with self._task_lock:
            if self._current_task is not None and self._loop is not None:
                # Schedule cancellation on the event loop thread
                self._loop.call_soon_threadsafe(self._current_task.cancel)
```

### How It Works

```
┌─────────────────┐     ┌──────────────────────────────────────┐
│  Main Thread    │     │  Background Thread (Event Loop)       │
├─────────────────┤     ├──────────────────────────────────────┤
│                 │     │                                      │
│ llm.completion()│────▶│ await litellm.acompletion()          │
│   (blocks)      │     │   (running as async task)            │
│                 │     │                                      │
│ ─ ─ ─ ─ ─ ─ ─ ─│     │ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ │
│                 │     │                                      │
│ llm.cancel()    │────▶│ task.cancel()                        │
│   (instant)     │     │   raises CancelledError              │
│                 │     │                                      │
│ LLMCancelledErr │◀────│ CancelledError propagates            │
│   (immediate)   │     │                                      │
└─────────────────┘     └──────────────────────────────────────┘
```

### Pros
| Benefit | Details |
|---------|---------|
| **Sync API preserved** | `llm.completion()` unchanged, no breaking changes |
| **True async cancellation** | `Task.cancel()` stops the coroutine immediately |
| **Instant cancellation** | No polling delay - cancel is immediate |
| **HTTP connection closed** | httpx closes connection on CancelledError |
| **Single code path** | One async implementation serves sync callers |
| **Lazy initialization** | Event loop only created when first needed |

### Cons
| Drawback | Details |
|----------|---------|
| **One background thread** | Single daemon thread for event loop (minimal overhead) |
| **Debugging complexity** | Async errors originate in background thread |
| **Task tracking overhead** | Need lock to track current task |
| **Lifecycle management** | Must handle loop cleanup on LLM disposal |

### Cancellation Behavior

When `cancel()` is called:

1. **Streaming calls**: The `async for chunk in response` loop receives `CancelledError`, stops immediately
2. **Non-streaming calls**: The `await acompletion()` receives `CancelledError`:
   - If waiting for connection: Connection attempt cancelled
   - If waiting for response: httpx closes the TCP connection
   - Server may or may not have started processing (provider-dependent)

---

## Comparison Matrix

| Criteria | Option 1: Thread Pool | Option 2: Pure Async | Option 3: Async Internal |
|----------|----------------------|---------------------|-------------------------|
| API compatibility | ✅ Full | ❌ Breaking | ✅ Full |
| Cancellation latency | ~100ms (polling) | Instant | Instant |
| HTTP connection closed | ❌ No | ✅ Yes | ✅ Yes |
| Token waste on cancel | ⚠️ Always | ⚠️ Maybe* | ⚠️ Maybe* |
| Implementation complexity | Low | High | Medium |
| Maintenance burden | Low | High (2 paths) | Low (1 path) |
| Resource cleanup | ⚠️ Delayed | ✅ Immediate | ✅ Immediate |
| User learning curve | None | High (async/await) | None |
| Thread overhead | Pool of 4 | None | 1 daemon thread |

*Token waste depends on timing - if cancelled before server starts processing, tokens may be saved. Once server is generating, tokens are consumed regardless.

---

## Recommendation: **Option 3 (Async Internal, Sync API)**

**Rationale:**

1. **No breaking changes** - `llm.completion()` API unchanged
2. **Instant cancellation** - No 100ms polling delay
3. **True HTTP cancellation** - httpx closes connection on `CancelledError`
4. **Minimal overhead** - Single daemon thread vs thread pool
5. **Better resource cleanup** - Cancelled tasks don't continue in background
6. **Simpler than it looks** - `asyncio.run_coroutine_threadsafe()` handles the complexity

### Why not Option 1 (Thread Pool)?

While simpler, the thread pool approach has a fundamental limitation: the HTTP request **continues in the background** after cancellation. This means:
- Tokens are always consumed
- Memory held until request completes
- Potential pool exhaustion if many cancellations

### Why not Option 2 (Pure Async)?

Breaking the sync API would require changes throughout:
- `Agent.step()` → `async Agent.step()`
- `Conversation.run()` → `async Conversation.run()`
- All tool executors → async
- User code → async

This is too disruptive. Option 3 gives us async benefits internally while keeping the sync API.

---

## Summary

**Option 3** is the sweet spot:

```
┌────────────────────────────────────────────────────────────┐
│                     User Code (unchanged)                   │
│                                                            │
│   conversation.send_message("Write an essay...")           │
│   thread = Thread(target=conversation.run)                 │
│   thread.start()                                           │
│                                                            │
│   time.sleep(1)                                            │
│   conversation.interrupt()  # Returns instantly!           │
│                                                            │
└────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌────────────────────────────────────────────────────────────┐
│                   LLM Class (internal change)              │
│                                                            │
│   def completion(messages):        # Sync API preserved    │
│       future = run_coroutine_threadsafe(                   │
│           acompletion(messages),   # Async internally      │
│           background_loop                                  │
│       )                                                    │
│       return future.result()                               │
│                                                            │
│   def cancel():                                            │
│       current_task.cancel()        # Instant!              │
│                                                            │
└────────────────────────────────────────────────────────────┘
```

### Future Enhancements

1. **Async support**: Add `async_interrupt()` for async conversation implementations with true task cancellation
2. **Graceful timeout**: Add optional timeout parameter to wait for graceful completion
3. **Partial response preservation**: Option to keep partial streaming response
4. **Server-side cancellation**: Integrate with `litellm.cancel_responses()` for providers that support it

## Implementation Steps

1. Add `LLMCancelledError` exception
2. Add cancellation methods to `LLM` class
3. Modify streaming loops to check cancellation
4. Add `InterruptEvent`
5. Add `interrupt()` to `BaseConversation` and `LocalConversation`
6. Handle `LLMCancelledError` in run loop
7. Add tests
8. Update documentation

## Example Usage

```python
import threading
import time

from openhands.sdk import Agent, Conversation, LLM

llm = LLM(model="claude-sonnet-4-20250514", api_key=...)
agent = Agent(llm=llm, tools=[...])
conversation = Conversation(agent=agent, workspace="./workspace")

conversation.send_message("Write a very long essay about philosophy...")

# Start in background thread
thread = threading.Thread(target=conversation.run)
thread.start()

# Wait a bit, then interrupt
time.sleep(1)
print("Interrupting...")
conversation.interrupt()  # Immediately cancels the LLM call!

thread.join()
print(f"Status: {conversation.state.execution_status}")  # PAUSED
```
