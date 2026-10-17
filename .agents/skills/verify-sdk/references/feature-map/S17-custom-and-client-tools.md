# Custom tools, client-defined tools and the tool registry

How an SDK program defines its own tools and how every tool reaches an agent. A custom tool is four Python classes: an `Action` (pydantic fields become the LLM-facing JSON schema), an `Observation` (its `to_llm_content` is what the model sees), a `ToolExecutor` (`__call__(action, conversation)`), and a `ToolDefinition` subclass whose `create(conv_state, **params)` returns one or more tool instances; a `create` that returns several tools sharing one executor is a tool set. `register_tool(name, ToolClass)` puts a resolver in a process-wide registry; the agent holds only `Tool(name=..., params=...)` specs and the conversation resolves them at start (`resolve_tool`), so specs serialize and unknown names fail. A tool's `name` defaults to its class name in snake_case without `_tool`. The registry also answers usability (`is_usable()`), builds a sealed tool catalog, and records each tool's module for remote re-import. The SDK turns a definition into OpenAI chat, Responses and MCP schemas and adds `security_risk` (for non-read-only tools) and `summary` fields. A client-defined tool needs no Python at all: `ClientToolSpec(name, description, parameters)` passed as `Conversation(..., client_tools=[...])` becomes a `ClientTool` whose executor returns an acknowledgment at once, so the program (or a frontend) handles the `ActionEvent` itself; specs are persisted and restored on resume, and a name that collides with a built-in or with a different schema is rejected.

Source: `openhands-sdk/openhands/sdk/tool/`, `openhands-sdk/openhands/sdk/agent/base.py`, `openhands-sdk/openhands/sdk/conversation/conversation.py`, `openhands-sdk/openhands/sdk/conversation/impl/local_conversation.py`

Needs: `llm` (examples only)

Examples: `examples/01_standalone_sdk/02_custom_tools.py`, `examples/01_standalone_sdk/53_client_defined_tools.py`

## Sub-features

