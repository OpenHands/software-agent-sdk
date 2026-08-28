# Skill: React Chat Frontend with OpenHands TypeScript Client

Build a React chat frontend that connects to an OpenHands agent server, sends messages, and displays a live conversation including all event types.

## Basic structure
You will probably want the following features:
* A settings modal/page where the user can set:
  * host and password for agent-server
  * LLM model and key
* a chat input
* the ability to display both messages and events from the agent
  * events typically come in action/observation pairs. it's usually best to only show one UI element per pair, depending on status
  * events can contain a lot of information. good to make the display collapsed by default, with a small summary, but expandable to see full details
  * condensation events should get displayed like other events
* a list of conversations the user can navigate between
  *  there are APIs for automatically setting a conversation title after the first user message is sent, use them

## Packages

```bash
npm install @openhands/typescript-client
# or from local repo:
npm install /path/to/typescript-client
```

Peer deps: `react`, `react-dom` (18+). No extra WebSocket library needed in browsers.

---

## 1. Core Concepts

- **`Conversation`** — creates/resumes a conversation on the agent server
- **`Agent`** — LLM config (model + api_key)
- **`Workspace`** — points at the agent server host
- **`WebSocketCallbackClient`** — streams events in real-time via WS
- Events arrive with a `kind` discriminator field; render each kind differently

The agent server WebSocket endpoint is:
```
ws[s]://<host>/sockets/events/<conversationId>[?session_api_key=<key>]
```

---

## 2. Minimal Hook: `useAgentConversation`

```tsx
import { useEffect, useRef, useState, useCallback } from 'react';
import {
  Conversation,
  Agent,
  Workspace,
  ConversationExecutionStatus,
} from '@openhands/typescript-client';
import type { Event } from '@openhands/typescript-client';

interface UseAgentConversationOptions {
  serverUrl: string;       // e.g. "http://localhost:8000"
  model: string;           // e.g. "claude-sonnet-4-6"
  apiKey: string;          // LLM API key
  sessionKey?: string;     // Agent server session key (if secured)
}

export function useAgentConversation({
  serverUrl,
  model,
  apiKey,
  sessionKey,
}: UseAgentConversationOptions) {
  const [events, setEvents] = useState<Event[]>([]);
  const [status, setStatus] = useState<ConversationExecutionStatus | null>(null);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const convRef = useRef<Conversation | null>(null);

  // Append incoming events (WebSocket + initial load)
  const onEvent = useCallback((event: Event) => {
    setEvents(prev => [...prev, event]);
  }, []);

  const start = useCallback(async (initialMessage: string) => {
    const agent = new Agent({ llm: { model, api_key: apiKey } });
    const workspace = new Workspace({
      host: serverUrl,
      workingDir: '/workspace',
      apiKey: sessionKey,
    });

    const conv = new Conversation(agent, workspace, { callback: onEvent });
    convRef.current = conv;

    await conv.start({ initialMessage });
    setConversationId(conv.id);

    // Stream events live
    await conv.startWebSocketClient();

    // Kick the agent
    await conv.run();

    setStatus(await conv.state.getAgentStatus());
  }, [serverUrl, model, apiKey, sessionKey, onEvent]);

  const sendMessage = useCallback(async (text: string) => {
    const conv = convRef.current;
    if (!conv) return;
    await conv.sendMessage(text);
    await conv.run();
  }, []);

  const pause = useCallback(async () => {
    await convRef.current?.pause();
  }, []);

  // Poll status while running
  useEffect(() => {
    if (status !== ConversationExecutionStatus.RUNNING) return;
    const t = setInterval(async () => {
      const s = await convRef.current?.state.getAgentStatus();
      if (s) setStatus(s);
    }, 1500);
    return () => clearInterval(t);
  }, [status]);

  // Cleanup
  useEffect(() => {
    return () => { convRef.current?.stopWebSocketClient(); };
  }, []);

  return { events, status, conversationId, start, sendMessage, pause };
}
```

---

## 3. Event Renderer

Handle every `kind` the server can emit. The `kind` field is the discriminator.

