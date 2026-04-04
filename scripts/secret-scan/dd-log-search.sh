#!/usr/bin/env bash
set -euo pipefail

# Query Datadog Logs Search API for secret patterns within a time window.
#
# Required environment variables:
#   DD_API_KEY    - Datadog API key
#   DD_APP_KEY    - Datadog Application key
#
# Arguments:
#   $1 - FROM_TIMESTAMP (ISO 8601, e.g., 2026-03-30T00:00:00Z)
#   $2 - TO_TIMESTAMP   (ISO 8601, e.g., 2026-04-03T00:00:00Z)
#
# Optional environment variables:
#   DD_SITE       - Datadog site (default: datadoghq.com)
#   DD_QUERY_EXTRA - Additional Datadog query filter (e.g., "service:enterprise-server")
#
# Output: JSON array of findings to stdout.

FROM_TS="${1:?Usage: dd-log-search.sh FROM_TIMESTAMP TO_TIMESTAMP}"
TO_TS="${2:?Usage: dd-log-search.sh FROM_TIMESTAMP TO_TIMESTAMP}"

: "${DD_API_KEY:?DD_API_KEY must be set}"
: "${DD_APP_KEY:?DD_APP_KEY must be set}"

DD_SITE="${DD_SITE:-datadoghq.com}"
DD_QUERY_EXTRA="${DD_QUERY_EXTRA:-}"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PATTERNS_FILE="$SCRIPT_DIR/patterns.txt"

# Build Datadog query from secret patterns.
# We use a subset of high-signal patterns suitable for Datadog query syntax.
# Datadog uses wildcard matching (* for any chars), not full regex.
DD_SECRET_PATTERNS=(
  "sk-proj-*"
  "sk-ant-*"
  "sk-oh-*"
  "tvly-*"
  "sk-or-v1-*"
  "gsk_*"
  "hf_*"
  "tgp_v1_*"
  "ghp_*"
  "github_pat_*"
  "AKIA*"
  "ANTHROPIC_API_KEY=*"
  "OPENAI_API_KEY=*"
  "GEMINI_API_KEY=*"
  "LMNR_PROJECT_API_KEY=*"
  '"api_key"*'
  '"password"*'
  '"secret"*'
  "BEGIN*PRIVATE KEY"
  "Authorization:*Basic*"
)

# Build the OR query
QUERY_PARTS=()
for pattern in "${DD_SECRET_PATTERNS[@]}"; do
  QUERY_PARTS+=("@message:${pattern}")
done

# Join with OR
QUERY="$(IFS=' OR '; echo "${QUERY_PARTS[*]}")"

# Append extra filters if provided
if [[ -n "$DD_QUERY_EXTRA" ]]; then
  QUERY="(${QUERY}) ${DD_QUERY_EXTRA}"
fi

echo "Datadog query: $QUERY" >&2
echo "Time range: $FROM_TS -> $TO_TS" >&2

# Collect all results with pagination
ALL_RESULTS="[]"
CURSOR=""
PAGE=0
MAX_PAGES=20  # Safety limit