- `S17.custom-tool-definition`: a `ToolDefinition` built from an `Action`, an `Observation` and an executor validates actions, calls the executor, checks the returned observation type, and exposes `as_executable()`; calling a tool without an executor raises. Unit: `tests/sdk/tool/test_tool_definition.py`. Example: `examples/01_standalone_sdk/02_custom_tools.py` (`GrepTool`). Real-LLM: none.
- `S17.tool-set`: one registered name whose `create()` returns several tools sharing state (`BashAndGrepToolSet` returns a terminal and a grep tool on one `TerminalExecutor`); every returned tool is added to the agent. Unit: `tests/tools/browser_use/test_browser_toolset.py::test_browser_toolset_create_tools_have_shared_executor` (the built-in instance of the pattern). Example: `examples/01_standalone_sdk/02_custom_tools.py`. Real-LLM: none.
- `S17.register-tool`: `register_tool(name, factory)` accepts a `ToolDefinition` subclass (resolved through `create(**params)`) or a ready instance with an executor (params rejected, same object returned); a bare callable is rejected, an empty name raises `ValueError`, and a duplicate name logs a warning and replaces the old entry. Unit: `tests/sdk/tool/test_registry.py` (`test_register_tool_rejects_callable_factory`, `test_register_tool_instance_rejects_params`, `test_register_tool_instance_returns_same_object`, `test_register_tool_type_uses_create_params`, `test_tool_registration_typed_inspection`). Example: `examples/01_standalone_sdk/02_custom_tools.py`. Real-LLM: none.
- `S17.resolve-tool`: `resolve_tool(Tool(name=..., params=...), conv_state)` passes `params` to `create()` with the conversation state (working dir, persistence dir), falls back to built-ins under their snake_case names, applies a `response_schema` param to a single tool, and raises `KeyError` for an unregistered name. Unit: `tests/cross/test_registry_directories.py`, `tests/sdk/tool/test_registry.py::test_builtin_resolves_under_its_snake_case_name`, `tests/sdk/tool/test_response_schema.py::test_builtin_finish_tool_spec_resolves_without_registry_entry` (the unregistered-name `KeyError` has no test). Example: none. Real-LLM: none.
- `S17.auto-naming`: `ToolDefinition.name` is derived from the class name (`FileEditorTool` to `file_editor`) unless the class sets `name`. Unit: `tests/cross/test_automatic_naming.py`. Example: `examples/01_standalone_sdk/02_custom_tools.py` (`BashAndGrepToolSet.name`). Real-LLM: none.
- `S17.auto-registration`: importing a built-in tool module (or `openhands.tools`) registers its tools, after which specs resolve. Unit: `tests/cross/test_automatic_registration.py`, `tests/tools/test_init.py`. Example: none. Real-LLM: none.
- `S17.tool-spec`: `Tool(name, params)` copies its `params` (later changes to the caller's dict do not reach it), round-trips through JSON, and rejects an empty name; it is not frozen (attributes can be reassigned). Unit: `tests/sdk/tool/test_tool.py`. Example: `examples/01_standalone_sdk/02_custom_tools.py`. Real-LLM: none.
- `S17.agent-tool-init`: the agent turns its specs into tools at conversation start, adds built-ins (`finish`, `think`) unless `include_default_tools` narrows or empties them, rejects duplicate tool names, and does not let a runtime tool replace an existing one; a custom tool may replace `finish`. Unit: `tests/sdk/agent/test_agent_tool_init.py`. Example: `examples/01_standalone_sdk/02_custom_tools.py`. Real-LLM: none.
- `S17.llm-schemas`: `to_openai_tool()`, `to_responses_tool()` (with `strict` and parameters) and `to_mcp_tool()` render the action schema without the internal `kind` field, keep required fields and annotations, and expand nested and circular `$ref`s without recursion errors. Unit: `tests/sdk/tool/test_mcp_schema.py`, `tests/sdk/tool/test_to_responses_tool.py`, `tests/sdk/tool/test_tool_definition.py` (`test_to_mcp_tool_*`, `test_mcp_tool_schema_required_fields`). Example: none. Real-LLM: none.
- `S17.injected-fields`: a `security_risk` parameter is added to tools that are not read-only (optional, ahead of content parameters, gated by the security setting) and a `summary` parameter is always added, while an MCP tool's own description is kept. Unit: `tests/sdk/tool/test_tool_definition.py` (`test_security_risk_only_added_for_non_readonly_tools`, `test_security_risk_is_optional_field_in_schema`, `test_security_risk_precedes_content_params_in_schema`), `tests/sdk/tool/test_to_responses_tool_security.py`, `tests/sdk/tool/test_to_responses_tool_summary.py`. Example: none. Real-LLM: none.
- `S17.schema-serialization`: `Action`/`Observation` subclasses are frozen and serialize as a discriminated union by `kind`, including nested polymorphic fields; tool definitions are immutable and `set_executor` returns a copy. Unit: `tests/sdk/tool/test_tool_serialization.py`, `tests/sdk/tool/test_schema_immutability.py`, `tests/sdk/tool/test_tool_immutability.py`. Example: none. Real-LLM: none.
- `S17.py-type`: `py_type()` maps JSON-schema types (string, integer, number, boolean, object, arrays, nullable unions) to Python types for dynamically built actions. Unit: `tests/sdk/tool/test_py_type.py`. Example: none. Real-LLM: none.
- `S17.output-coercion`: odd executor results (an observation with `None` content and other shapes) still become a valid tool result message. Unit: `tests/sdk/tool/test_tool_call_output_coercion.py`. Example: none. Real-LLM: none.
- `S17.annotations-and-resources`: `ToolAnnotations` (`readOnlyHint`, `destructiveHint`, `idempotentHint`, `openWorldHint`) travel with the tool, and `declared_resources(action)` returns `DeclaredResources` keys that the parallel executor locks on. Unit: `tests/sdk/tool/test_tool_definition.py::test_tool_creation_with_annotations`, `tests/sdk/agent/test_parallel_executor_locking.py`. Example: `examples/01_standalone_sdk/45_parallel_tool_execution.py`. Real-LLM: none.
- `S17.usability`: a tool class's `is_usable()` decides `is_tool_usable(name)`, `is_tool_available(name)` and `list_usable_tools()`; a checker that raises counts as unusable. Unit: `tests/sdk/tool/test_registry.py::test_register_tool_type_respects_is_usable`, `tests/sdk/tool/test_defaults.py::test_is_tool_usable_contract`. Example: none. Real-LLM: none.
- `S17.catalog`: `list_tool_catalog(check_usable=...)` lists offered tools with `user_selectable`, `usable`, `description` and `in_default_set`, offers built-ins once under their snake_case names, hides the profile router, and after `seal_tool_catalog()` ignores later registrations until `unseal_tool_catalog()`. Unit: `tests/sdk/tool/test_registry.py` (`test_catalog_*`, `test_sealed_catalog_*`, `test_unsealed_catalog_tracks_registrations_again`). Example: none. Real-LLM: none.
- `S17.module-qualnames`: `get_tool_module_qualnames()` maps each registered tool name to the module that defined it (a copy), which a remote conversation forwards so the server imports the same modules. Unit: `tests/cross/test_registry_qualnames.py`. Example: `examples/02_remote_agent_server/06_custom_tool/main.py` (server side). Real-LLM: none.
- `S17.client-tool-spec`: `ClientToolSpec(name, description, parameters, annotations)` round-trips through JSON and requires an object `parameters` schema. Unit: `tests/sdk/tool/test_client_tool.py` (`test_spec_*`). Example: `examples/01_standalone_sdk/53_client_defined_tools.py`. Real-LLM: none.
- `S17.client-tool-ack`: a `ClientTool` built from a spec gets a dynamic `ClientAction_<name>` action type, its executor returns a `ClientToolObservation` acknowledgment without doing anything, and its `ActionEvent` round-trips through JSON (including an argument named `kind`). Unit: `tests/sdk/tool/test_client_tool.py` (`test_executor_returns_acknowledgment`, `test_from_spec_*`, `test_client_tool_callable`, `test_client_tool_kind_argument_*`, `test_create_*`). Example: `examples/01_standalone_sdk/53_client_defined_tools.py`. Real-LLM: none.
- `S17.client-tool-schema`: the OpenAI schema keeps the client's constraints (enums, required), `security_risk` is injected when annotations are unset, and the MCP view uses the original schema. Unit: `tests/sdk/tool/test_client_tool.py` (`test_to_openai_tool`, `test_openai_schema_preserves_client_constraints`, `test_security_risk_injected_when_annotations_unset`, `test_mcp_tool_uses_original_schema`). Example: none. Real-LLM: none.
- `S17.client-tool-registration`: `register_client_tools(specs)` returns `Tool` specs carrying the schema in `params`, reuses the action type for an identical spec, raises `ClientToolSchemaConflictError` for the same name with a different schema, and `ClientToolRegistrationError` for duplicate names in one call or a built-in tool name. Unit: `tests/sdk/tool/test_client_tool.py` (`test_same_spec_reuses_action_type`, `test_same_name_different_schema_conflicts`, `test_register_client_tools_*`), `tests/cross/test_client_tool_registration.py`. Example: none. Real-LLM: none.
- `S17.client-tools-conversation`: `Conversation(..., client_tools=SPECS)` registers the specs and injects them into the agent; a persisted conversation resumes with its client tools without passing them again; a client tool named like a built-in the agent does not attach still resumes, one named like an attached built-in is rejected. Unit: `tests/sdk/conversation/local/test_client_tools_persistence.py`. Example: `examples/01_standalone_sdk/53_client_defined_tools.py`. Real-LLM: none.
- `S17.execute-tool`: `conversation.execute_tool(name, action)` runs a resolved tool outside the agent loop and returns its observation. Unit: `tests/sdk/tool/test_switch_llm.py`, `tests/tools/ask_oracle/test_ask_oracle.py`. Example: `examples/01_standalone_sdk/38_browser_session_recording.py`. Real-LLM: none.

## How to get to it (agent POV)

- Imports: `from openhands.sdk import Action, Observation, ToolDefinition, TextContent, ImageContent`; `from openhands.sdk.tool import Tool, ToolExecutor, ToolAnnotations, DeclaredResources, register_tool, resolve_tool, list_registered_tools, list_tool_catalog, is_tool_usable, ClientToolSpec, register_client_tools`.
- Define and register: subclass, then `register_tool(MyTool.name, MyTool)` at import time; reference it as `Agent(tools=[Tool(name=MyTool.name, params={...})])`. `params` keys are `create()` keyword arguments. `S17.custom-tool-definition`, `S17.tool-set`, `S17.register-tool`, `S17.resolve-tool`, `S17.auto-naming`, `S17.tool-spec`.
- Built-in tool control: `Agent(include_default_tools=[...])` (names of `BUILT_IN_TOOLS` classes, empty list for none). `S17.agent-tool-init`.
- Client tools: `Conversation(agent=..., workspace=..., client_tools=[ClientToolSpec(...)], callbacks=[on_event])`; in the callback, an `ActionEvent` with `event.tool_name` in your names is your cue to act. `S17.client-*`.
- Direct execution: `conversation.execute_tool("tool_name", SomeAction(...))`. `S17.execute-tool`.
- Catalog and usability: `list_tool_catalog()`, `seal_tool_catalog()` / `unseal_tool_catalog()` (in `openhands.sdk.tool.registry`), `ToolDefinition.is_usable()` classmethod. `S17.catalog`, `S17.usability`.
- Remote counterpart (owned by the agent server map): `examples/02_remote_agent_server/06_custom_tool/` (custom tool image, `tool_module_qualnames`) and `examples/02_remote_agent_server/14_client_defined_tools.py` (`client_tools` over REST).

## Driving it

Preconditions:
- `uv sync --dev` has run; `control-sdk` is on `PATH` (`export PATH="$PWD/.agents/skills/verify-sdk/scripts:$PATH"`). `sdk-map run` does both.
- The custom tools live in `programs/S17/mytools.py` and register when imported, as example 02's do:
  - `word_count`: read-only; its name comes from the class.
  - `notebook_tool_set`: a tool set returning `add_note` and `list_notes`, which share one notebook. `add_note` declares a resource key.
  - `yell`: an explicit `name`.
  - `gated` and `broken_check`: their `is_usable()` decides.
- Live bullets: `DEEPSEEK_API_KEY` exported.

- **Custom tools in a conversation (`S17.custom-tool-definition`, `S17.tool-set`, `S17.resolve-tool`, `S17.annotations-and-resources`, `S17.execute-tool`, `S17.tool-spec`).** A scripted agent with `Tool(name="word_count")` and `Tool(name="notebook_tool_set", params={"name": "conv", "delay": 1})` does the following:
  - counts a file and a missing file;
  - sends a wrong argument and an unknown tool name;
  - makes three `add_note` calls in one response (one with tags, one with a nested outline), then three `list_notes` calls in one response, with `tool_concurrency_limit=4`.

  The program then calls both tools with `conversation.execute_tool`.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S17/custom_tools --arg conversation \
    --check status eq finished \
    --check tools_map eq '["add_note", "finish", "list_notes", "think", "word_count"]' \
    --check results.0.kind eq WordCountObservation --check results.0.text eq "doc.txt has 5 words" \
    --check results.1.is_error eq true --check results.1.text contains "No such file: missing.txt" \
    --check results.2.kind eq AgentErrorEvent --check results.2.text contains "Error validating tool 'word_count'" \
    --check results.2.text contains "Field required" \
    --check results.3.kind eq AgentErrorEvent --check results.3.text contains "Tool 'no_such_tool' not found" \
    --check results.6.text contains "3 note" --check results.6.text contains "[high] second #a #b" \
    --check results.6.text contains "outline: root/1" \
    --check results.9.kind eq NotesObservation \
    --check adds_overlapped eq false --check lists_overlapped eq true \
    --check direct_kind eq NotesObservation --check direct_text contains "[high] second" \
    --check direct_count_words eq 5 \
    --check action_kinds eq '["AddNoteAction", "FinishAction", "ListNotesAction", "WordCountAction"]' \
    --check agent_tool_specs.1.params.name eq conv --check agent_tool_specs.1.params.delay eq 1
  ```
  Each `add_note` declares the key `notebook:conv`, so the three run one after another. `list_notes` declares no keys, so its three calls overlap. The agent stores only the `Tool` specs, with their `params`; the tools come from `create()` when the conversation starts.
- **The registry (`S17.register-tool`, `S17.resolve-tool`, `S17.auto-naming`, `S17.auto-registration`, `S17.tool-spec`, `S17.usability`, `S17.catalog`, `S17.module-qualnames`).** The program checks the following directly, with a conversation's state for `resolve_tool`:
  - what `register_tool` accepts;
  - how `resolve_tool` answers known, unknown, built-in, parameterized and `response_schema` specs;
  - the derived names;
  - registration on import;
  - `Tool` specs;
  - usability probes;
  - the catalog, sealed and unsealed;
  - module qualnames.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S17/custom_tools --arg registry \
    --check callable_factory contains "TypeError: register_tool" --check callable_factory contains "only accepts" \
    --check empty_name eq "ValueError: ToolDefinition name must be a non-empty string" \
    --check instance_same_object eq true --check instance_with_params contains "is a fixed instance" \
    --check duplicate_warning eq '["Duplicate tool name registered: word_count"]' \
    --check unregistered contains "ToolDefinition 'no_such_tool' is not registered" \
    --check unregistered matches "^KeyError" \
    --check builtin_finish eq '["FinishTool"]' \
    --check tool_set_names eq '["add_note", "list_notes"]' --check tool_set_shares_notebook eq true \
    --check params_reached_create eq true --check word_count_root_is_workspace eq true \
    --check create_rejects_unknown_param contains "word_count takes no params" \
    --check response_schema_fields eq '["path", "summary", "verdict"]' \
    --check response_schema_on_set contains "requires a spec that resolves to exactly one tool" \
    --check names.WordCountTool eq word_count --check names.NotebookToolSet eq notebook_tool_set \
    --check names.ShoutTool eq yell --check names.AddNoteTool eq add_note \
    --check glob_before_import eq false --check glob_after_import eq true --check glob_resolves eq '["glob"]' \
    --check spec_round_trip eq true --check spec_params_copied eq true \
    --check spec_empty_name contains "Tool name cannot be empty" \
    --check gated_usable_off eq false --check gated_listed_off eq false --check gated_usable_on eq true \
    --check broken_usable eq false --check broken_warning.0 contains "Failed to determine usability for tool 'broken_check'" \
    --check available.finish eq true --check available.gated eq false \
    --check available.gated_unchecked eq true --check available.no_such_tool eq false \
    --check catalog_word_count.user_selectable eq true --check catalog_word_count.usable eq true \
    --check catalog_word_count.in_default_set eq false --check catalog_gated_usable eq false \
    --check catalog_switch_llm_count eq 1 --check catalog_has_class_names eq false \
    --check catalog_router.user_selectable eq false --check catalog_unchecked_all_usable eq true \
    --check catalog_default_set contains terminal --check catalog_default_set contains file_editor \
    --check late_while_sealed eq false --check late_resolves_while_sealed eq '["word_count"]' \
    --check late_after_unseal eq true \
    --check qualname_word_count eq S17.mytools --check qualname_glob eq openhands.tools.glob.definition \
    --check qualnames_is_copy eq true
  ```
  A sealed catalog hides later registrations, but they still resolve.
- **What the model is sent (`S17.llm-schemas`, `S17.injected-fields`, `S17.annotations-and-resources`, `S17.custom-tool-definition`, `S17.py-type`, `S17.output-coercion`, `S17.schema-serialization`).** An agent with the custom tools and a real `LLM` pointed at a local OpenAI-compatible endpoint (`programs/S16/fake_openai.py`) runs one turn; the endpoint records the `tools` of the request. The program then renders the Responses and MCP views. It also serializes an action, maps JSON types with `py_type`, and calls tools whose executors return a dict, a list, the wrong observation type, or have no executor.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S17/custom_tools --arg schemas \
    --check sent_tools eq '["add_note", "finish", "list_notes", "think", "word_count"]' \
    --check add_note_properties eq '["security_risk", "summary", "text", "priority", "tags", "outline"]' \
    --check add_note_required eq '["text"]' --check add_note_priority_enum eq '["low", "high"]' \
    --check list_notes_properties eq '["summary", "contains"]' \
    --check word_count_properties eq '["summary", "path"]' --check word_count_required eq '["path"]' \
    --check sent_has_kind eq false --check add_note_has_ref eq false \
    --check add_note_outline_mentions_children eq true \
    --check responses_tool.type eq function --check responses_tool.strict eq false \
    --check responses_tool.properties contains security_risk \
    --check mcp_properties eq '["text", "priority", "tags", "outline"]' --check mcp_keys contains outputSchema \
    --check mcp_annotations.readOnlyHint eq false \
    --check add_note_resources eq '["notebook:schemas"]' --check list_notes_resources len-eq 0 \
    --check word_count_resources_declared eq false \
    --check action_kind eq AddNoteAction --check action_json_kind eq AddNoteAction \
    --check action_round_trip_type eq AddNoteAction --check action_round_trip_equal eq true \
    --check action_frozen contains "Instance is frozen" --check tool_frozen contains "Instance is frozen" \
    --check set_executor_returns_copy eq true \
    --check py_types.integer contains int --check py_types.array_of_int eq "list[int]" \
    --check py_types.nullable_string contains str --check py_types.mixed eq Any \
    --check py_types.object eq "dict[str, Any]" \
    --check dict_coerced_type eq WordCountObservation --check dict_coerced_text eq "dict.txt has 7 words" \
    --check list_result contains "Output must be dict or BaseModel" \
    --check no_executor_call contains "has no executor" --check no_executor_as_executable contains "has no executor"
  ```
  `security_risk` goes only to the tool that is not read-only, ahead of `summary` and the tool's own fields. The `Section` outline refers to itself; it is sent expanded one level, with no `$ref`. An executor that returns another observation class fails with `AttributeError: 'NotesObservation' object has no attribute 'pop'` (`wrong_observation_type`), not with a type error that names the expected class.
- **Validating a dumped action twice (`S17.schema-serialization`).** The program validates `action.model_dump(mode="json")` back into an `Action` and then validates the same dict again.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S17/custom_tools --arg schemas \
    --check action_round_trip_type eq AddNoteAction \
    --check validate_keeps_input_kind eq true --check validate_same_dict_again eq "no error"
  ```
  Expected: validation reads the caller's dict without changing it. Known failure (reproduced 2026-10-13, not yet filed): the discriminated-union validator (`openhands/sdk/utils/models.py`, `data.pop("kind", None)`) removes `kind` from the caller's dict. A second `Action.model_validate` of the same dict fails with "Unknown kind '' for openhands.sdk.tool.schema.Action". The same pop is behind the `'... object has no attribute 'pop'` error above.
- **Built-in tools and duplicates at agent start (`S17.agent-tool-init`).** Agents with `word_count` and default, narrowed, empty and unknown `include_default_tools`; a duplicate spec; a custom tool named `finish` with only `ThinkTool` built in; and a runtime tool that would replace it.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S17/custom_tools --arg agent_init \
    --check default eq '["finish", "think", "word_count"]' \
    --check only_finish eq '["finish", "word_count"]' --check none eq '["word_count"]' \
    --check unknown_builtin contains "Unknown built-in tool class: 'NoSuchTool'" \
    --check duplicate eq "ValueError: Duplicate tool names found: {'word_count'}" \
    --check custom_finish_action eq CustomFinishAction --check custom_finish_status eq finished \
    --check custom_finish_results.0.text contains "custom finish: built True" \
    --check runtime_replace contains "Duplicate tool names found"
  ```
  Neither error comes from `Agent(...)`: the specs are only resolved when the conversation starts the agent.
- **Client tools in a conversation, then resumed (`S17.client-tool-ack`, `S17.client-tool-schema`, `S17.client-tools-conversation`).** As in example 53, the agent has the terminal, and `Conversation(..., client_tools=SPECS, callbacks=[on_event])` adds three client tools: `show_notification` (with a `level` enum), `navigate_to` (read-only) and `set_filter` (with an argument named `kind`). The scripted agent calls each of them, and once sends a `level` outside the enum. A second program run reopens the conversation in a new process with an agent that has only the terminal and no `client_tools`.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S17/client_tools --arg conversation \
    --check status eq finished \
    --check tools_map eq '["finish", "navigate_to", "set_filter", "show_notification", "terminal", "think"]' \
    --check results.1.kind eq ClientToolObservation --check results.1.text eq "Tool call dispatched to client." \
    --check results.1.is_error eq false \
    --check intercepted len-eq 4 --check intercepted.0.action_type eq ClientAction_show_notification \
    --check intercepted.0.raw_arguments.level eq info --check intercepted.1.raw_arguments.route eq /files \
    --check intercepted.2.raw_arguments.kind eq python --check intercepted.2.args.mcp_arg_kind eq python \
    --check kind_arg_round_trip eq true \
    --check notify_properties eq '["security_risk", "summary", "message", "level"]' \
    --check notify_required eq '["message", "level"]' --check notify_level_enum eq '["info", "warning", "error"]' \
    --check navigate_properties eq '["summary", "route"]' --check mcp_schema_is_original eq true
  control-sdk program S17/client_tools --arg resume \
    --check resumed_same_id eq true \
    --check tools_before_run eq '["navigate_to", "set_filter", "show_notification", "terminal"]' \
    --check status eq finished --check last_result.tool eq navigate_to \
    --check last_result.text eq "Tool call dispatched to client." --check intercepted.0.raw_arguments.route eq /home
  ```
  The SDK never checks a client tool's arguments against its schema. `level: "critical"` is dispatched like any other call (`intercepted.3`), so the client has to validate.
- **Client tool registration (`S17.client-tool-spec`, `S17.client-tool-registration`, `S17.client-tools-conversation`).**
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S17/client_tools --arg registration \
    --check array_parameters contains "parameters must be an object JSON Schema" \
    --check spec_round_trip eq true --check default_parameters.type eq object \
    --check returned_specs.0.name eq show_notification --check returned_specs.0.params.spec.name eq show_notification \
    --check same_action_type eq true --check action_type_name eq ClientAction_show_notification \
    --check executor_reply eq "Tool call dispatched to client." \
    --check schema_conflict contains "ClientToolSchemaConflictError: Client tool 'show_notification' is already registered with a different parameters schema" \
    --check duplicate_in_call contains "ClientToolRegistrationError: Duplicate client tool name 'ping'" \
    --check registered_tool_name contains "ClientToolRegistrationError: Client tool name 'terminal' collides with an existing non-client tool" \
    --check finish_attached eq "ValueError: Duplicate tool names found: {'finish'}" \
    --check think_client_status eq finished --check think_client_results.0.kind eq ClientToolObservation
  ```
  A client `finish` next to the attached built-in is refused by the agent's duplicate-name check (`ValueError`), not with `ClientToolRegistrationError`. A client `think` works when `include_default_tools` leaves the built-in `think` out.
- **Custom tools with a real model (`S17.custom-tool-definition`, `S17.tool-set`).** Live; example 02 runs its custom `grep` and the terminal in the run's workspace.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --llm deepseek-flash --field run)
  control-sdk exec --model-env --expect-output EXAMPLE_COST -- python examples/01_standalone_sdk/02_custom_tools.py
  ```
- **Client tools with a real model (`S17.client-tools-conversation`, `S17.client-tool-ack`).** Live; the example asserts that both client tools were called.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --llm deepseek-flash --field run)
  control-sdk exec --model-env --expect-output "client tool call" -- python examples/01_standalone_sdk/53_client_defined_tools.py
  ```
  It prints `[CLIENT HANDLER] show_notification(...)` and `navigate_to(...)` lines and `=== N client tool call(s) intercepted ===`.

## Gotchas

- The registry is process-global: a test or script that registers a name changes it for every later conversation in the process, and a duplicate registration only warns (a TODO in `register_tool` says it should raise).
- Registration happens on import. A spec whose module was never imported fails at conversation start with `KeyError ... is not registered`, not at `Agent(...)` construction.
- `Tool.params` must be JSON-serializable for persisted or remote conversations; live objects (executors, clients) belong inside `create()`, as example 02 does with `TerminalExecutor`.
- Example 02's `GrepTool` gets the class-derived name `grep`, the same as the built-in grep tool; adding `Tool(name="grep")` next to `BashAndGrepToolSet` would trip the agent's duplicate-name check.
- Client tool executors only acknowledge. The SDK does not wait for the client's result; the model sees the acknowledgment text. Handle results through a follow-up message.
- A client tool whose name matches a built-in the agent attaches (for example `finish`) is rejected when the conversation starts the agent, by the duplicate-name check (`ValueError: Duplicate tool names found: {'finish'}`), not by `ClientToolRegistrationError`; that error is for names already in the tool registry (such as `terminal` once imported) and for duplicates within one `client_tools` list.
- A client tool argument named `kind` is stored as `mcp_arg_kind`, and `event.action.model_dump()` adds `kind: "ClientAction_<name>"`. A callback that forwards `model_dump()` (as example 53's does) sends the client renamed and extra keys; forward `json.loads(event.tool_call.arguments)` to pass on exactly what the model sent.
- Client tool arguments are not checked against the spec's `enum`: a value outside it is dispatched like any other, so the client must validate.
- An executor that returns the wrong observation class fails with `AttributeError: '<Class>' object has no attribute 'pop'` from the discriminated-union validator, not with an error that names the expected type.
- `Action.model_validate(d)` (and every discriminated model's validation) removes `kind` from the dict `d` it is given; copy the dict first if you need it again.
