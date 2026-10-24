# Reviewed verification evidence

This packet summarizes live checks on `enyst/agent-sdk` at TARGET
`5966c781ed014efc96c028a99bddd4ff78426741` on 2026-10-23 UTC.
It contains selected assertions and provenance, not complete transcripts.
The independent review is documented in [review-notes.md](review-notes.md) and
[independent-review.json](independent-review.json).

- `issue-39-skill-subpath.json` through `issue-42-child-unborn.json`: two fresh reproductions each, with their positive controls.
- `retired-known-bugs.json`: F10 tag consistency and F14 init-time trajectory path each reproduced as xfail twice at BASE and xpass twice at TARGET, before marker removal.
- `docker-results.json`: real Docker lifecycle/retention proof and two startup-deadline passes; release-image limitation stated explicitly.
- `runtime-supplements.json`: independently inspected F31 retention/disk-budget and F33 sync/async SDK lifecycle observations, including its exact fresh maintained map replay; F31 remains standalone supplemental evidence and the released-child limitation is retained.
- `feature-results.json` / `feature-results.md`: per-feature verdicts, run IDs, branches and source SHAs; these metadata tables do not substitute for the selected exchanges above.
- `capability-lanes.json`: exact focused F16 OpenVSCode URL/editor replay and independently reviewed F17 immutable-storage exchanges, with frozen source identity and isolated capability setup.
- `provenance.json`: source run IDs, relative evidence names and SHA-256 checksums for local traceability.
- `publication-scan.json`: scan of the curated output against configured environment secret values and common credential formats.

Private directories, keys, environment dumps, raw credential exchanges, raw
conversation state and full server logs were excluded. Temporary absolute
paths and local server addresses were normalized. SHA-256 checksums refer to
the original local artifacts, which are not included in this packet.

The publication directory contains only the curated public files. The canonical
raw CLI report is retained separately outside this directory. Re-run the
publication scan after regenerating the final per-feature snapshot.
