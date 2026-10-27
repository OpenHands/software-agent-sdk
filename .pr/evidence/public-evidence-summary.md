# Public feature evidence export

Generated from aggregation snapshot 2026-10-23T06:44:47+00:00 (5bd67842feefeb4570bc85bb75872fa600f78a728feba7f5bde1f79da1671ef0).

Current/frozen IDs: 948/935; initial family outputs: 34/34.
Admitted ledger attempts: 1480 from 109 TARGET/BASE runs; output-only statuses: 0.
The canonical CLI report has 1471 rows after its latest-per-run-and-feature reduction. The compact public JSON preserves all 1480 raw ledger rows and their branches.

Aggregation and CLI live-ledger inputs agree: **True**. A false value means tail activity changed the ledger; regenerate after workers finish.

Public candidates: `feature-results.json`, `feature-results.md`, and `feature-results-scan.json`.
The canonical raw CLI table, summary, exact invocation and ledger fingerprints remain outside this public directory in the sibling local-cli-evidence directory. They include free-text ledger fields and use a different row reduction.

Rerun the scratch export-public-evidence.py script with --require-stable after tail runs finish. This refreshes aggregation, invokes `control-agent-server evidence report --runs ... --out ...`, and reruns the metadata/secret scan. It does not contact servers or APIs.

No source artifact is copied. No response body, transcript, workspace file or private state is included in these feature tables. Known secret values were compared without printing.