while [[ $PAGE -lt $MAX_PAGES ]]; do
  PAGE=$((PAGE + 1))
  echo "Fetching page $PAGE..." >&2

  # Build request body
  if [[ -n "$CURSOR" ]]; then
    REQUEST_BODY=$(cat <<ENDJSON
{
  "filter": {
    "query": "$QUERY",
    "from": "$FROM_TS",
    "to": "$TO_TS"
  },
  "page": {
    "cursor": "$CURSOR",
    "limit": 100
  },
  "sort": "timestamp"
}
ENDJSON
)
  else
    REQUEST_BODY=$(cat <<ENDJSON
{
  "filter": {
    "query": "$QUERY",
    "from": "$FROM_TS",
    "to": "$TO_TS"
  },
  "page": {
    "limit": 100
  },
  "sort": "timestamp"
}
ENDJSON
)
  fi

  RESPONSE=$(curl -sS -X POST \
    "https://api.${DD_SITE}/api/v2/logs/events/search" \
    -H "Content-Type: application/json" \
    -H "DD-API-KEY: ${DD_API_KEY}" \
    -H "DD-APPLICATION-KEY: ${DD_APP_KEY}" \
    -d "$REQUEST_BODY")

  # Check for errors
  if echo "$RESPONSE" | jq -e '.errors' > /dev/null 2>&1; then
    ERRORS=$(echo "$RESPONSE" | jq -r '.errors[]' 2>/dev/null || echo "$RESPONSE")
    echo "ERROR: Datadog API error: $ERRORS" >&2
    exit 1
  fi

  # Extract log entries with secret redaction applied to messages
  ENTRIES=$(echo "$RESPONSE" | jq -c '[.data[]? | {
    id: .id,
    timestamp: .attributes.timestamp,
    service: .attributes.service,
    host: .attributes.host,
    message: ((.attributes.message // .attributes.attributes.message // "")[0:500]
      | gsub("(?i)sk-proj-[A-Za-z0-9_-]{4}[A-Za-z0-9_-]+"; "sk-proj-****[REDACTED]")
      | gsub("(?i)sk-ant-[A-Za-z0-9_-]{4}[A-Za-z0-9_-]+"; "sk-ant-****[REDACTED]")
      | gsub("(?i)sk-oh-[A-Za-z0-9_-]{4}[A-Za-z0-9_-]+"; "sk-oh-****[REDACTED]")
      | gsub("(?i)sk-or-v1-[A-Za-z0-9_-]{4}[A-Za-z0-9_-]+"; "sk-or-v1-****[REDACTED]")
      | gsub("(?i)ghp_[A-Za-z0-9]{4}[A-Za-z0-9]+"; "ghp_****[REDACTED]")
      | gsub("(?i)github_pat_[A-Za-z0-9_]{4}[A-Za-z0-9_]+"; "github_pat_****[REDACTED]")
      | gsub("AKIA[0-9A-Z]{4}[0-9A-Z]+"; "AKIA****[REDACTED]")
      | gsub("(?i)tvly-[A-Za-z0-9]{4}[A-Za-z0-9]+"; "tvly-****[REDACTED]")
      | gsub("(?i)gsk_[A-Za-z0-9]{4}[A-Za-z0-9]+"; "gsk_****[REDACTED]")
      | gsub("(?i)hf_[A-Za-z0-9]{4}[A-Za-z0-9]+"; "hf_****[REDACTED]")
      | gsub("(?i)tgp_v1_[A-Za-z0-9]{4}[A-Za-z0-9]+"; "tgp_v1_****[REDACTED]")
    ),
    tags: .attributes.tags
  }]')

  COUNT=$(echo "$ENTRIES" | jq 'length')
  echo "  Found $COUNT entries on page $PAGE" >&2

  # Merge into all results
  ALL_RESULTS=$(echo "$ALL_RESULTS $ENTRIES" | jq -s '.[0] + .[1]')

  # Check for next page
  CURSOR=$(echo "$RESPONSE" | jq -r '.meta.page.after // empty')
  if [[ -z "$CURSOR" ]] || [[ "$COUNT" -eq 0 ]]; then
    break
  fi
done

TOTAL=$(echo "$ALL_RESULTS" | jq 'length')
echo "Total log entries with potential secrets: $TOTAL" >&2

# Post-process: group by service and deduplicate by pattern
SUMMARY=$(echo "$ALL_RESULTS" | jq '{
  total_matches: length,
  by_service: (group_by(.service) | map({
    service: .[0].service,
    count: length,
    sample_timestamps: [.[0:3][] | .timestamp],
    sample_messages: [.[0:3][] | .message]
  })),
  entries: .
}')

echo "$SUMMARY"
