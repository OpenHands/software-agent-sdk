# Contract: Agent Flight Recorder Trace Bundle

## Purpose

The bundle is the portable, canonical representation of one recorded run. Readers
must be able to validate and replay it without the originating SQLite index.

## Names and Layout

An active bundle is a directory named `<trace_id>.afr`. A finalized bundle may be
the same directory or `<trace_id>.afr.zip` with this internal layout:

```text
<trace_id>.afr/
├── manifest.json
├── records.jsonl
├── findings.jsonl
├── content/
│   └── <sha256>.<media-suffix>.zst
└── checksums.sha256
```

ZIP entries use forward slashes and paths relative to the bundle root. Importers
reject absolute paths, parent traversal, duplicate entries, and unsupported entry
types.

## Manifest

Required fields:

| Field | Active | Finalized | Contract |
| --- | --- | --- | --- |
| `format` | required | required | Literal `agent-flight-recorder` |
| `format_version` | required | required | Positive major version |
| `trace_id` | required | required | Matches directory identity and all records |
| `created_at` | required | required | ISO 8601 timestamp |
| `completed_at` | optional | required | ISO 8601 terminal timestamp |
| `status` | required | required | Recorded run status |
| `stop_reason` | optional | required | Required for terminal status |
| `recorder_version` | required | required | Semantic version |
| `record_count` | optional | required | Number of valid record lines |
| `finding_count` | optional | required | Number of valid finding lines |
| `content_encoding` | required | required | `zstd` for compressed blobs |

Finalization writes a temporary manifest and atomically replaces the prior file.
Adding findings invalidates final counts and checksums until finalization runs again.

## Record Stream

`records.jsonl` is UTF-8 append-only JSON Lines. Each complete line:

- validates as a Record from [the data model](../data-model.md#record);
- ends with a newline;
- has a unique `record_id`;
- has a `sequence` strictly greater than the preceding complete line;
- carries the manifest `trace_id`;
- preserves unknown fields and record kinds.

A truncated final line caused by a crash is ignored and reported as a
`recorder.warning`. Invalid complete lines fail import and identify their line
number. Re-importing an existing `record_id` does not create another record.

## Findings Stream

`findings.jsonl` is UTF-8 JSON Lines. Every finding validates against
[the Finding model](../data-model.md#finding). Each evidence and affected-span ID
must resolve within the same bundle. Invalid findings are rejected without changing
the record stream.

## Content Objects

`content_ref` uses `sha256:<digest>`. The corresponding filename begins with the
same digest. Readers decompress the object, hash the uncompressed bytes, and reject
a mismatch. Multiple records may reference one object. Unreferenced objects may be
reported but do not invalidate replay.

## Checksums

A finalized `checksums.sha256` lists the SHA-256 digest and relative path of the
manifest, both JSONL files, and every content object. Paths are sorted
lexicographically. The checksum file does not list itself. Import validates all
listed entries and rejects missing, duplicate, or mismatched entries.

## Compatibility

- Readers reject unsupported `format_version` major values.
- Readers ignore unknown object fields and retain them on round trip.
- Readers retain unknown record kinds as generic activities.
- Writers emit only the current format version.
- Schema migrations transform imported records before indexing but do not rewrite
  the source bundle unless the user explicitly exports a new bundle.

## Replay Guarantees

For a valid bundle, export followed by import preserves:

1. Record identity and sequence order.
2. Span lifecycle and relationship status.
3. Model-call correlation and aggregate usage.
4. Artifact associations.
5. Finding identity and evidence links.
6. Explicit provenance and uncertainty.

SQLite files are never part of the contract and may always be reconstructed.
