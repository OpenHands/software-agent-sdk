# What this PR changes, in two examples

Review context for #4322. Every output below was produced by running the real
conversion path, not written by hand:

```
MCPToolDefinition.to_openai_tool()
  -> mcp/tool.py:377  _process_schema_node(inputSchema, ...)
     -> tool/schema.py  the `anyOf` filter
```

## Why the expression changed more than the title says

Fair question, and the honest answer is that the one changed line is not only
about `false`. It decides which `anyOf` members survive, and a member can be
three different kinds of thing:

| member kind    | example                        | what JSON Schema says             |
| -------------- | ------------------------------ | --------------------------------- |
| object         | `{"type": "string"}`           | a normal subschema                 |
| boolean        | `true` / `false`               | `true` accepts everything, `false` accepts nothing |
| anything else  | `5`, `"string"`, `null`, `[]`  | not a schema at all — malformed    |

The PR title mentions only the second row, but the same line is the only thing
standing between the third row and a crash. That is why the expression is wider
than the title. The two examples below are one from each row.

Note this is untrusted input: `inputSchema` comes verbatim from a third-party
MCP server (`mcp/tool.py` deep-copies it and feeds it straight in).

---

## Example 1 — a `false` branch (what the title is about)

`false` is legal JSON Schema for "this variant accepts nothing". Generators emit
it for an uninhabited variant (e.g. a TypeScript `never` arm of a union).

**The server advertises:**

```json
{
  "name": "db_query",
  "inputSchema": {
    "type": "object",
    "properties": {
      "table": {
        "anyOf": [false, {"type": "string"}],
        "description": "Table to read from."
      }
    },
    "required": ["table"]
  }
}
```

**Before — what the LLM receives:**

```json
"table": {"description": "Table to read from.", "not": {}}
```

`{"not": {}}` means "nothing is valid here". The argument is required, so the
model is asked to fill a parameter that rejects every possible value.

**After:**

```json
"table": {"description": "Table to read from.", "type": "string"}
```

`anyOf` is a disjunction, so `anyOf: [false, X]` is exactly `X` — the `false`
arm contributes nothing and `string` is the whole meaning of the schema.

---

## Example 2 — a member that is not a schema at all

The everyday version of this is a server author writing `"string"` where they
meant `{"type": "string"}`. It is malformed, but we receive it anyway.

**The server advertises:**

```json
{
  "name": "kv_set",
  "inputSchema": {
    "type": "object",
    "properties": {
      "value": {
        "anyOf": ["string", {"type": "string"}],
        "description": "Value to store."
      }
    },
    "required": ["value"]
  }
}
```

**Before — what the LLM receives:** nothing. It raises:

```
AttributeError: 'str' object has no attribute 'get'
  mcp/tool.py:377   in _get_tool_schema
  tool/schema.py    in _process_schema_node
```

The old filter kept every non-dict member and then handed `"string"` to a
function that expects a mapping. This escapes `to_openai_tool()`, i.e. while
building the tool list for the LLM — so one malformed member from one server
takes out the whole request, not just that tool.

**After:**

```json
"value": {"description": "Value to store.", "type": "string"}
```

Members that are not schemas are skipped, and the usable branch is used.

---

## The `true` case (the question on the test)

`true` accepts every instance, so `anyOf: [true, {"type": "string"}]` genuinely
accepts anything, and it converts to `{}`.

That is a real loss of type enforcement — but the schema itself is what dropped
the enforcement, not the converter. Narrowing to `string` would advertise a
constraint the server never asked for. This also matches the behavior before the
PR, so it is not a change; the test only pins it so it cannot drift silently,
which is what had happened to the `anyOf: [true, null]` case from #4185.

If you would rather prefer the concrete branch for the model's benefit, that is
a one-line change (drop `t is True or`) and the test flips with it. No strong
feelings here either — happy to go the other way.

---

## Full matrix

Produced by running all three versions of the filter through the real
`_process_schema_node`:

| `anyOf` value                     | before (main)         | this PR              |
| --------------------------------- | --------------------- | -------------------- |
| `[{"type":"string"},{"type":"null"}]` | `{"type":"string"}` | `{"type":"string"}` |
| `[false, {"type":"string"}]`      | `{"not": {}}`         | `{"type":"string"}`  |
| `[{"type":"string"}, false]`      | `{"type":"string"}`   | `{"type":"string"}`  |
| `[false]`                         | `{"not": {}}`         | `{}`                 |
| `[true, {"type":"string"}]`       | `{}`                  | `{}`                 |
| `[true, {"type":"null"}]`         | `{}`                  | `{}`                 |
| `[5, {"type":"string"}]`          | **TypeError**         | `{"type":"string"}`  |
| `["string", {"type":"string"}]`   | **AttributeError**    | `{"type":"string"}`  |
| `[null, {"type":"string"}]`       | **TypeError**         | `{"type":"string"}`  |
| `[[], {"type":"string"}]`         | **AttributeError**    | `{"type":"string"}`  |
| `[5]`                             | **TypeError**         | `{}`                 |

One row worth flagging on its own: `anyOf: [false]` goes from `{"not": {}}`
(accepts nothing) to `{}` (accepts anything). Both are useless to a model, and
the schema is degenerate either way, but it is a genuine inversion rather than a
fix, and it is the one behavior change here that nobody asked for.