```tsx
import type { Event } from '@openhands/typescript-client';

interface EventBubbleProps { event: Event }

export function EventBubble({ event }: EventBubbleProps) {
  switch (event.kind) {

    // ── Visible chat messages ─────────────────────────────────────────────
    case 'MessageEvent': {
      const msg = (event as any).llm_message;
      const role: string = msg?.role ?? (event as any).source ?? 'unknown';
      const text = extractText(msg?.content);
      const isUser = role === 'user';
      return (
        <div className={`chat-bubble ${isUser ? 'user' : 'agent'}`}>
          <span className="role">{isUser ? 'You' : 'Agent'}</span>
          <p>{text}</p>
        </div>
      );
    }

    // ── Tool call the agent is making ─────────────────────────────────────
    case 'ActionEvent': {
      const e = event as any;
      return (
        <details className="event-action">
          <summary>
            Tool call: <code>{e.tool_name}</code>
          </summary>
          <pre>{JSON.stringify(e.action, null, 2)}</pre>
          {e.thought && <blockquote>{extractThought(e.thought)}</blockquote>}
        </details>
      );
    }

    // ── Result of a tool call ─────────────────────────────────────────────
    case 'ObservationEvent': {
      const e = event as any;
      const content =
        typeof e.observation === 'string'
          ? e.observation
          : JSON.stringify(e.observation, null, 2);
      return (
        <details className="event-observation">
          <summary>Result: <code>{e.tool_name}</code></summary>
          <pre>{content}</pre>
        </details>
      );
    }

    // ── Scaffold error (sent to LLM) ──────────────────────────────────────
    case 'AgentErrorEvent': {
      const e = event as any;
      return (
        <div className="event-error agent-error">
          <strong>Agent error ({e.tool_name}):</strong> {e.error}
        </div>
      );
    }

    // ── Conversation-level failure (NOT sent to LLM) ──────────────────────
    case 'ConversationErrorEvent': {
      const e = event as any;
      return (
        <div className="event-error conversation-error">
          <strong>Error [{e.code}]:</strong> {e.detail}
        </div>
      );
    }

    // ── User/hook rejection ───────────────────────────────────────────────
    case 'UserRejectObservation': {
      const e = event as any;
      return (
        <div className="event-reject">
          Action <code>{e.tool_name}</code> rejected by {e.rejection_source}:
          {' '}{e.rejection_reason}
        </div>
      );
    }

    // ── Execution paused ──────────────────────────────────────────────────
    case 'PauseEvent':
      return <div className="event-status">Agent paused</div>;

    // ── Condensation (history compacted) ─────────────────────────────────
    case 'Condensation': {
      const e = event as any;
      return (
        <div className="event-condensation">
          Context condensed ({e.forgotten_event_ids?.length ?? 0} events removed)
          {e.summary && <p>{e.summary}</p>}
        </div>
      );
    }

    case 'CondensationSummaryEvent': {
      const e = event as any;
      return (
        <div className="event-condensation summary">
          <em>Summary:</em> {e.summary}
        </div>
      );
    }

    // ── State change (use for status badge) ───────────────────────────────
    case 'ConversationStateUpdateEvent': {
      const e = event as any;
      if (e.key === 'execution_status') {
        return <div className="event-status">Status → {String(e.value)}</div>;
      }
      return null; // skip noisy state updates
    }

    // ── System prompt (usually hide in UI) ────────────────────────────────
    case 'SystemPromptEvent':
      return null;

    // ── Token IDs (VLLM internal, usually hide) ───────────────────────────
    case 'TokenEvent':
      return null;

    // ── Unknown / future events ───────────────────────────────────────────
    default:
      return (
        <details className="event-unknown">
          <summary>Event: {event.kind}</summary>
          <pre>{JSON.stringify(event, null, 2)}</pre>
        </details>
      );
  }
}

// Helpers
function extractText(content: unknown): string {
  if (!content) return '';
  if (typeof content === 'string') return content;
  if (Array.isArray(content)) {
    return content
      .filter((c: any) => c.type === 'text')
      .map((c: any) => c.text ?? '')
      .join('\n');
  }
  return JSON.stringify(content);
}

function extractThought(thought: unknown): string {
  if (!thought) return '';
  if (typeof thought === 'string') return thought;
  if (Array.isArray(thought)) {
    return (thought as any[])
      .filter(t => t.type === 'text')
      .map(t => t.text ?? '')
      .join('\n');
  }
  return '';
}
```

