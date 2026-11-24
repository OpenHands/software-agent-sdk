# Trajectory capture pilot

Agent Server and local SDK conversations support opt-in shadow capture for the
standard LiteLLM Chat Completions and Responses adapters. The journal records
requests at the LiteLLM boundary, before LiteLLM's provider-specific
transformation. It keeps ordered model inputs and tool definitions, including
duplicate entries. Transport credentials and headers are excluded. Model and
tool content can contain private data. Keep the spool, witness, archive, and
decoded journal private.

Set `OPENHANDS_TRAJECTORY_SHADOW_IDS` to the comma-separated, hyphenated UUIDs
of enrolled conversations. Set `OPENHANDS_TRAJECTORY_SHADOW_SPOOL` and
`OPENHANDS_TRAJECTORY_WITNESS_SPOOL` to separately retained directories. Agent
Server records an admitted run in the witness before scheduling it. The SDK
adds boundary checksums to the witness before writing the shadow records. Set
`OPENHANDS_TRAJECTORY_SHADOW_ORIGIN` to `interactive`, `automation`, or
`evaluation` when the enrolled cohort shares a known origin. Otherwise the
journal records `unknown`.

Use local POSIX storage that survives sandbox loss. The platform owns the
spool and its SQLite WAL files through teardown. Protect both volumes from
tool processes. Keep each database and its WAL together and use a platform
worker with access on the same host while capture is active. Drain it as part
of teardown or recover it after hard termination. Cloud Storage FUSE and
network filesystems lack the required SQLite WAL guarantees. A sandbox whose
only disk is deleted at teardown needs a platform handoff before enrollment.

## Seal and compare

Run the worker with platform credentials. Configure the bucket for private access,
version retention, and an IAM identity that can create and read objects. The
sandbox session key is independent of these credentials. A seal pins each
immutable compressed segment by generation and checksum. The worker reads
back every segment before publishing the seal. The archive head advances
only when its expected generation still matches and the spool reproduces
the entire acknowledged prefix. Concurrent workers can retry against the
new head. Seal references and journal
data stay in the `shadow/v1` namespace.

```bash
export OPENHANDS_SUPPRESS_BANNER=1
python -m openhands.agent_server.persistence.trajectory seal "$SPOOL" "$CONVERSATION" "$STORE" > seal.json
python -m openhands.agent_server.persistence.trajectory witness "$WITNESS" "$CONVERSATION" \
  --origin interactive --admitted-runs admitted-run-ids.json > admission.json
python -m openhands.agent_server.persistence.trajectory compare "$STORE" seal.json admission.json \
  --legacy verified-legacy.json > comparison.json
```

`STORE` is a local directory or a `gs://` prefix. The platform worker uses
its existing `gcloud storage` installation and credentials for GCS. The
witness command can derive admitted runs
from Agent Server when there is no external run list. The pilot also needs a Cloud
enrollment inventory to detect conversations missing from both stores and
the witness. Only a comparison supplied with independent run IDs can report
shadow eligibility. The SDK records provider options it cannot safely
capture as coverage gaps. Legacy coverage comes from generation-pinned
native event bytes. Model attempts and run IDs the legacy source cannot
prove stay missing. A comparison records gaps,
orphaned links, unfinished work, and shadow-only or legacy-only evidence.

Use `read STORE seal.json` to read verified JSONL for the dataset exporter.
The first line contains the seal and its reference. A consumer must require
the full sequence through that watermark and a successful process exit.
After losing a pinned generation, recover from the retained spool
or another copy with matching bytes. A replacement object generation gets
a new seal reference. Seals also record a checksum of the uncompressed
records so compression changes can be verified independently of object bytes.
If the archive head disappears after publication, the uploader stops. Use the
platform's retained acknowledgment to restore the latest verified head before
resuming uploads.

Archive each comparison with the admission inventory. Measure the admitted
run denominator, explained and unexplained gaps by boundary, sequence
coverage, readable reasoning, bytes, object writes, append latency and
time to seal. Exercise sandbox termination, retries, interrupts, rejected
and parallel tools, resumes, deployment, and storage failures before
changing the delivery reader. The proposed observation and rollback gates
are in [SDK #7036](https://github.com/OpenHands/software-agent-sdk/issues/7036).

## Migration

Dry-run `select admission.json candidates.json` for each pinned inventory
entry. The input is a JSON array with each candidate's `source` (`shadow`
or `legacy`), `store` (directory or GCS prefix), and `archive` reference
(`key`, `generation`, `sha256`, and `size`). A shadow reference points to
a seal. A legacy reference points to a verified inventory comprising
`conversation_id`, `events` (pinned object references), and `gaps`.
The reader checks the source bytes before deriving evidence. Older Classic
caches and other source families still use the dataset recovery workflow. Keep both references in the report. Promotion
requires one candidate that covers the required evidence and every
compatible candidate's evidence, with zero candidate gaps. Keep the legacy
reference when evidence ties. Independently verified evidence can resolve
conflicts. Unknown or incomparable evidence produces a hold.

Publish an immutable version of the selection manifest and preserve the
references used by earlier deliveries. Verify each referenced object and
previous delivery before switching the reader's manifest pointer. Keep both
writers and rollback to the old manifest during the observation window.
