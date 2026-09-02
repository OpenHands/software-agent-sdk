# Public weak-type budget

`config/public-type-budget.json` records the existing `any` and `unknown`
locations reachable from the package's public declaration entry points. It is
a ratchet, not a target.

Run the check after building:

```bash
npm run check:public-type-budget
```

The check fails when:

- a new public `any` or `unknown` location appears;
- an approved location changes or widens; or
- a weak location is tightened without removing its budget entry.

When a weak type is removed, delete its matching budget tuple in the same
change. Do not regenerate and replace the whole budget: that would approve
unrelated regressions. `node scripts/check-public-type-budget.mjs
--print-current` is an inspection aid for intentionally reviewed updates.

Every group records a category, reason, and owner. Public domain contracts,
generic transport internals, and deliberately opaque Cloud proxy payloads are
kept separate so they can be reviewed under different standards.

Generated Agent Server declarations under `dist/generated/` are excluded from
this budget. Their source OpenAPI document is governed by the Agent Server
schema-quality allowlist, and generated-file drift is checked separately by
`npm run check:agent-server-api`.