---

## 4. Chat Shell Component

```tsx
import { useState, useRef, useEffect } from 'react';
import { useAgentConversation } from './useAgentConversation';
import { EventBubble } from './EventBubble';

export function ChatApp() {
  const [input, setInput] = useState('');
  const [started, setStarted] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);

  const { events, status, start, sendMessage, pause } = useAgentConversation({
    serverUrl: 'http://localhost:8000',
    model: 'claude-sonnet-4-6',
    apiKey: process.env.REACT_APP_API_KEY ?? '',
  });

  // Auto-scroll to bottom
  useEffect(() => {
    scrollRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [events]);

  const handleSend = async () => {
    const text = input.trim();
    if (!text) return;
    setInput('');
    if (!started) {
      setStarted(true);
      await start(text);
    } else {
      await sendMessage(text);
    }
  };

  return (
    <div className="chat-app">
      <header>
        <h1>OpenHands Chat</h1>
        <span className={`status-badge status-${status ?? 'idle'}`}>
          {status ?? 'idle'}
        </span>
        {status === 'running' && (
          <button onClick={pause}>Pause</button>
        )}
      </header>

      <div className="event-list">
        {events.map((evt, i) => (
          <EventBubble key={evt.id ?? i} event={evt} />
        ))}
        <div ref={scrollRef} />
      </div>

      <div className="input-row">
        <textarea
          value={input}
          onChange={e => setInput(e.target.value)}
          onKeyDown={e => {
            if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); handleSend(); }
          }}
          placeholder="Message the agent…"
          rows={2}
        />
        <button onClick={handleSend} disabled={!input.trim() || status === 'running'}>
          Send
        </button>
      </div>
    </div>
  );
}
```

---

## 5. Event Types Reference

All events share `id`, `kind`, `timestamp`, `source` (`'agent' | 'user' | 'environment'`).

| `kind` | Source | When shown | Key fields |
|---|---|---|---|
| `MessageEvent` | agent/user | Always — main chat bubbles | `llm_message.role`, `llm_message.content[]` |
| `ActionEvent` | agent | Collapsible tool-call block | `tool_name`, `action`, `thought`, `tool_call_id` |
| `ObservationEvent` | environment | Collapsible result block | `tool_name`, `observation`, `action_id` |
| `AgentErrorEvent` | agent | Inline error — sent to LLM | `tool_name`, `tool_call_id`, `error` |
| `ConversationErrorEvent` | environment | Fatal error banner | `code`, `detail` |
| `UserRejectObservation` | environment | Rejection notice | `tool_name`, `tool_call_id`, `rejection_reason`, `rejection_source` (`'user'\|'hook'`) |
| `PauseEvent` | user | Status line | — |
| `ConversationStateUpdateEvent` | environment | Status badge update | `key`, `value` |
| `Condensation` | environment | "History compacted" notice | `forgotten_event_ids[]`, `summary?`, `llm_response_id` |
| `CondensationSummaryEvent` | environment | Summary block | `summary` |
| `CondensationRequest` | environment | (hide) | — |
| `SystemPromptEvent` | agent | (hide) | `system_prompt`, `tools[]` |
| `TokenEvent` | agent | (hide — VLLM only) | `prompt_token_ids[]`, `response_token_ids[]` |
| `LLMCompletionLogEvent` | environment | Debug only | `filename`, `log_data`, `model_name` |

---

## 6. Loading Existing Conversations

```tsx
// Resume an existing conversation by ID (e.g. from a list page)
const conv = new Conversation(agent, workspace, {
  conversationId: existingId,
  callback: onEvent,
});
await conv.start(); // connects without sending initial message

// Fetch historical events
const history = await conv.state.events.getEvents();
setEvents(history);

await conv.startWebSocketClient(); // subscribe to future events
```

---

## 7. Type Definitions Location

TypeScript event interfaces live in:
```
typescript-client/src/events/types.ts
```

Exported from the package as named types with `Typed` prefix aliases to avoid collisions:
```ts
import type {
  TypedMessageEvent,
  TypedActionEvent,
  TypedObservationEvent,
  ConversationErrorEvent,
  CondensationEvent,
  UserRejectObservation,
  TypedConversationEvent,
} from '@openhands/typescript-client';
```

