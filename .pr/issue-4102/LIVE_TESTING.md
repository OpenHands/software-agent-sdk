# Live acceptance evidence for #4102

Tested on Linux 6.8.0-134-generic x86_64 with Python 3.13.13.

- Before: current `main` at `777671766f4196da318b5b1e6179a6dc897cad36`
- After: PR code at `bf0c3d3f70a39c1fe2c15ec2ecd5852260b9ce48`
- Corpus: 120 production-format persisted conversations, 460 events each,
  55,200 event JSON files total
- Corpus digest: `e0da2863d98da62858afa445c368b6a577bd1894e10d6221e709440b0df4463f`

The harness launches the real `python -m openhands.agent_server` process. Runtime
hydration is observed through production `owner_lease.json` files and the SDK's
`Resumed conversation ... from persistent storage` logs. There is no test-only
endpoint or monkeypatch.

## Results

| Check | Current main | PR |
| --- | ---: | ---: |
| Startup time | 27.147 s | 10.836 s |
| Startup RSS | 505,288 KiB | 302,820 KiB |
| Runtime leases after readiness | 120 | 0 |
| Resume log entries after readiness | 120 | 0 |
| Runtime leases after live search/count | 120 | 0 |
| Eight concurrent event-count requests | all returned 460 | all returned 460 |
| Runtime leases after those requests | 120 | 1 |
| Resume entries for the requested conversation | already 1 at startup | exactly 1 |

Additional PR checks:

- `GET /api/conversations/search?limit=100` returned 100 items plus a next page;
  `GET /api/conversations/count` returned 120, while the runtime count remained 0.
- Eight concurrent event-count requests for the same unloaded conversation all
  returned 460; the process created one lease and logged exactly one resume.
- A fork using an unloaded persisted conversation's existing ID returned HTTP
  409, and the target `meta.json` SHA-256 was unchanged.
- With one of 120 persisted records changed to `running`, startup hydrated only
  that record, logged one resume, retained all 120 catalog records, and reported
  exactly one `error` record after crash recovery.

The PR therefore satisfies the acceptance boundary: catalog operations remain
lightweight, runtime operations hydrate on demand once, running recovery is
preserved, and persisted IDs remain protected during fork.

## Reproduction

Generate the corpus with the PR packages on `PYTHONPATH`:

```bash
python .pr/issue-4102/live_lazy_loading_evidence.py generate \
  --root /tmp/issue-4102/corpus --conversations 120 --events 460
```

Create detached worktrees for the two exact refs, then run:

```bash
python .pr/issue-4102/live_lazy_loading_evidence.py benchmark \
  --main-source /path/to/main-worktree \
  --pr-source /path/to/pr-worktree \
  --python /path/to/software-agent-sdk/.venv/bin/python \
  --corpus /tmp/issue-4102/corpus \
  --manifest /tmp/issue-4102/corpus_manifest.json \
  --output /tmp/issue-4102/results
```

Machine-readable stable results are in `live_results.json`; the harness also
writes full server logs and an expanded `live_results.json` into the output
directory for audit.
