# Event-Sourced State: Systems Metrics

We report four SDK-attributable systems metrics for the event-sourced state management design described in Section 4.2, including its persistence and crash recovery paths. We extract real event payloads from 433 SWE-Bench Verified evaluation conversations (39,870 total events) and replay them through the SDK's production I/O code path on a local machine. The SDK does not instrument persist or replay timing internally, so storage metrics are measured directly from the traces while latency metrics are obtained by re-executing the same `LocalFileStore` lock-and-write path with the original payloads under a fixed deployment configuration.

## Metrics

1. **Persist latency per event / action cycle.** The wall-clock time to durably append a single event to the log. Each append acquires a file lock, serializes the event to JSON, and writes a new file. An action cycle comprises one ActionEvent write followed by one ObservationEvent write — the two persists that bracket every tool invocation.

2. **Replay time vs. log size.** The time to reconstruct in-memory state from the on-disk event log. This has two phases: index rebuild (listing the events directory and parsing filenames via regex) and full replay (reading and deserializing every event file). This cost is paid once on process startup or after a crash.

3. **Storage growth.** The cumulative on-disk footprint of the event log as a function of conversation length, broken down by event type. Since each event is an independent JSON file, total storage grows linearly with event count.

4. **Time-to-recover via replay after failures.** The end-to-end latency of the crash recovery path: load all persisted events, then scan in reverse for actions that lack a matching observation (unmatched-action detection, as implemented in `ConversationState.get_unmatched_actions()`). An unmatched action indicates the agent crashed mid-execution and must re-dispatch.

## Setup

**Workload:** Event payloads extracted from a full SWE-Bench Verified evaluation run (433 instances, `litellm_proxy` backend, max 500 iterations). Events range from 190B to 260KB, with a median of 1.5KB.
**I/O path:** All persist measurements exercise the production code path — `LocalFileStore.lock()` followed by `LocalFileStore.write()` — with the original JSON payloads from the evaluation traces.

## Scripts

All scripts accept `--eval-dir <path>` pointing to an evaluation run directory (must contain `conversations/` with `.tar.gz` traces, and `output.jsonl`).

| Script | Metrics | Usage |
|---|---|---|
| `bench_persist_latency.py` | Persist latency per event / action cycle | `python bench_persist_latency.py --eval-dir <path>` |
| `bench_replay_and_recovery.py` | Replay time vs. log size, time-to-recover | `python bench_replay_and_recovery.py --eval-dir <path>` |
| `bench_storage_growth.py` | Storage growth and composition | `python bench_storage_growth.py --eval-dir <path>` |

---

## Results

### 1. Persist Latency per Event / Action Cycle

**Method:** Extract persisted event files from 29 sampled SWE-Bench conversations. Replay each through the `LocalFileStore.lock()` + `LocalFileStore.write()` path with the original JSON payloads.

#### Per-Event Persist Latency

| Event Type | N | Median | Mean | P95 | Median Size |
|---|---|---|---|---|---|
| SystemPromptEvent | 29 | 0.351ms | 0.374ms | 0.582ms | 24,500B |
| MessageEvent | 29 | 0.201ms | 0.206ms | 0.261ms | 3,239B |
| ActionEvent | 1,264 | 0.163ms | 0.175ms | 0.244ms | 1,071B |
| ObservationEvent | 1,264 | 0.167ms | 0.180ms | 0.255ms | 2,254B |
| ConversationStateUpdateEvent | 58 | 0.168ms | 0.172ms | 0.218ms | 191B |
| **All Events** | **2,644** | **0.166ms** | **0.180ms** | **0.267ms** | **1,395B** |

#### Per Action Cycle (Action + Observation)

| Metric | Value |
|---|---|
| Median | 0.36ms |
| Mean | 0.37ms |

---

### 2. Replay Time vs. Log Size

**Method:** Build event logs of increasing size from real payloads. Measure index rebuild (directory listing + filename regex parse) and full replay (read + JSON parse all events).

| Events | Storage | Index Rebuild | Full Replay |
|---|---|---|---|
| 10 | 36.4KB | 0.02ms | 0.30ms |
| 25 | 57.5KB | 0.03ms | 0.58ms |
| 50 | 122.1KB | 0.05ms | 1.21ms |
| 100 | 227.0KB | 0.08ms | 2.28ms |
| 200 | 576.2KB | 0.17ms | 4.89ms |
| 500 | 2.0MB | 0.37ms | 14.26ms |
| 1,000 | 4.3MB | 0.75ms | 29.49ms |
| 1,500 | 8.2MB | 1.09ms | 48.06ms |

Replay scales linearly with event count. At the maximum observed conversation size in the evaluation (358 events), full replay completes in under 10ms.

---

### 3. Storage Growth

**Method:** Analyze all 433 SWE-Bench conversations. Measure per-conversation storage and breakdown by event type.

#### Conversation Size Distribution

| Metric | Min | P25 | Median | P75 | Max |
|---|---|---|---|---|---|
| Events | 22 | 64 | 82 | 108 | 358 |
| Storage | 109.6KB | — | 380.0KB | 634.3KB | 3,357.0KB |

Mean events per conversation: 92.1 (stdev 39.9). Average event size: ~624 bytes. Storage grows linearly with event count.

#### Storage Composition by Event Type

| Event Type | Count | % Events | Total | % Storage | Avg Size |
|---|---|---|---|---|---|
| ObservationEvent | 19,065 | 47.8% | 177.1MB | 78.0% | 9.51KB |
| ActionEvent | 19,069 | 47.8% | 38.3MB | 16.9% | 2.05KB |
| SystemPromptEvent | 433 | 1.1% | 10.1MB | 4.5% | 23.93KB |
| MessageEvent | 433 | 1.1% | 1.4MB | 0.6% | 3.29KB |
| ConversationStateUpdateEvent | 866 | 2.2% | 0.2MB | 0.1% | 0.19KB |
| **Total** | **39,870** | | **227.1MB** | | |

ObservationEvents (tool outputs) account for 78% of storage despite being only 48% of events by count.

---

### 4. Time-to-Recover via Replay After Failures

**Method:** Build event logs from real payloads, then measure the full recovery path: read all events + reverse scan for actions without matching observations (unmatched-action detection, as implemented in `ConversationState.get_unmatched_actions()`).

| Events | Storage | Time-to-Recover |
|---|---|---|
| 10 | 36.4KB | 0.33ms |
| 25 | 57.5KB | 0.56ms |
| 50 | 122.1KB | 1.20ms |
| 100 | 227.0KB | 2.34ms |
| 200 | 576.2KB | 4.97ms |
| 500 | 2.0MB | 13.88ms |
| 1,000 | 4.3MB | 29.41ms |
| 1,500 | 8.2MB | 48.33ms |

Recovery includes deserializing all events and scanning in reverse for actions that lack a corresponding observation (indicating a crash mid-execution). At the median conversation size (82 events), recovery completes in ~2ms. At the largest observed conversation (358 events), recovery completes in under 10ms.