Type guards: `isMessageEvent`, `isActionEvent`, `isObservationEvent`, `isAgentErrorEvent`,
`isObservationLike`, `isConversationErrorEvent`, `isCondensationEvent`.

---

## 8. Configuring Agent Tools

Tools are passed on the `agent` object when creating a conversation. **Tool names are lowercase snake_case** — the swagger doc examples show PascalCase names like `BashTool` which do NOT work.

```ts
const agent = new Agent({
  llm: { model, api_key: apiKey },
  tools: [
    { name: 'terminal' },        // bash / shell execution
    { name: 'file_editor' },     // read, write, patch files
    { name: 'browser_tool_set'}, // web browsing (requires Chromium)
    { name: 'task_tracker' },    // optional: task list management
  ],
});
```

**Confirmed registered tool names** (from `openhands-tools`):

| Name | Class | Description |
|---|---|---|
| `terminal` | `TerminalTool` | Bash/shell execution |
| `file_editor` | `FileEditorTool` | File read/write/patch |
| `browser_tool_set` | `BrowserToolSet` | Web browsing (navigate, click, type, screenshot) |
| `task_tracker` | `TaskTrackerTool` | Task list management |
| `glob` | `GlobTool` | File pattern matching |
| `grep` | `GrepTool` | Content search |
| `apply_patch` | `ApplyPatchTool` | Apply unified diffs |

**`browser_tool_set` requires Chromium:**
```bash
uvx playwright install chromium --with-deps
```

If you get `"ToolDefinition 'X' is not registered"` — the name is wrong. Check the actual `.name` class attribute in `openhands-tools/openhands/tools/*/definition.py` or test via the API.

---

## 9. Listing Conversations

Use `ConversationManager` to fetch the conversation list:

```ts
import { ConversationManager } from '@openhands/typescript-client';
import type { ConversationInfo } from '@openhands/typescript-client';

const manager = new ConversationManager({
  host: 'http://localhost:8000',
  apiKey: sessionKey || undefined, // X-Session-API-Key header
});
const conversations: ConversationInfo[] = await manager.getAllConversations();
manager.close();
```

`ConversationInfo` has: `id`, `title`, `execution_status`, `created_at`, `updated_at`, `agent`.

---

## 10. Vite Configuration

When using the local package (`file:../typescript-client`) with Vite, exclude it from pre-bundling to avoid esbuild choking on the conditional `require('ws')` in the WebSocket client:

```ts
// vite.config.ts
export default defineConfig({
  plugins: [react()],
  optimizeDeps: {
    exclude: ['@openhands/typescript-client'],
  },
  server: {
    allowedHosts: true, // allow any hostname (reverse proxy, tunnels, etc.)
  },
});
```

And in `package.json`, bind to all interfaces by default:
```json
"dev": "vite --host"
```

---

## 11. Authentication

The agent server uses the `X-Session-API-Key` HTTP header. Set via `OH_SESSION_API_KEYS_0` env var on the server. Pass it as `apiKey` in `Workspace` and `ConversationManager`:

```ts
new Workspace({ host, workingDir: '/workspace', apiKey: sessionKey });
new ConversationManager({ host, apiKey: sessionKey });
```

For WebSocket connections the key goes as a query param: `?session_api_key=<key>` (the client handles this automatically).

With no `OH_SESSION_API_KEYS_0` set, auth is disabled — omit `apiKey` entirely.

---

## 12. Notes

- The agent server runs on port 8000 by default (`agent-server --host 0.0.0.0 --port 8000`)
- `llm_message.content` is always an array of `{type:'text', text:string}` objects
- `ActionEvent.thought` is also an array of text content objects (iterate to get text)
- `ConversationStateUpdateEvent` with `key='full_state'` carries the entire state snapshot
- `ConversationStateUpdateEvent` with `key='execution_status'` is the most useful for live status
- Track status from WS events (key `execution_status`) rather than polling — avoids extra HTTP calls
- Deduplicate events by `id` when merging history + live WS stream to avoid duplicates
- When resuming an existing conversation, load history with `conv.state.events.getEvents()` BEFORE calling `startWebSocketClient()` for a consistent snapshot
