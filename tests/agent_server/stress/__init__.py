"""Stress / scale tests for the agent-server.

Each test exercises a failure mode that's likely to break the New User
Journey at realistic scale — parallel sub-agents, many conversations,
long-running commands, slow webhooks, websocket back-pressure, and so on —
by driving the agent-server in-process via FastAPI's ASGI transport. No
real binary, no real network, no real LLM: everything runs against
``ConversationService`` + ``BashEventService`` instances backed by
``tmp_path``.

The suite is excluded from default pytest runs via the ``stress`` marker
(``addopts = -m 'not stress'`` in pyproject.toml) so it doesn't run on every
``make test``. Files are still collected, so import-time breakage in a
stress test surfaces immediately.

POSIX-only by construction: the suite uses ``psutil.num_fds()``, POSIX file
locks, bash pipelines, and POSIX shell builtins. There are no Windows shims
and the FD assertions silently no-op on platforms where psutil can't read
FDs (see ``probe.py``). Don't try to run this on Windows.

Layout
------
- ``conftest.py``    Per-test ``ConversationService``/``BashEventService``
                     fixtures, the in-process FastAPI app, an
                     ``httpx.AsyncClient`` over ASGITransport, and the
                     ``ResourceProbe`` fixture.
- ``budgets.py``     Frozen dataclasses with the assertion thresholds
                     (per-call latency, RSS deltas, FD growth, event
                     counts, etc.). Relative-to-baseline ratios where
                     possible; absolute thresholds only for failure modes
                     whose definition *is* unbounded growth.
- ``probe.py``       psutil-backed background sampler — RSS, FDs, threads,
                     CPU — used to assert peak/delta budgets.
- ``scripts.py``     Shared helpers: ``SlowTestLLM``, the "create the
                     conversation, then ``switch_llm`` to a TestLLM"
                     dance (placeholder LLM survives the JSON round-trip
                     in ``start_conversation``; TestLLM doesn't), and
                     ``wait_for_terminal`` polling.
- ``test_*.py``      One file per failure mode. Each file's module
                     docstring names the bug class it catches and any
                     architectural caveats.

How to run
----------
The suite is a marker-based opt-in. Pass ``-m stress`` to override the
``-m 'not stress'`` filter set in ``addopts``::

    uv run pytest -m stress
    uv run pytest -m stress tests/agent_server/stress/test_conversation_listing.py

A bare ``pytest tests/agent_server/stress/`` will collect-then-deselect
because the addopts filter still applies — pass ``-m stress`` alongside
the path if you want a path-scoped run.

What you'll see
---------------
- On pass: ``N passed in T s``. Most files are a single test.
- On budget breach: an ``AssertionError`` with the measured value, the
  budget, and a one-line diagnosis pointing at the likely regression
  (e.g. "listing path may be materializing the full store into memory
  per call"). The budget files in ``budgets.py`` document the intent of
  each threshold so you can decide whether to fix the regression or
  re-tune.
- A few tests are intentionally marked ``@pytest.mark.xfail(strict=True)``
  to surface known bugs as regression markers — if one of those starts
  passing, the bug got fixed and the marker should be removed.
"""
