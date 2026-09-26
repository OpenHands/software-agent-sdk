---
name: pr-triage
description: >-
  This skill should be used when the user asks to "triage PRs",
  "scan pull requests", "check PR status", "review stale PRs",
  "which PRs need attention", or mentions triaging issues and pull requests.
  Scans all open PRs in a GitHub repository, categorizes them by review
  status, triggers the ReviewBot on unreviewed PRs, and produces a summary
  report of what needs attention. Works with any GitHub repository.
---

# PR & Issue Triage Skill

Scan all open pull requests and issues in a GitHub repository, categorize
them, trigger review bots where needed, and produce an actionable summary
report.

## Prerequisites

- `GITHUB_TOKEN` environment variable with repo access
- `gh` CLI authenticated (the token is auto-detected)

## Step 0: Determine the Target Repository

The target repository (`OWNER/REPO`) must be resolved before any other
step. Use the following priority order:

1. **Current git checkout** — run `git remote get-url origin` and extract
   the `OWNER/REPO` slug from the URL.
2. **Explicit user input** — if no git remote is found (or the workspace
   has no `.git` directory), **ask the user** which repository to triage.
   Do not guess or hard-code a default.

```bash
# Try to detect from git remote
REPO_SLUG=$(git remote get-url origin 2>/dev/null \
  | sed -E 's#.*github\.com[:/]##; s#\.git$##')

if [ -z "$REPO_SLUG" ]; then
  echo "No git remote found. Please provide the repository (OWNER/REPO)."
  # Wait for user input before proceeding.
fi
```

Store the resolved value in `REPO_SLUG` (e.g. `octocat/hello-world`) and
substitute it into every `gh` / `gh api` command below.

## Workflow

### Step 1: Fetch All Open PRs

```bash
gh pr list --repo "$REPO_SLUG" --state open \
  --json number,title,author,createdAt,updatedAt,isDraft,labels,url \
  --limit 200
```

### Step 2: Get Detailed Review Status via GraphQL

Split `REPO_SLUG` into its owner and repo components, then query:

```bash
OWNER="${REPO_SLUG%%/*}"
REPO="${REPO_SLUG##*/}"

gh api graphql -f query="
{
  repository(owner: \"$OWNER\", name: \"$REPO\") {
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
}"
```

### Step 3: Categorize Each PR

Classify every non-draft (ready for review) PR into one of these buckets:

| Category | Condition | Action |
|----------|-----------|--------|
| ✅ **APPROVED** | Has `APPROVED` review, 0 unresolved threads | Ready to merge — nudge the author or maintainer |
| ⏳ **CHANGES REQUESTED** | Has `CHANGES_REQUESTED` review | Waiting for author — leave as is |
| 💬 **HAS UNRESOLVED THREADS** | >0 unresolved review threads | Waiting for author — leave as is |
| 🔴 **NO REVIEWS** | Zero reviews, zero `/codereview` comments | **Trigger ReviewBot** |
| 🟡 **BOT-ONLY REVIEWED** | Only bot accounts reviewed, 0 unresolved threads, no `/codereview` comment | **Trigger ReviewBot** |
| 🟠 **REVIEWED (needs decision)** | Human reviewed, threads resolved, no approval | Needs maintainer attention |

**Bot accounts** are usernames that match common automation patterns:
`*[bot]`, `*-bot`, `copilot-*`, `dependabot`, `github-actions`.
Treat reviews only from these accounts as "bot-only reviewed".

### Step 4: Check for Existing Bot Comments

Before triggering the ReviewBot, verify no `/codereview` or
`/github-pr-review` comment already exists:

```bash
gh api "repos/$REPO_SLUG/issues/{PR_NUMBER}/comments" \
  --jq '[.[] | select(.body | test("/codereview|/github-pr-review"))] | length'
```

If the count is `> 0`, **skip** — the bot was already triggered.

### Step 5: Trigger ReviewBot

For PRs that need it (per rules above), post a comment that triggers the
review commands **and** includes a disclosure note:

```bash
gh api "repos/$REPO_SLUG/issues/{PR_NUMBER}/comments" \
  -f body="@OpenHands /codereview /github-pr-review

---
_🤖 This comment was automatically posted by the **pr-triage** skill
([OpenHands](https://github.com/All-Hands-AI/OpenHands)) on behalf of
a maintainer._"
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
📊 PR Triage Report for octocat/hello-world — 2026-04-21
══════════════════════════════════════════════════════════

🚀 Ready to Merge (3):
  #42 ✅ fix: correct typo in README (alice)
  #38 ✅ feat: add dark mode support (bob)
  ...

🔴 ReviewBot Triggered (5):
  #55 → feat: add search endpoint (carol)
  #51 → fix: handle null pointer in parser (dave)
  ...

⏳ Waiting for Author (8):
  #47 CHANGES_REQUESTED: refactor auth module (eve)
  #44 💬 2 unresolved: add caching layer (frank)
  ...

🗑️ Close Candidates (4 ancient drafts):
  #12 (180d) POC: new layout engine (grace)
  #8  (210d) experiment: wasm support (heidi)
  ...
```
