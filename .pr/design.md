# Conversation content search (local backends)

Full-text search over conversation titles and messages for **self-hosted OpenHands only**. Never mounted in hosted deployments: requires `OH_CONTENT_SEARCH_ENABLED=true` **and** local deployment kind. Default is off.

## What exists today

- `GET /api/conversations/search` matches **titles only** (SQL `LIKE`). No message-body search, no fuzzy matching.
- Browser Find can't see unloaded events; the Canvas list loads the latest 50 events per conversation.

## Design

```
query ─→ SearchService.search()          (FastAPI handler)
           └→ SearchIndex                 (SQLite FTS5, porter stemming)
                ├→ backfill: walk conversation storage dirs on first call
                └→ sync: per-conversation mtime table (re-index changed files only)
```

### Why an external index, not a query against storage

Conversations are stored as JSONL event files. A `grep`-style scan on every query reads every file; FTS5 turns this into a ~millisecond token lookup after one backfill, and re-syncs only conversations whose event file mtime changed since the last index.

### Gating

| Condition | Result |
|---|---|
| `OH_CONTENT_SEARCH_ENABLED` unset/false | Router never mounted — no route, no OpenAPI schema entry, no 404 fingerprint |
| Flag on + hosted deployment kind | Router never mounted |
| Flag on + local | Router mounted, `app.state.content_search_service` constructed |
| Cloud backend in Canvas | Hook never queries (`isSearching: false`), search box hidden entirely |

Verified live: `create_app()` with flag off → `search-content` routes: **NONE**; flag on → `['/api/conversations/search-content']`.

### Query semantics

- Portered fuzzy match on tokens (FTS5 porter stemmer), with a raw prefix pass first so exact-word hits aren't missed when stemming eats a short token
- `limit` (default 50, hard cap), `capped: true` in response when truncated
- Returns per-hit: conversation id, title, matched snippet with markers, event timestamp
- Conversation ids in hits and deletion are the dashed UUID form used by the rest of the API; the index internally keys rows by the hex directory names and the service layer normalizes at the boundary

### Concurrency & durability

- Search route is `async def` and offloads SQLite work with `asyncio.to_thread` — no database calls on the event loop
- Both SQLite connections (index + stem probe) open with `check_same_thread=False`; every access is serialized by the service lock, so thread-hopping via `to_thread` is safe
- WAL journal with `synchronous=NORMAL` for fast writes without losing durability on clean shutdown
- Corrupt index file (crash mid-write) → the service rebuilds the index from scratch instead of erroring every search

### Fuzzy-match safeguards

- Stem-truncated tokens exact-quote their fuzzy candidates — prefix-quoting dragged in unrelated words (`doings` → candidate `doing` → stem `do` matched `door`)
- Prefix expansion per token is capped; above the vocabulary size limit the engine degrades to prefix-only matching instead of scanning unbounded vocab

### Zero impact when disabled

1. No route, no schema entry → `check_agent_server_openapi_quality.py` sees the same default schema as upstream `main`
2. No SQLite file opened unless the first search call happens
3. Index lives under `config_dir/search-index/`; deletion of a conversation prunes its rows on next sync

## Frontend (Canvas)

- Search box above conversation list (local backends only)
- Debounced 300 ms, whitespace-only input never queries, `retry: false` + `meta.disableToast` on errors → no toast spam against stock backends
- Results show title + snippet with `<mark>`-style highlighting; click jumps to the conversation and highlights the target message; "Return to live" clears back to list
- Opt-in MSW mock handler (`MOCK_CONTENT_SEARCH_ENABLED`) so `dev:mock` mirrors a backend without the feature

## Testing

- Backend: 60 tests (index unit, service unit, endpoint via `TestClient`, live-server cross tests, delete-prunes-index, gating), 100% line coverage on `search_index.py` and `search_service.py`
- Frontend: 10 tests (hook: cloud-gating/debounce/whitespace/error/reset/mapping; results component: error/loading/empty)
- Full suites run at both forks: all failures proven pre-existing by stash-compare at base commit

## PR plan (per repo)

| Repo | Anchor issue | Notes |
|---|---|---|
| software-agent-sdk | #6767 | SDK PR gate requires same-repo ready-for-dev issue; #6767 is the content-search issue (do **not** link #6627 — that is the tag-filter issue, a different feature) |
| OpenHands/OpenHands | #19378 | Canvas gate requires same-repo issue; #19378 (`enhancement` + `ready-for-dev`) covers within-conversation find — our PR closes the "search unavailable" gap for local users |

Both PRs open as **drafts** with `HUMAN:` first line + `AGENT:` AI disclosure, per the repos' PR-description validator.
