# Live verification

Started the fixed agent-server with a workspace containing
`.agents/agents/qa-bad-skill.md` with a missing skill. Sent two messages with
`run: false`, then created a second conversation with `initial_message`.

Observed:

```text
message statuses 200 200
initial_message status 201
initial_message conversation status error
```

Server logs include the intended warning and then continue serving the request:

```text
Skipping invalid file-based agent 'qa-bad-skill' from
.../.agents/agents/qa-bad-skill.md: Skill 'qa-f27-missing-skill' not found
but was given to agent 'qa-bad-skill'.
POST /api/conversations/.../events HTTP/1.1 200
```

The later `error` status is from the intentionally invalid `test` LLM used for
the no-network reproduction, after the skill failure no longer aborts startup.
