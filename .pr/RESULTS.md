# Issue #3142 — benchmark results

Reproduction and verification for *perf: O(N) full scan for conversation search
and count*.

Run it yourself:

```bash
uv run python .pr/bench_conversation_search.py --sizes 100,500,1000,2000 --repeat 5
```

The script builds one real conversation through `ConversationService`, clones its
on-disk layout N times, then times the read APIs behind `GET /conversations` and
`GET /conversations/count` against a freshly started service.

Environment: macOS 15 (arm64, APFS SSD), Python 3.13.11, median of 5 calls after
one warmup. All timings in milliseconds.

## Before (`main` @ c248ec0ae)

```
     N |    count() |  count(status) |   page(20) |  page(20,status)
--------------------------------------------------------------------
   100 |       0.00 |          11.49 |       3.50 |            14.96
   500 |       0.00 |          58.75 |       3.96 |            62.64
  1000 |       0.00 |         115.60 |       4.46 |           118.14
  2000 |       0.00 |         234.16 |       5.93 |           243.58

Scaling from N=100 to N=2000:
  count()            x    0.8
  count(status)      x   20.4     <-- dead-linear in total conversations
  page(20)           x    1.7
  page(20,status)    x   16.3     <-- dead-linear in total conversations
```

## After (this branch)

```
     N |    count() |  count(status) |   page(20) |  page(20,status)
--------------------------------------------------------------------
   100 |       0.00 |           0.19 |       3.61 |             3.73
   500 |       0.00 |           0.75 |       4.02 |             4.71
  1000 |       0.00 |           1.49 |       4.50 |             6.04
  2000 |       0.00 |           2.99 |       5.76 |             9.93

Scaling from N=100 to N=2000:
  count()            x    0.9
  count(status)      x   15.8
  page(20)           x    1.6
  page(20,status)    x    2.7
```

## Speedup

| Operation | N=100 | N=500 | N=1000 | N=2000 |
| --- | ---: | ---: | ---: | ---: |
| `count(status)` | **60x** | **78x** | **78x** | **78x** |
| `page(20, status)` | **4.0x** | **13x** | **20x** | **25x** |

At N=2000 a status-filtered list call drops from 244 ms to 10 ms, and a
status-filtered count from 234 ms to 3 ms.

## Reading the numbers

`count()` and `page(20)` without a filter were already flat before this change
(PR #4100 made the catalog metadata-only), which is why they are unchanged. The
regression was entirely in the **status-filtered** paths, which called
`get_state()` — a full `ConversationState` validation — once per conversation.

`page(20, status)` does not go fully flat, and shouldn't: it still loads full
state for the 20 conversations it actually returns. That fixed ~5 ms floor is
visible as the `page(20)` column. The residual slope on `count(status)` is the
`stat()` pass that keeps the index honest against out-of-process writers — about
1.5 µs per conversation.

## Test evidence

`tests/agent_server/test_conversation_service.py::TestConversationSearchScaling`
pins the behaviour. On `main` the two scaling tests fail:

```
FAILED test_filtered_search_loads_state_only_for_the_page
  AssertionError: expected 5 full state loads (the page), got 45
FAILED test_filtered_count_loads_no_state
  AssertionError            # _load_persisted_state_sync was called at all
```

On this branch all four pass, including the two that guard correctness
(out-of-process status changes are still observed; live conversations still
answer from memory).
