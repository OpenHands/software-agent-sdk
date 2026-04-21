---
name: pr-triage
description: >-
  This skill should be used when the user asks to "triage PRs",
  "scan pull requests", "check PR status", "review stale PRs",
  "which PRs need attention", or mentions triaging issues and pull requests.
  Scans all open PRs in OpenHands/software-agent-sdk, categorizes them by
  review status, triggers the ReviewBot on unreviewed PRs, and produces
  a summary report of what needs attention.
---

# PR & Issue Triage Skill

Scan all open pull requests and issues in `OpenHands/software-agent-sdk`,
categorize them, trigger review bots where needed, and produce an
actionable summary report.

## Prerequisites

- `GITHUB_TOKEN` environment variable with repo access
- `gh` CLI authenticated (the token is auto-detected)

## Workflow

### Step 1: Fetch All Open PRs

```bash
gh pr list --repo OpenHands/software-agent-sdk --state open \
  --json number,title,author,createdAt,updatedAt,isDraft,labels,url \
  --limit 200
```

### Step 2: Get Detailed Review Status via GraphQL

For each non-draft PR, query review threads and latest reviews:

```bash
gh api graphql -f query='
{
  repository(owner: "OpenHands", name: "software-agent-sdk") {
    pullRequests(states: OPEN, first: 100) {
      nodes {
        number
        title
        isDraft
        createdAt
        updatedAt
        author { login }
        reviewThreads(first: 50) {
          totalCount
          nodes {
            isResolved
            comments(first: 1) {
              nodes { author { login } body createdAt }
            }
          }
        }
        latestReviews(first: 10) {
          nodes {
            author { login }
            state
            submittedAt
          }
        }
        reviews(first: 5, states: [CHANGES_REQUESTED]) {
          totalCount
        }
      }
    }
  }
}'
```

### Step 3: Categorize Each PR

Classify every non-draft (ready for review) PR into one of these buckets:

| Category | Condition | Action |
|----------|-----------|--------|
| ✅ **APPROVED** | Has `APPROVED` review, 0 unresolved threads | Ready to merge — nudge the author or maintainer |
| ⏳ **CHANGES REQUESTED** | Has `CHANGES_REQUESTED` review | Waiting for author — leave as is |
| 💬 **HAS UNRESOLVED THREADS** | >0 unresolved review threads | Waiting for author — leave as is |
| 🔴 **NO REVIEWS** | Zero reviews, zero `/codereview` comments | **Trigger ReviewBot** |
| 🟡 **BOT-ONLY REVIEWED** | Only `all-hands-bot`/`copilot-*` reviewed, 0 unresolved threads, no `/codereview` comment | **Trigger ReviewBot** |
| 🟠 **REVIEWED (needs decision)** | Human reviewed, threads resolved, no approval | Needs maintainer attention |

### Step 4: Check for Existing Bot Comments

Before triggering the ReviewBot, verify no `/codereview` or `/github-pr-review`
comment already exists:

```bash
gh api "repos/OpenHands/software-agent-sdk/issues/{PR_NUMBER}/comments" \
  --jq '[.[] | select(.body | test("/codereview|/github-pr-review"))] | length'
```

If the count is `> 0`, **skip** — the bot was already triggered.

### Step 5: Trigger ReviewBot

For PRs that need it (per rules above), post a comment that triggers the
review commands **and** includes a disclosure note:

```bash
gh api "repos/OpenHands/software-agent-sdk/issues/{PR_NUMBER}/comments" \
  -f body="@OpenHands /codereview /github-pr-review

---
_🤖 This comment was automatically posted by the **pr-triage** skill ([OpenHands](https://github.com/All-Hands-AI/OpenHands)) on behalf of a maintainer._"
```

### Step 6: Identify Stale PRs

Flag PRs and issues that may need closing:

| Staleness Level | Condition |
|-----------------|-----------|
| 🕸️ **Stale** | Non-draft PR with no update in >14 days |
| 🗑️ **Ancient draft** | Draft PR created >60 days ago |
| 📦 **Stale issue** | Issue with no update in >30 days |

### Step 7: Produce the Report

Output a structured report with these sections:

1. **🚀 Ready to Merge** — Approved PRs with no blockers
2. **🔴 ReviewBot Triggered** — PRs where the bot was just triggered
3. **⏳ Waiting for Author** — Changes requested or unresolved threads
4. **🟠 Needs Maintainer Decision** — Reviewed but not approved
5. **🗑️ Close Candidates** — Ancient drafts, stale PRs/issues
6. **📊 Summary Stats** — Total counts per category

## Decision Rules

1. **Never trigger the ReviewBot** if there are existing unaddressed
   review comments (unresolved threads or `CHANGES_REQUESTED`).
2. **Always check** for existing `/codereview` comments before posting.
3. **Do not close PRs automatically** — only flag them. Let the human decide.
4. **Draft PRs are informational only** — never trigger reviews on drafts.

## Example Run

```
📊 PR Triage Report — 2026-04-21
═══════════════════════════════════

🚀 Ready to Merge (6):
  #1996 ✅ test: use default agent preset for integration tests (xingyaoww)
  #2196 ✅ fix: redact credentials from URLs (jpshackelford)
  ...

🔴 ReviewBot Triggered (10):
  #1780 → fix: remote conversation OTEL session ID (timon0305)
  #2142 → feat: add MaybeDontAnalyzer security analyzer (robotdan)
  ...

⏳ Waiting for Author (15):
  #2470 CHANGES_REQUESTED: Alternate fix for stale execution (DoubleDensity)
  #2349 💬 1 unresolved: condenser for subagents (VascoSch92)
  ...

🗑️ Close Candidates (18 ancient drafts):
  #704 (193d) Add Kubernetes build context generator (tofarr)
  #796 (185d) feat: add enum support to environment parser (tofarr)
  ...
```
