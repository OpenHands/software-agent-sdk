#!/usr/bin/env bash
set -euo pipefail

# Scan GCS buckets for objects containing sensitive data within a time window.
#
# Required environment variables:
#   GOOGLE_APPLICATION_CREDENTIALS - Path to GCP service account key file
#     OR the workflow sets up gcloud auth before calling this script.
#
# Arguments:
#   $1 - FROM_TIMESTAMP (ISO 8601, e.g., 2026-03-30T00:00:00Z)
#   $2 - TO_TIMESTAMP   (ISO 8601, e.g., 2026-04-03T00:00:00Z)
#
# Optional environment variables:
#   GCS_BUCKETS - Comma-separated list of GCS bucket names to scan
#                 (default: openhands-evaluation-logs,openhands-artifacts)
#   GCS_MAX_OBJECTS - Max objects to scan per bucket (default: 500)
#   GCS_MAX_FILE_SIZE - Max file size in bytes to download for scanning (default: 10485760 = 10MB)
#
# Output: JSON array of findings to stdout.

FROM_TS="${1:?Usage: gcs-scan.sh FROM_TIMESTAMP TO_TIMESTAMP}"
TO_TS="${2:?Usage: gcs-scan.sh FROM_TIMESTAMP TO_TIMESTAMP}"

GCS_BUCKETS="${GCS_BUCKETS:-openhands-evaluation-logs,openhands-artifacts}"
GCS_MAX_OBJECTS="${GCS_MAX_OBJECTS:-500}"
GCS_MAX_FILE_SIZE="${GCS_MAX_FILE_SIZE:-10485760}"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PATTERNS_FILE="$SCRIPT_DIR/patterns.txt"
WORK_DIR=$(mktemp -d)
trap 'rm -rf "$WORK_DIR"' EXIT

# Build grep pattern from patterns.txt (skip comments and empty lines)
GREP_PATTERNS="$WORK_DIR/grep_patterns.txt"
grep -v '^\s*#' "$PATTERNS_FILE" | grep -v '^\s*$' > "$GREP_PATTERNS"

echo "Scanning GCS buckets: $GCS_BUCKETS" >&2
echo "Time range: $FROM_TS -> $TO_TS" >&2

# Convert ISO timestamps to epoch for comparison
FROM_EPOCH=$(date -u -j -f "%Y-%m-%dT%H:%M:%SZ" "$FROM_TS" "+%s" 2>/dev/null || date -u -d "$FROM_TS" "+%s")
TO_EPOCH=$(date -u -j -f "%Y-%m-%dT%H:%M:%SZ" "$TO_TS" "+%s" 2>/dev/null || date -u -d "$TO_TS" "+%s")

ALL_FINDINGS="[]"

IFS=',' read -ra BUCKETS <<< "$GCS_BUCKETS"
for BUCKET in "${BUCKETS[@]}"; do
  BUCKET=$(echo "$BUCKET" | xargs)  # trim whitespace
  echo "Scanning bucket: gs://$BUCKET" >&2

  # List objects with creation time, filter by time window
  # Use gsutil ls -l for detailed listing, or gcloud storage ls --long
  OBJECT_LIST="$WORK_DIR/objects_${BUCKET}.json"

  # Use gcloud storage to list objects as JSON
  if ! gcloud storage ls "gs://${BUCKET}/**" \
    --format="json(name,timeCreated,size)" \
    --filter="timeCreated>=${FROM_TS} AND timeCreated<=${TO_TS}" \
    2>/dev/null | head -c 50000000 > "$OBJECT_LIST"; then
    echo "  WARNING: Failed to list objects in gs://$BUCKET, skipping" >&2
    continue
  fi

  # Parse and filter objects - focus on log/text/json files
  OBJECTS=$(jq -r '[.[]? |
    select(
      (.name | test("\\.(log|txt|json|yaml|yml|env|cfg|conf|out|err)$"; "i")) or
      (.name | test("(log|debug|output|artifact|result)"; "i"))
    ) |
    select((.size // "0") | tonumber < '"$GCS_MAX_FILE_SIZE"') |
    .name
  ] | .[0:'"$GCS_MAX_OBJECTS"'] | .[]' "$OBJECT_LIST" 2>/dev/null || true)

  if [[ -z "$OBJECTS" ]]; then
    echo "  No matching objects in time window" >&2
    continue
  fi

  OBJ_COUNT=$(echo "$OBJECTS" | wc -l | xargs)
  echo "  Found $OBJ_COUNT objects to scan" >&2

  BUCKET_FINDINGS="[]"
  SCANNED=0

  while IFS= read -r OBJ_NAME; do
    [[ -z "$OBJ_NAME" ]] && continue
    SCANNED=$((SCANNED + 1))

    # Download to temp file
    OBJ_FILE="$WORK_DIR/current_object"
    if ! gcloud storage cp "gs://${BUCKET}/${OBJ_NAME}" "$OBJ_FILE" 2>/dev/null; then
      echo "  WARNING: Failed to download gs://${BUCKET}/${OBJ_NAME}" >&2
      continue
    fi

    # Scan for secret patterns
    MATCHES=$(grep -n -f "$GREP_PATTERNS" "$OBJ_FILE" 2>/dev/null || true)
    if [[ -n "$MATCHES" ]]; then
      # Truncate each match line to 200 chars to avoid leaking full secrets in output
      TRUNCATED_MATCHES=$(echo "$MATCHES" | head -20 | while IFS= read -r line; do
        echo "${line:0:200}"
      done)

      MATCH_COUNT=$(echo "$MATCHES" | wc -l | xargs)
      echo "  FOUND $MATCH_COUNT matches in gs://${BUCKET}/${OBJ_NAME}" >&2

      FINDING=$(jq -n \
        --arg bucket "$BUCKET" \
        --arg object "$OBJ_NAME" \
        --arg match_count "$MATCH_COUNT" \
        --arg sample_matches "$TRUNCATED_MATCHES" \
        '{
          bucket: $bucket,
          object: $object,
          match_count: ($match_count | tonumber),
          sample_matches: ($sample_matches | split("\n"))
        }')

      BUCKET_FINDINGS=$(echo "$BUCKET_FINDINGS" | jq --argjson f "$FINDING" '. + [$f]')
    fi

    rm -f "$OBJ_FILE"

    # Progress
    if [[ $((SCANNED % 50)) -eq 0 ]]; then
      echo "  Scanned $SCANNED / $OBJ_COUNT objects..." >&2
    fi
  done <<< "$OBJECTS"

  BUCKET_FINDING_COUNT=$(echo "$BUCKET_FINDINGS" | jq 'length')
  echo "  Bucket $BUCKET: $BUCKET_FINDING_COUNT objects with secrets (scanned $SCANNED)" >&2

  ALL_FINDINGS=$(echo "$ALL_FINDINGS $BUCKET_FINDINGS" | jq -s '.[0] + .[1]')
done

TOTAL_FINDINGS=$(echo "$ALL_FINDINGS" | jq 'length')
echo "Total GCS objects with potential secrets: $TOTAL_FINDINGS" >&2

# Output structured summary
echo "$ALL_FINDINGS" | jq '{
  total_objects_with_secrets: length,
  by_bucket: (group_by(.bucket) | map({
    bucket: .[0].bucket,
    count: length,
    objects: [.[] | {object: .object, match_count: .match_count}]
  })),
  findings: .
}'
