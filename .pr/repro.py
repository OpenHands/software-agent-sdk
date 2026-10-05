"""Repro for OpenHands/software-agent-sdk#5495.

Shows condenser_kind in the agent settings schema.
"""

import re

from openhands.sdk.settings import OpenHandsAgentSettings
from openhands.sdk.settings.model import (
    LLMSummarizingCondenserSettings,
    NoOpCondenserSettings,
    export_agent_settings_schema,
)


schema = export_agent_settings_schema()
condenser = next(s for s in schema.sections if s.key == "condenser")
print("condenser section fields:")
for f in condenser.fields:
    choices = [c.value for c in f.choices] if f.choices else None
    print(f"  {f.key:48s} label={f.label!r} choices={choices}")

kind = next((f for f in condenser.fields if f.key == "condenser.condenser_kind"), None)
print()
print("condenser.condenser_kind exported:", kind is not None)
if kind is not None:
    print("  description:", kind.description)
rst = [
    f.key
    for f in condenser.fields
    if f.description
    and (re.search(r"``", f.description) or "iscriminator" in f.description)
]
print("condenser fields with RST markup or 'discriminator' in description:", rst)

print()
print("discriminator contract:")
legacy = OpenHandsAgentSettings.model_validate(
    {"condenser": {"enabled": True, "max_size": 100}}
)
print(
    "  payload without condenser_kind ->",
    type(legacy.condenser).__name__,
    legacy.condenser.condenser_kind,
)
noop = OpenHandsAgentSettings.model_validate(
    {"condenser": {"enabled": True, "condenser_kind": "no_op"}}
)
print(
    "  payload with condenser_kind=no_op ->",
    type(noop.condenser).__name__,
    noop.condenser.model_dump(),
)
print(
    "  model_dump keeps condenser_kind:",
    "condenser_kind" in LLMSummarizingCondenserSettings().model_dump(),
    "condenser_kind" in NoOpCondenserSettings().model_dump(),
)
