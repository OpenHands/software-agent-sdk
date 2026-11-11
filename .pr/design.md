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

- Backend: 40 tests (index unit, endpoint via `TestClient`, delete-prunes-index, gating)
- Frontend: 10 tests (hook: cloud-gating/debounce/whitespace/error/reset/mapping; results component: error/loading/empty)
- Full suites run at both forks: all failures proven pre-existing by stash-compare at base commit

## PR plan (per repo)

| Repo | Anchor issue | Notes |
|---|---|---|
| software-agent-sdk | #6627 | SDK PR gate requires same-repo ready-for-dev issue; #6627 is the open local-search-territory issue (`enhancement` + `ready-for-dev`) |
| OpenHands/OpenHands | #19378 | Canvas gate requires same-repo issue; #19378 (`enhancement` + `ready-for-dev`) covers within-conversation find — our PR closes the "search unavailable" gap for local users |

Both PRs open as **drafts** with `HUMAN:` first line + `AGENT:` AI disclosure, per the repos' PR-description validator.
