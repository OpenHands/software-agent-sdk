# Independently reviewed observations

The issue JSON files retain the source run IDs, checkout SHA, selected exchanges, and SHA-256 provenance. These observations were checked against the original artifacts; their status is not inferred only from map summaries.

| Finding | Reviewed control | Reviewed failure |
|---|---|---|
| [Issue 39](https://github.com/enyst/agent-sdk/issues/39) | Explicit `repo_path: "skills/github"` returns 200 with `name: "github"`; GET is 200, DELETE is 200, subsequent GET is 404. | The same public repository with embedded `/skills/github` returns 422: `Invalid skill. Ensure the source contains a valid SKILL.md.` Both fresh attempts agree. |
| [Issue 40](https://github.com/enyst/agent-sdk/issues/40) | Ordinary transient HTTP 429 gives `{"valid":true,"error":null}`; the profile list equals its pre-validation value. | The local HTTP provider recorded 429 with both `error.type` and `error.code` equal to `insufficient_quota`; the live validation endpoint still returns `{"valid":true,"error":null}`. Both fresh attempts agree. |
| [Issue 41](https://github.com/enyst/agent-sdk/issues/41) | Live OpenHands profile and materialization responses each contain `public_skills: null`. The generated-contract key shapes compile. | Exported `OpenHandsAgentProfile` and `AgentProfileDiagnostics` each reject the same key with TS2353. Independently rerunning both captured compiler fixtures gives generated-contract exit 0 and exported-types exit 2 in each attempt. |
| [Issue 42](https://github.com/enyst/agent-sdk/issues/42) | Explicit `isolation: "shared"` returns 201 and that child reaches `finished`. The same fixture has `.git` but no HEAD commit, independently confirmed with read-only `rev-parse --verify HEAD` (exit 128). | Omitting isolation returns 500, with `Git command failed: git worktree add ... HEAD`. Both fresh attempts agree. |

The TypeScript diagnostic is:

```text
error TS2353: Object literal may only specify known properties, and '"public_skills"' does not exist in type 'Shape<OpenHandsAgentProfile>'.
error TS2353: Object literal may only specify known properties, and '"public_skills"' does not exist in type 'Shape<AgentProfileDiagnostics>'.
```

The two retired markers were also checked directly:

- `F10.fork-tags-consistent`: each BASE run observes GET `tags.qa` absent (`null`) but the persisted-tag assertion returns `src`; each TARGET run observes `null` on both sides. Source tags remain `src`, so the control shows the intended setup was present. Both BASE attempts xfail and both TARGET attempts xpass before removal.
- `F14.trajectory-init-path`: deferred initialization reaches `ready` and the newly created conversation is readable in each attempt. Each BASE global trajectory request returns 404 and stops the recipe before its scoped request. Each TARGET run returns 200 on both global and scoped trajectory requests. Thus no scoped BASE request is claimed.

Only selected public fields, assertion outcomes and compiler diagnostics are published. Raw payloads, private directories, key material, complete server logs and raw credential exchanges are excluded. The separate scan records exact known-secret comparisons and format checks; it is an additional check, not a guarantee about every unknown secret.
