"""Fork a conversation that was built with a hook and carries a confirmation
policy, a security analyzer with its own model key, and secrets, all set after
construction, and report what the fork has: in memory, in its
`base_state.json`, and after it is loaded again from disk. No model is called.

Usage: fork_state.py
"""

import json
from pathlib import Path

from pydantic import SecretStr
from sdk_verify import emit, make_llm, run_paths

from openhands.sdk import LLM, Agent, LocalConversation
from openhands.sdk.hooks import HookConfig, HookDefinition, HookMatcher
from openhands.sdk.security.confirmation_policy import ConfirmRisky, NeverConfirm
from openhands.sdk.security.risk import SecurityRisk
from openhands.sdk.security.toolshield_llm_analyzer import (
    ToolShieldLLMSecurityAnalyzer,
)
from openhands.sdk.utils.cipher import Cipher


paths = run_paths()
BASE = paths["run"] / "fork-state"
WORKSPACE = str(paths["workspace"])
VALUES = ("start-secret-value-1", "guard-key-value-2", "runtime-secret-value-3")
HOOK_REASON = "blocked by the source hook"
cipher = Cipher("sdk-verify-fork-state-key")


def agent() -> Agent:
    return Agent(llm=make_llm([{"finish": "unused"}]), tools=[])


def secret_values(conversation) -> dict[str, str | None]:
    registry = conversation.state.secret_registry
    return {name: registry.get_secret_value(name) for name in registry.secret_sources}


def guard_key(conversation) -> str | None:
    analyzer = conversation.state.security_analyzer
    if not isinstance(analyzer, ToolShieldLLMSecurityAnalyzer):
        return None
    key = analyzer.llm.api_key
    return key.get_secret_value() if isinstance(key, SecretStr) else None


source = LocalConversation(
    agent=agent(),
    workspace=WORKSPACE,
    persistence_dir=str(BASE),
    cipher=cipher,
    secrets={"START_TOKEN": VALUES[0]},
    hook_config=HookConfig(
        user_prompt_submit=[
            HookMatcher(
                hooks=[HookDefinition(command=f"echo '{HOOK_REASON}' >&2; exit 2")]
            )
        ]
    ),
    visualizer=None,
)
source.set_confirmation_policy(
    ConfirmRisky(threshold=SecurityRisk.MEDIUM, confirm_unknown=False)
)
source.set_security_analyzer(
    ToolShieldLLMSecurityAnalyzer(
        llm=LLM(model="openai/guard", api_key=SecretStr(VALUES[1]), usage_id="guard")
    )
)
source.update_secrets({"RUNTIME_TOKEN": VALUES[2]})

fork = source.fork()
fork_dir = Path(fork.state.persistence_dir or "")
saved = json.loads((fork_dir / "base_state.json").read_text())
saved_text = "".join(p.read_text() for p in fork_dir.rglob("*.json"))
out: dict = {
    "ok": True,
    "fork_policy": fork.state.confirmation_policy.model_dump(mode="json"),
    "fork_analyzer": type(fork.state.security_analyzer).__name__,
    "fork_analyzer_is_own_object": fork.state.security_analyzer
    is not source.state.security_analyzer,
    "fork_guard_key_kept": guard_key(fork) == VALUES[1],
    "fork_secrets": secret_values(fork),
    "saved_policy": saved.get("confirmation_policy"),
    "saved_analyzer": (saved.get("security_analyzer") or {}).get("kind"),
    "saved_secret_names": sorted(saved["secret_registry"]["secret_sources"]),
    "saved_guard_key_is_placeholder": set(
        ((saved.get("security_analyzer") or {}).get("llm") or {}).get("api_key") or "*"
    )
    == {"*"},
    "plaintext_on_disk": any(value in saved_text for value in VALUES),
}

# A prompt sent to the fork goes through the hook the source was built with.
fork.send_message("hello")
out["fork_prompt_block"] = next(iter(fork.state.blocked_messages.values()), None)

# Changing the fork does not reach back into the source.
fork.set_confirmation_policy(NeverConfirm())
fork.update_secrets({"FORK_ONLY": "fork-only-value-4"})
out["source_policy_after_fork_change"] = type(source.state.confirmation_policy).__name__
out["source_has_fork_only_secret"] = (
    "FORK_ONLY" in source.state.secret_registry.secret_sources
)

fork_id = fork.id
fork.close()
source.close()
reloaded = LocalConversation(
    agent=agent(),
    workspace=WORKSPACE,
    persistence_dir=str(BASE),
    conversation_id=fork_id,
    cipher=cipher,
    visualizer=None,
)
out |= {
    "reloaded_policy": type(reloaded.state.confirmation_policy).__name__,
    "reloaded_guard_key_kept": guard_key(reloaded) == VALUES[1],
    "reloaded_secrets": secret_values(reloaded),
}
reloaded.close()
emit(out)
