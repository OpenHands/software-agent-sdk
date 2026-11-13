"""The hook configuration a program writes or loads, and how the SDK reads a
command hook's answer, through the public classes (HookConfig, HookMatcher,
HookDefinition, HookManager) without a conversation, as example 57 does.
"""

import json

from pydantic import ValidationError
from sdk_verify import emit, run_paths

from openhands.sdk.hooks import (
    HookConfig,
    HookDefinition,
    HookManager,
    HookMatcher,
    HookType,
)


workspace = run_paths()["workspace"]
home_openhands = run_paths()["openhands"]


def rejects(build) -> bool:
    try:
        build()
        return False
    except (ValidationError, ValueError):
        return True


cmd = [{"hooks": [{"command": "true"}]}]
typed = HookConfig(
    pre_tool_use=[
        HookMatcher(matcher="terminal", hooks=[HookDefinition(command="true")])
    ]
)
pascal = HookConfig.from_dict({"PreToolUse": cmd})
legacy = HookConfig.from_dict({"hooks": {"PreToolUse": cmd, "Stop": cmd}})
async_def = HookDefinition.model_validate({"command": "x", "async": True})
roundtrip = HookConfig.model_validate_json(
    json.dumps({"post_tool_use": [{"hooks": [{"command": "x", "async": True}]}]})
)
merged = HookConfig.merge([typed, legacy])

# Discovery: project file, then the user file (~/.openhands = OH_PERSISTENCE_DIR).
missing = HookConfig.load(working_dir=workspace / "nowhere")
user_file = home_openhands / "hooks.json"
user_file.write_text(json.dumps({"Stop": cmd}))
from_user = HookConfig.load(working_dir=workspace / "nowhere")
project = workspace / "proj" / ".openhands"
project.mkdir(parents=True, exist_ok=True)
(project / "hooks.json").write_text(json.dumps({"SessionStart": cmd}))
from_project = HookConfig.load(working_dir=workspace / "proj")
explicit = HookConfig.load(path=project / "hooks.json")
user_file.unlink()


def matches(matcher: str, tool: str | None) -> bool:
    return HookMatcher(matcher=matcher).matches(tool)


# How a command hook's answer is read: run each through the PreToolUse path.
def verdict(command: str) -> dict:
    manager = HookManager(
        config=HookConfig(
            pre_tool_use=[HookMatcher(hooks=[HookDefinition(command=command)])]
        ),
        working_dir=str(workspace),
        session_id="s1",
    )
    go_on, results = manager.run_pre_tool_use(tool_name="terminal", tool_input={})
    r = results[0]
    return {
        "continue": go_on,
        "blocked": r.blocked,
        "success": r.success,
        "exit_code": r.exit_code,
        "reason": r.reason,
        "error": r.error,
    }


chain = HookManager(
    config=HookConfig(
        pre_tool_use=[
            HookMatcher(
                hooks=[
                    HookDefinition(command="exit 2"),
                    HookDefinition(command="touch second_hook_ran"),
                ]
            )
        ]
    ),
    working_dir=str(workspace),
)
chain_continue, chain_results = chain.run_pre_tool_use(
    tool_name="terminal", tool_input={}
)
timeout_hook = HookManager(
    config=HookConfig(
        pre_tool_use=[HookMatcher(hooks=[HookDefinition(command="sleep 5", timeout=1)])]
    ),
    working_dir=str(workspace),
)
_, timeout_results = timeout_hook.run_pre_tool_use(tool_name="terminal", tool_input={})

emit(
    {
        "ok": True,
        # S10.config-format
        "typed_pre_tool_use": len(typed.pre_tool_use),
        "pascal_pre_tool_use": len(pascal.pre_tool_use),
        "legacy_events": sorted(k for k, v in legacy.model_dump().items() if v),
        "unknown_event_rejected": rejects(
            lambda: HookConfig.from_dict({"BeforeTool": cmd})
        ),
        "both_spellings_rejected": rejects(
            lambda: HookConfig.from_dict({"PreToolUse": cmd, "pre_tool_use": cmd})
        ),
        "extra_field_rejected": rejects(
            lambda: HookConfig.model_validate({"pre_tool_use": [], "colour": 1})
        ),
        "async_alias": async_def.async_,
        "async_dumps_as": sorted(
            k for k in async_def.model_dump(by_alias=True) if "async" in k
        ),
        "async_roundtrip": roundtrip.post_tool_use[0].hooks[0].async_,
        "merged_pre_tool_use": len(merged.pre_tool_use),
        "merged_stop": len(merged.stop),
        # S10.definition-validation
        "command_requires_command": rejects(
            lambda: HookDefinition(type=HookType.COMMAND)
        ),
        "prompt_requires_prompt": rejects(lambda: HookDefinition(type=HookType.PROMPT)),
        "prompt_forbids_command": rejects(
            lambda: HookDefinition(type=HookType.PROMPT, prompt="p", command="x")
        ),
        "prompt_forbids_async": rejects(
            lambda: HookDefinition.model_validate(
                {"type": "prompt", "prompt": "p", "async": True}
            )
        ),
        "agent_forbids_command": rejects(
            lambda: HookDefinition(type=HookType.AGENT, command="x")
        ),
        "agent_forbids_async": rejects(
            lambda: HookDefinition.model_validate(
                {"type": "agent", "system_prompt": "s", "async": True}
            )
        ),
        "display_prompt": HookDefinition(
            type=HookType.PROMPT, name="policy", prompt="p"
        ).display_command,
        "display_agent": HookDefinition(
            type=HookType.AGENT, name="review", system_prompt="s"
        ).display_command,
        "display_command": HookDefinition(command="./check.sh").display_command,
        # S10.config-discovery
        "missing_is_empty": missing.is_empty(),
        "user_file_found": bool(from_user.stop),
        "project_wins": bool(from_project.session_start) and not from_project.stop,
        "explicit_path": bool(explicit.session_start),
        # S10.matcher
        "match": {
            "star_any": matches("*", "terminal"),
            "empty_any": matches("", "file_editor"),
            "star_no_tool": matches("*", None),
            "exact_no_tool": matches("terminal", None),
            "exact": matches("terminal", "terminal"),
            "exact_other": matches("terminal", "terminal2"),
            "slash_regex": matches("/file_.*/", "file_editor"),
            "auto_regex": matches("terminal|grep", "grep"),
            "full_match_only": matches("term.*", "my_terminal"),
            "bad_regex_exact": matches("[oops", "[oops"),
        },
        # S10.exit-codes and S10.command-exec
        "exit0": verdict("exit 0"),
        "exit2": verdict("echo 'no way' >&2; exit 2"),
        "exit1": verdict("exit 1"),
        "json_deny": verdict("""echo '{"decision": "deny", "reason": "policy"}'"""),
        "json_continue_false": verdict("""echo '{"continue": false}'"""),
        "json_non_string_decision": verdict("""echo '{"decision": 1}'"""),
        "missing_command": verdict("/no/such/hook.sh"),
        "chain_continue": chain_continue,
        "chain_results": len(chain_results),
        "chain_second_ran": (workspace / "second_hook_ran").exists(),
        "timeout_error": timeout_results[0].error,
        "timeout_success": timeout_results[0].success,
    }
)
